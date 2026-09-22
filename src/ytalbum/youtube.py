"""All YouTube I/O (pipeline stages 1-2 and the download itself).

Uses the yt-dlp Python API. Everything returned is mapped into `models` right here,
so no other module ever sees a raw yt-dlp dict.

NB: never read `playlist_count` from a flat entry: it is the size of the list the
entry was found in, not the entry's own size (DESIGN.md §3.1).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import Config
from .models import Collection, Entry, Music, SourceRef

log = logging.getLogger(__name__)


class NotSupported(Exception):
    """The URL is valid but its kind is not handled yet."""


_CHANNEL_URL = re.compile(
    r"^(?P<base>https?://(?:www\.|m\.|music\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))"
    r"(?:/(?:featured|videos|playlists|releases|streams|shorts|community|about))?/?(?:[?#].*)?$"
)


def channel_base_url(url: str) -> str | None:
    """'https://www.youtube.com/@Sabaton/playlists' -> 'https://www.youtube.com/@Sabaton'; None if not a channel."""
    if "list=" in url:
        return None
    m = _CHANNEL_URL.match(url.strip())
    return m["base"] if m else None


class _YdlLogger:
    def debug(self, msg: str) -> None:
        log.debug(msg.removeprefix("[debug] "))

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        # yt-dlp warns about things it then recovers from itself ("re-fetching using API");
        # real failures arrive as DownloadError. Visible with -v.
        log.info(msg)

    def error(self, msg: str) -> None:
        log.debug(msg)  # surfaced via the DownloadError we catch instead


class YouTube:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "quiet": True,
            "noprogress": True,
            "logger": _YdlLogger(),
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
        }
        if self.cfg.cookies_file:
            params["cookiefile"] = str(self.cfg.cookies_file)
        elif self.cfg.cookies_from_browser:
            browser, _, profile = self.cfg.cookies_from_browser.partition(":")
            params["cookiesfrombrowser"] = (browser, profile or None, None, None)
        if runtime := self.cfg.resolved_js_runtime():
            name, path = runtime
            params["js_runtimes"] = {name: {"path": path} if path else {}}
        params.update(extra)
        return params

    # -- stage 1: resolve ------------------------------------------------------------

    def list_channel(self, url: str) -> list[SourceRef]:
        """A channel's albums: its Releases tab (official albums, if any), then its Playlists tab."""
        base = channel_base_url(url)
        if not base:
            raise ValueError(f"not a channel URL: {url}")
        refs: list[SourceRef] = []
        seen: set[str] = set()
        for tab in ("releases", "playlists"):
            try:
                with YoutubeDL(self._params(extract_flat="in_playlist")) as ydl:
                    info = ydl.extract_info(f"{base}/{tab}", download=False)
            except DownloadError as e:
                log.info("%s/%s: %s", base, tab, _short_error(e))  # e.g. "does not have a releases tab"
                continue
            for ref in refs_from_tab(info, tab):
                if ref.source_id not in seen:
                    seen.add(ref.source_id)
                    refs.append(ref)
        return refs

    # -- stage 1+2: resolve and inspect ------------------------------------------

    def fetch(self, url: str) -> Collection:
        """Resolve a playlist or video URL into a Collection with fully inspected entries."""
        with YoutubeDL(self._params(extract_flat="in_playlist")) as ydl:
            info = ydl.extract_info(url, download=False)
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

        if info.get("_type") != "playlist":
            return Collection(
                source_url=url,
                source_id=info["id"],
                is_playlist=False,
                title=nfc(info.get("title")) or info["id"],
                channel=_channel(info),
                thumbnail=best_thumbnail(info),
                fetched_at=fetched_at,
                entries=[entry_from_info(info, 1)],
            )

        if _is_channel(info):
            raise NotSupported("this is a channel, not a playlist — use list_channel()")

        flat = list(info.get("entries") or [])
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.concurrency)) as pool:
            entries = list(pool.map(self._inspect, range(1, len(flat) + 1), flat))

        return Collection(
            source_url=url,
            source_id=info["id"],
            is_playlist=True,
            title=nfc(info.get("title")) or info["id"],
            channel=_channel(info),
            thumbnail=best_thumbnail(info),
            fetched_at=fetched_at,
            entries=entries,
        )

    def _inspect(self, position: int, flat: dict[str, Any]) -> Entry:
        video_id = flat.get("id") or ""
        try:
            with YoutubeDL(self._params(noplaylist=True)) as ydl:
                info = ydl.extract_info(flat.get("url") or video_id, download=False)
        except DownloadError as e:
            return Entry(
                video_id=video_id,
                position=position,
                title=flat.get("title") or video_id,
                channel=_channel(flat),
                duration=flat.get("duration"),
                skipped=_short_error(e),
            )
        return entry_from_info(info, position)

    # -- stage 6: download -------------------------------------------------------

    def download_audio(self, video_id: str, dest_dir: Path) -> Path:
        """Download one video's audio as Opus into dest_dir/<video_id>.opus.

        Prefers YouTube's native Opus stream; ffmpeg then only remuxes (no re-encode).
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        params = self._params(
            format="bestaudio[acodec=opus]/bestaudio",
            outtmpl=str(dest_dir / "%(id)s.%(ext)s"),
            postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "opus"}],
            noplaylist=True,
            overwrites=True,
        )
        with YoutubeDL(params) as ydl:
            ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        path = dest_dir / f"{video_id}.opus"
        if not path.exists():
            raise RuntimeError(f"download produced no {path.name}")
        return path

    def fetch_bytes(self, url: str) -> bytes:
        with YoutubeDL(self._params()) as ydl:
            return ydl.urlopen(url).read()


# -- mapping helpers (pure, unit-tested) -------------------------------------------


def nfc(s: str | None) -> str | None:
    """YouTube text can be decomposed (o + U+0308 instead of ö); tags and filenames want NFC."""
    return unicodedata.normalize("NFC", s) if s else s


def entry_from_info(info: dict[str, Any], position: int) -> Entry:
    artists = [nfc(a) for a in info.get("artists") or ([info["artist"]] if info.get("artist") else [])]
    return Entry(
        video_id=info["id"],
        position=position,
        title=nfc(info.get("title")) or info["id"],
        channel=_channel(info),
        duration=info.get("duration"),
        thumbnail=best_thumbnail(info),
        chapters=[
            {"title": nfc(c.get("title")), "start": c.get("start_time"), "end": c.get("end_time")}
            for c in info.get("chapters") or []
        ],
        music=Music(
            artist=", ".join(artists) or None,
            track=nfc(info.get("track")),
            album=nfc(info.get("album")),
            year=info.get("release_year"),
        ),
    )


def refs_from_tab(info: dict[str, Any], tab: str) -> list[SourceRef]:
    return [
        SourceRef(
            url=e.get("url") or f"https://www.youtube.com/playlist?list={e['id']}",
            source_id=e["id"],
            title=nfc(e.get("title")) or e["id"],
            tab=tab,
        )
        for e in info.get("entries") or []
        if e.get("id") and (e.get("ie_key") == "YoutubeTab" or e.get("_type") == "playlist")
    ]


def best_thumbnail(info: dict[str, Any]) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return info.get("thumbnail")
    return max(thumbs, key=lambda t: (t.get("preference") or 0, (t.get("width") or 0) * (t.get("height") or 0)))["url"]


def _channel(info: dict[str, Any]) -> str | None:
    return nfc(info.get("channel") or info.get("uploader"))


def _is_channel(info: dict[str, Any]) -> bool:
    return str(info.get("id", "")).startswith("UC") or info.get("channel_id") == info.get("id")


def _short_error(e: DownloadError | str) -> str:
    """yt-dlp's multi-line advice -> one sentence, e.g. 'age-restricted: needs cookies'."""
    msg = str(e).removeprefix("ERROR: ")
    # "[youtube] abc: Video unavailable. This video is private" -> drop the extractor prefix
    if msg.startswith("[") and ": " in msg:
        msg = msg.split(": ", 1)[1]
    if "confirm your age" in msg or "age-restricted" in msg.lower():
        return "age-restricted: needs cookies (ytalbum config --cookies-from-browser/--cookies-file)"
    if "not a bot" in msg:
        return "YouTube wants a sign-in (bot check): try cookies or wait"
    return msg.split(". ")[0].strip().rstrip(".")
