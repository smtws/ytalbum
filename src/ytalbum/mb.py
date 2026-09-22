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

from .titles import key as text_key

log = logging.getLogger(__name__)

BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "ytalbum/0.1 ( https://github.com/Tordt/YT-Downloads )"
HIT_TTL = 30 * 24 * 3600
MISS_TTL = 3600


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
