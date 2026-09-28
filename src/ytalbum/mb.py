"""MusicBrainz client: polite (≤1 req/s, retries on 503), cached on disk (DESIGN.md §7).

Only this module talks to musicbrainz.org. Hits are cached for 30 days, empty results
for 1 hour, errors never.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from . import user_agent
from .text import key as text_key

log = logging.getLogger(__name__)

BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = user_agent()
HIT_TTL = 30 * 24 * 3600
MISS_TTL = 3600


# The site, not the web service: seeding and editing are pages a person opens, not API calls
# (§9, slice 43). `YTALBUM_MUSICBRAINZ_WEB=http://127.0.0.1:8796` points them at a local stand-in, which is
# how the seeding was verified without opening a real edit form.
WEB = (os.environ.get("YTALBUM_MUSICBRAINZ_WEB") or "https://musicbrainz.org").rstrip("/")
# what a YouTube playlist is to a release, in their vocabulary. Left for the editor to choose: the
# numeric link_type is optional in the seeding format, and guessing it wrongly would be worse than
# letting the person pick from the list that is already in front of them.
SEED_NOTE = ("Seeded by ytalbum (https://github.com/smtws/ytalbum) from a YouTube playlist. "
             "Track lengths are measured from the audio files. Please check everything before you submit.")


def seed_url() -> str:
    """Where the release editor takes a seeded form."""
    return f"{WEB}/release/add"


def recording_edit_url(mbid: str) -> str:
    """The page where a person can correct one recording, which seeding cannot do for them."""
    return f"{WEB}/recording/{mbid}/edit"


def seed_release(plan: Any, lengths: dict[str, float] | None = None) -> dict[str, str]:
    """The release editor's own form fields for this album (their documented seeding format).

    Read from <https://musicbrainz.org/doc/Development/Release_Editor_Seeding> on 2026-09-27: a
    form POST to `/release/add`, where only `name` is required and everything else is optional,
    with `_x_` standing for an index. **ytalbum submits nothing** — these fields open a form with
    the boxes already filled, and the person reviews it, logged in as themselves.

    The lengths are the ones measured from the files, because that is the only number here that
    MusicBrainz does not already have a better source for.
    """
    lengths = lengths or {}
    fields: dict[str, str] = {
        "name": plan.album or "",
        "artist_credit.names.0.name": plan.albumartist or "",
        "artist_credit.names.0.artist.name": plan.albumartist or "",
        "mediums.0.format": "Digital Media",
        "edit_note": SEED_NOTE,
    }
    # the kind of release, in their vocabulary. `single` is the only distinction this program makes
    # that they also make; anything else is an album until a person says otherwise in the form.
    fields["type"] = "Single" if plan.kind == "single" else "Album"
    if plan.year:
        fields["events.0.date.year"] = str(plan.year)
    if plan.source_url:
        fields["urls.0.url"] = plan.source_url
    done = [t for t in plan.tracks if t.state == "done"]
    for i, track in enumerate(sorted(done, key=lambda t: (t.disc, t.number))):
        fields[f"mediums.0.track.{i}.name"] = track.title
        fields[f"mediums.0.track.{i}.number"] = str(track.number)
        seconds = lengths.get(track.video_id) or track.file_length or track.duration
        if seconds:
            fields[f"mediums.0.track.{i}.length"] = str(round(seconds * 1000))
        # only where it differs from the album's: a credit repeated on every track is noise
        if track.artist and track.artist != plan.albumartist:
            fields[f"mediums.0.track.{i}.artist_credit.names.0.name"] = track.artist
            fields[f"mediums.0.track.{i}.artist_credit.names.0.artist.name"] = track.artist
    return fields


def seedable(plan: Any) -> str:
    """Empty when this album may be offered to MusicBrainz; otherwise why not (§9, slice 43)."""
    if plan.mbid:
        return "MusicBrainz already has this release"
    if plan.is_compilation:
        return "MusicBrainz wants releases that exist as releases, not compilations"
    if plan.kind == "artist_playlist":
        return "this is a playlist somebody made, not a release"
    if not any(t.state == "done" for t in plan.tracks):
        return "nothing has been downloaded yet"
    if not (plan.album or "").strip() or not (plan.albumartist or "").strip():
        return "an album needs a title and an artist before it can be offered"
    return ""


def length_disagreement(track: Any, by: float = 10.0) -> dict[str, float] | None:
    """Where the file and the recording disagree about how long the song is, by more than `by`.

    Seeding cannot fix this: the format covers releases, not recordings. What is possible is a link
    to the recording's own edit page and the two numbers, so the person can decide (§9, slice 43).
    """
    ours = track.file_length or track.duration
    if not (track.mbid and ours and track.mb_length):
        return None
    apart = abs(ours - track.mb_length)
    return {"ours": round(ours, 1), "theirs": round(track.mb_length, 1), "apart": round(apart, 1)} if apart > by else None


class MusicBrainzError(Exception):
    pass


class MusicBrainzAPI(Protocol):
    def search_recordings(self, artist: str, title: str) -> list[dict[str, Any]]: ...
    def search_releases(self, artist: str, album: str) -> list[dict[str, Any]]: ...
    def release(self, mbid: str) -> dict[str, Any] | None: ...
    def artist_albums(self, artist: str) -> list[dict[str, Any]]: ...


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ytalbum" / "musicbrainz.sqlite3"


def phrase(text: str) -> str:
    """A Lucene phrase query term: inside quotes only backslash and quote need escaping."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MusicBrainz:
    def __init__(
        self,
        cache_path: Path | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 1.1,
        retries: int = 4,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20,
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self.retries = retries
        self._lock = threading.Lock()
        self._last = 0.0
        self._db: sqlite3.Connection | None = None
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT, expires REAL)")

    # -- public API ----------------------------------------------------------------------

    def search_recordings(self, artist: str, title: str) -> list[dict[str, Any]]:
        d = self._get("recording", {"query": f"recording:{phrase(title)} AND artist:{phrase(artist)}", "limit": "10"})
        return d.get("recordings", []) if d else []

    def search_releases(self, artist: str, album: str) -> list[dict[str, Any]]:
        d = self._get("release", {"query": f"release:{phrase(album)} AND artist:{phrase(artist)}", "limit": "25"})
        return d.get("releases", []) if d else []

    def release(self, mbid: str) -> dict[str, Any] | None:
        return self._get(f"release/{mbid}", {"inc": "recordings+artist-credits+release-groups"})

    def artist_albums(self, artist: str) -> list[dict[str, Any]]:
        """Studio albums (release groups: primary type Album, no secondary type) of the best-matching artist."""
        found = self._get("artist", {"query": f"artist:{phrase(artist)}", "limit": "5"})
        match = next((a for a in (found or {}).get("artists", []) if text_key(a.get("name", "")) == text_key(artist)), None)
        if not match:
            return []
        groups = self._get("release-group", {"artist": match["id"], "type": "album", "limit": "100"})
        return [g for g in (groups or {}).get("release-groups", []) if not g.get("secondary-types")]

    # -- transport -----------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        params = {**params, "fmt": "json"}
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if (cached := self._cache_get(key)) is not None:
            return cached or None

        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self.client.get(f"{BASE}/{path}", params=params)
            except httpx.HTTPError as e:
                if attempt == self.retries:
                    raise MusicBrainzError(f"{path}: {e}") from e
                time.sleep(2**attempt)
                continue
            if r.status_code in (429, 503):  # MusicBrainz's "slow down"
                if attempt == self.retries:
                    raise MusicBrainzError(f"{path}: HTTP {r.status_code} after {attempt + 1} tries")
                time.sleep(2**attempt)
                continue
            if r.status_code == 404:
                self._cache_put(key, {}, MISS_TTL)
                return None
            if r.status_code >= 400:
                raise MusicBrainzError(f"{path}: HTTP {r.status_code}")
            body = r.json()
            empty = not any(body.get(k) for k in ("recordings", "releases", "media", "id", "artists", "release-groups"))
            self._cache_put(key, body, MISS_TTL if empty else HIT_TTL)
            return body
        return None

    def _wait_turn(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        if not self._db:
            return None
        row = self._db.execute("SELECT body, expires FROM cache WHERE key = ?", (key,)).fetchone()
        if row and row[1] > time.time():
            return json.loads(row[0])
        return None

    def _cache_put(self, key: str, body: dict[str, Any], ttl: float) -> None:
        if self._db:
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(body), time.time() + ttl))
