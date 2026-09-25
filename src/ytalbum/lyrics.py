"""Lyrics for a track: LRCLIB lookup, the `.lrc` sidecar, the tag copy (DESIGN.md §7).

Only this module talks to lrclib.net. Hits are cached for 30 days, misses for 7.

The sidecar next to the audio is what ytalbum knows. `tag_file` replaces every tag on every
pass, so the `LYRICS` comment is written *from* the sidecar instead of being kept — a lyric
can then never be half-lost, and players that read tags see the same text as players that
read `.lrc` files (MPD/Volumio has no lyrics tag at all).

A match is refused unless a candidate's length is within `TOLERANCE` of the file's: a cover
or a live version is exactly what a title-only match would attach.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .models import AlbumPlan, PlanTrack, Provenance
from .tag import audio_length
from .titles import key as text_key

log = logging.getLogger(__name__)

BASE = "https://lrclib.net/api"
USER_AGENT = "ytalbum/0.1 ( https://github.com/smtws/ytalbum )"
HIT_TTL = 30 * 24 * 3600
MISS_TTL = 7 * 24 * 3600
TOLERANCE = 3.0  # seconds a candidate's length may differ from ours (YouTube pads, we trim)
MAX_EXACT = 3600.0  # lrclib's /api/get answers "duration: must be between 1 and 3600"
SUFFIX = ".lrc"
# a title that says the recording has no singing: its words are the sung version's, not its
NO_VOCALS = re.compile(r"\b(instrumentals?|karaoke|backing track)\b", re.I)

# statuses kept in PlanTrack.lyrics
SYNCED, PLAIN, INSTRUMENTAL, NONE = "synced", "plain", "instrumental", "none"


class LyricsError(Exception):
    pass


@dataclass
class Lyrics:
    synced: str | None = None  # LRC, with timestamps
    plain: str | None = None
    lrclib_id: int | None = None
    instrumental: bool = False
    length: float | None = None  # seconds of the recording this came from — a second opinion
                                 # on how long the song is, kept even when it was refused

    @property
    def text(self) -> str | None:
        return self.synced or self.plain

    @property
    def status(self) -> str:
        if self.instrumental:
            return INSTRUMENTAL
        return SYNCED if self.synced else PLAIN if self.plain else NONE


class LyricsAPI(Protocol):
    def get(self, artist: str, title: str, album: str | None, length: float | None) -> Lyrics | None: ...


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ytalbum" / "lyrics.sqlite3"


class Lrclib:
    def __init__(
        self,
        cache_path: Path | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 0.5,
        retries: int = 3,
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

    def get(self, artist: str, title: str, album: str | None = None, length: float | None = None) -> Lyrics | None:
        """The lyrics of this recording, or None when nothing matches it closely enough.

        Without a length nothing is accepted: the length is the only thing that tells a
        recording apart from its covers.
        """
        if length is None:
            return None
        silent = bool(NO_VOCALS.search(title))  # "(instrumental)": the sung words are not its
        exact = None
        if album and length <= MAX_EXACT:
            # the exact endpoint wants lrclib's own album name and ±2s (measured 2026-09-25),
            # so it answers for real albums and never for our compilation names
            params = {"artist_name": artist, "track_name": title, "duration": str(round(length))}
            if found := self._request("get", {**params, "album_name": album}):
                exact = _pick([found], length, silent)
                if exact and exact.text:
                    return exact
        # An entry without words is not an answer yet: lrclib's "instrumental" is set when
        # nobody has submitted lyrics, not only when a recording has none — 16 of the 18
        # entries for "Blöde Frage, Saufgelage" are such stubs, and one of them beat the
        # synced entry of the very same length because the exact endpoint answered first.
        rows = self._request("search", {"artist_name": artist, "track_name": title}) or []
        same = [
            row
            for row in rows
            if isinstance(row.get("duration"), int | float) and _same_artist(artist, row.get("artistName") or "")
        ]
        fits = [row for row in same if abs(row["duration"] - length) <= TOLERANCE]
        if picked := _pick(fits, length, silent):
            return picked
        if exact:
            return exact  # the album's own entry, and nobody has words for this song
        # nothing close enough to be this recording — but how long lrclib thinks the song
        # is, is worth knowing: it is the second opinion on a file that carries an intro
        near = min(same, key=lambda row: abs(row["duration"] - length), default=None)
        return Lyrics(length=near["duration"]) if near else None

    # -- transport -----------------------------------------------------------------------

    def _request(self, path: str, params: dict[str, str]) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if (cached := self._cache_get(key)) is not None:
            return cached or None

        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self.client.get(f"{BASE}/{path}", params=params)
            except httpx.HTTPError as e:
                if attempt == self.retries:
                    raise LyricsError(f"{path}: {e}") from e
                time.sleep(2**attempt)
                continue
            if r.status_code in (429, 503):  # lrclib answers "server is busy" fairly often
                if attempt == self.retries:
                    raise LyricsError(f"{path}: HTTP {r.status_code} after {attempt + 1} tries")
                time.sleep(2**attempt)
                continue
            if 400 <= r.status_code < 500:
                # "TrackNotFound", or a request lrclib will never accept (a track over an hour):
                # a real answer either way, and asking again every run would only annoy it
                log.debug("%s: HTTP %s %s", path, r.status_code, r.text[:120])
                self._cache_put(key, None, MISS_TTL)
                return None
            if r.status_code >= 500:
                raise LyricsError(f"{path}: HTTP {r.status_code}")
            body = r.json()
            self._cache_put(key, body, MISS_TTL if not body else HIT_TTL)
            return body
        return None

    def _wait_turn(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def _cache_get(self, key: str) -> Any:
        if not self._db:
            return None
        row = self._db.execute("SELECT body, expires FROM cache WHERE key = ?", (key,)).fetchone()
        if row and row[1] > time.time():
            return json.loads(row[0]) or {}  # {} = "nothing there", told apart from a cache miss
        return None

    def _cache_put(self, key: str, body: Any, ttl: float) -> None:
        if self._db:
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(body), time.time() + ttl))


def _has_words(row: dict[str, Any]) -> bool:
    return bool(row.get("syncedLyrics") or row.get("plainLyrics"))


def _pick(fits: list[dict[str, Any]], length: float, silent: bool) -> Lyrics | None:
    """The best candidate of this length: words first, unless the track says it has none."""
    if silent:
        # an instrumental cut is as long as the sung one, so length cannot tell them apart;
        # the title can, and borrowing the singer's words would be plainly wrong
        quiet = [row for row in fits if not _has_words(row)]
        return _lyrics(min(quiet, key=lambda row: abs(row["duration"] - length))) if quiet else None
    if worded := [row for row in fits if _has_words(row)]:
        return _lyrics(min(worded, key=lambda row: (not row.get("syncedLyrics"), abs(row["duration"] - length))))
    return _lyrics(min(fits, key=lambda row: abs(row["duration"] - length))) if fits else None


def _lyrics(row: dict[str, Any]) -> Lyrics | None:
    found = Lyrics(
        synced=row.get("syncedLyrics") or None,
        plain=row.get("plainLyrics") or None,
        lrclib_id=row.get("id"),
        instrumental=bool(row.get("instrumental")),
        length=row.get("duration"),
    )
    return found if found.text or found.instrumental else None


def _same_artist(ours: str, theirs: str) -> bool:
    """lrclib's search is loose; a cover by somebody else must not pass as our recording."""
    a, b = text_key(ours), text_key(theirs)
    return bool(a) and bool(b) and (a in b or b in a)


# -- the sidecar file --------------------------------------------------------------------


def sidecar_path(album_dir: Path, filename: str) -> Path:
    """`01 Artist - Title.lrc` beside `01 Artist - Title.opus` — where players look for it."""
    return album_dir / (Path(filename).stem + SUFFIX)


def read_sidecar(album_dir: Path, track: PlanTrack) -> str | None:
    try:
        return sidecar_path(album_dir, track.filename).read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def write_sidecar(album_dir: Path, track: PlanTrack, text: str) -> Path:
    path = sidecar_path(album_dir, track.filename)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return path


def remove_sidecar(album_dir: Path, filename: str) -> None:
    sidecar_path(album_dir, filename).unlink(missing_ok=True)


def rename_sidecar(album_dir: Path, old: str, new: str) -> None:
    """Follow the audio file: a renamed track keeps its lyrics."""
    was, now = sidecar_path(album_dir, old), sidecar_path(album_dir, new)
    if was != now and was.exists() and not now.exists():
        was.rename(now)


# -- one track ---------------------------------------------------------------------------


def update_track(api: LyricsAPI, plan: AlbumPlan, track: PlanTrack, album_dir: Path, audio: Path) -> str | None:
    """Look the lyrics up once and write the sidecar. Returns the text to put in the tag.

    Sets `track.lyrics` so no track is looked up twice; a missing lyric is never an error
    (same rule as the cover: it must not stop an album).
    """
    if track.provenance.get("lyrics") == Provenance.USER:
        return read_sidecar(album_dir, track)
    try:
        found = api.get(track.artist, track.title, plan.album, audio_length(audio))
    except LyricsError as e:
        log.debug("no lyrics for %s - %s: %s", track.artist, track.title, e)
        return read_sidecar(album_dir, track)  # ask again next time
    status = found.status if found else NONE
    if status == NONE and NO_VOCALS.search(track.title):
        # "none" means we looked and lrclib has nothing; here we know *why* there are no words
        status = INSTRUMENTAL
    track.lyrics = status
    track.lyrics_id = found.lrclib_id if found else None
    track.lyrics_length = found.length if found else None
    if not found or not found.text:
        remove_sidecar(album_dir, track.filename)
        return None
    # no shifting: the match was gated on *this* file's length, so the timestamps of the
    # recording that matched are the timestamps of the file in front of us. A trim changes
    # that length, which is why a trim change makes the track be looked up again (download.py)
    text = found.text
    write_sidecar(album_dir, track, text)
    return text


__all__ = ["Lrclib", "Lyrics", "LyricsAPI", "LyricsError", "read_sidecar", "update_track"]
