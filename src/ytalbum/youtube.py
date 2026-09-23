"""All YouTube I/O (pipeline stages 1-2 and the download itself).

Uses the yt-dlp Python API. Everything returned is mapped into `models` right here,
so no other module ever sees a raw yt-dlp dict.

NB: never read `playlist_count` from a flat entry: it is the size of the list the
entry was found in, not the entry's own size (DESIGN.md §3.1).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled, DownloadError

from . import pot as pot_server
from .config import Config
from .models import Collection, Entry, Music, SourceRef

log = logging.getLogger(__name__)


class NotSupported(Exception):
    """The URL is valid but its kind is not handled yet."""


class NoAudioStream(Exception):
    """YouTube offers no audio-only stream for this video (old/low-quality upload, or age-gated)."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description


class Cancelled(Exception):
    """The user cancelled the job; raised only at points where stopping leaves nothing half-done."""


BOT_CHECK = "YouTube wants a sign-in (bot check): wait a while, or configure cookies"
_TRANSIENT = re.compile(
    r"not a bot|HTTP Error (403|429|5\d\d)|timed out|timeout|temporarily|Connection (reset|refused|aborted)|"
    r"Unable to download (webpage|API page)|Remote end closed|Name or service not known",
    re.I,
)


def is_bot_check(message: str) -> bool:
    return "not a bot" in message or message == BOT_CHECK


def is_transient(message: str) -> bool:
    """Failures that say nothing about the video itself and may be gone on the next run."""
    return bool(_TRANSIENT.search(message)) or message == BOT_CHECK


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
    def __init__(self, cfg: Config, cancel: threading.Event | None = None) -> None:
        self.cfg = cfg
        self.cancel = cancel
        self._pot_checked = False
        self._pot_lock = threading.Lock()
        self._last_beat = 0.0

    def _pot_home(self) -> Path | None:
        return self.cfg.resolved_pot_provider() if self.cfg.pot_mode in ("server", "script") else None

    def ensure_pot_server(self) -> None:
        """Server mode: make sure the token server runs before reading/downloading (once per instance)."""
        with self._pot_lock:
            if self._pot_checked or self.cfg.pot_mode != "server":
                return
            self._pot_checked = True
            home, node = self._pot_home(), self.cfg.resolved_node()
            if home and node:
                pot_server.ensure_server(home, node, self.cfg.pot_port, self.cfg.pot_idle)

    def check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def _progress(self, *_: Any) -> None:
        """yt-dlp progress hook: abort a running download on cancel, keep the token server alive."""
        if self.cancel is not None and self.cancel.is_set():
            raise DownloadCancelled("cancelled")
        self._beat()

    def _beat(self, *_: Any) -> None:
        """Keep the token server alive while we work (throttled)."""
        now = time.monotonic()
        if self.cfg.pot_mode == "server" and now - self._last_beat > 30:
            self._last_beat = now
            pot_server.touch_heartbeat()

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "quiet": True,
            "noprogress": True,
            "logger": _YdlLogger(),
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
            "sleep_interval_requests": 0.5,  # be gentle; YouTube answers bursts with a bot check
            "progress_hooks": [self._progress],
        }
        if self.cfg.cookies_file:
            params["cookiefile"] = str(self.cfg.cookies_file)
        elif self.cfg.cookies_from_browser:
            browser, _, profile = self.cfg.cookies_from_browser.partition(":")
            params["cookiesfrombrowser"] = (browser, profile or None, None, None)
        if runtime := self.cfg.resolved_js_runtime():
            name, path = runtime
            params["js_runtimes"] = {name: {"path": path} if path else {}}
        if pot := self._pot_home():
            # proof-of-origin tokens like a browser: unlocks streams YouTube otherwise withholds
            args: dict[str, dict[str, list[str]]] = {"youtubepot-bgutilscript": {"server_home": [str(pot)]}}
            if self.cfg.pot_mode == "server":  # the plugin prefers the server, falls back to the script
                args["youtubepot-bgutilhttp"] = {"base_url": [pot_server.base_url(self.cfg.pot_port)]}
                self._beat()
            params["extractor_args"] = args
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

    def search_albums(self, query: str, limit: int = 12) -> list[SourceRef]:
        """YouTube Music's album search, each hit resolved to its official OLAK5uy_ playlist."""
        url = f"https://music.youtube.com/search?q={urllib.parse.quote_plus(query)}#albums"
        with YoutubeDL(self._params(extract_flat="in_playlist", playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        browse_ids = [e["id"] for e in info.get("entries") or [] if str(e.get("id", "")).startswith("MPREb_")]
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.concurrency)) as pool:
            refs = list(pool.map(self._resolve_album, browse_ids))
        return [r for r in refs if r]

    def _resolve_album(self, browse_id: str) -> SourceRef | None:
        try:
            with YoutubeDL(self._params(extract_flat="in_playlist", playlistend=1)) as ydl:
                return ref_from_ytm_album(ydl.extract_info(f"https://music.youtube.com/browse/{browse_id}", download=False))
        except DownloadError as e:
            log.info("album %s: %s", browse_id, _short_error(e))
            return None

    def source_state(self, url: str) -> dict[str, Any] | None:
        """One cheap request: which videos the source lists now, and when it last changed."""
        self.check()
        with YoutubeDL(self._params(extract_flat="in_playlist")) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return None
        if info.get("_type") != "playlist":
            return {"ids": [info["id"]], "modified": info.get("upload_date")}
        return {"ids": [e["id"] for e in info.get("entries") or [] if e.get("id")], "modified": info.get("modified_date")}

    def playlist_details(self, url: str) -> dict[str, Any] | None:
        """One cheap flat request: how many entries a playlist has, plus a thumbnail."""
        self.check()
        with YoutubeDL(self._params(extract_flat="in_playlist")) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info or info.get("_type") != "playlist":
            return None
        entries = [e for e in info.get("entries") or [] if e]
        return {
            "count": len(entries),
            "thumbnail": best_thumbnail(entries[0]) if entries else best_thumbnail(info),
            "title": nfc(info.get("title")),
        }

    def search_playlists(self, query: str, limit: int = 10) -> list[SourceRef]:
        """YouTube search restricted to playlists (lyric-video albums, fan compilations)."""
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}&sp=EgIQAw%253D%253D"
        with YoutubeDL(self._params(extract_flat="in_playlist", playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        return refs_from_tab(info, "search")

    # -- stage 1+2: resolve and inspect ------------------------------------------

    def fetch(self, url: str) -> Collection:
        """Resolve a playlist or video URL into a Collection with fully inspected entries."""
        self.ensure_pot_server()
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
        blocked = threading.Event()  # after the first bot check, stop asking
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.concurrency)) as pool:
            entries = list(pool.map(lambda pos, f: self._inspect(pos, f, blocked), range(1, len(flat) + 1), flat))

        return Collection(
            source_url=url,
            source_id=info["id"],
            is_playlist=True,
            title=nfc(info.get("title")) or info["id"],
            channel=_channel(info),
            thumbnail=playlist_thumbnail(info, entries),
            fetched_at=fetched_at,
            entries=entries,
            modified=info.get("modified_date"),
        )

    def _inspect(self, position: int, flat: dict[str, Any], blocked: threading.Event | None = None) -> Entry:
        video_id = flat.get("id") or ""

        def failed(reason: str, raw: str) -> Entry:
            return Entry(
                video_id=video_id,
                position=position,
                title=nfc(flat.get("title")) or video_id,
                channel=_channel(flat),
                duration=flat.get("duration"),
                skipped=reason,
                transient=is_transient(raw),
            )

        self.check()
        if blocked is not None and blocked.is_set():
            return failed(BOT_CHECK, BOT_CHECK)
        try:
            with YoutubeDL(self._params(noplaylist=True)) as ydl:
                info = ydl.extract_info(flat.get("url") or video_id, download=False)
        except DownloadError as e:
            if blocked is not None and is_bot_check(str(e)):
                blocked.set()
            return failed(_short_error(e), str(e))
        return entry_from_info(info, position)

    # -- stage 6: download -------------------------------------------------------

    def download_audio(self, video_id: str, dest_dir: Path, choice: str = "best") -> Path:
        """Download one video's audio into dest_dir/<video_id>.<ext>, never re-encoding.

        "best": YouTube's own Opus stream (remuxed). "combined": for videos that have no
        audio-only stream — the AAC track is copied out of the video into an .m4a.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_pot_server()
        codec = "m4a" if choice == "combined" else "opus"
        params = self._params(
            format="bestaudio/best" if choice == "combined" else "bestaudio[acodec=opus]/bestaudio",
            outtmpl=str(dest_dir / "%(id)s.%(ext)s"),
            postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": codec}],
            noplaylist=True,
            overwrites=True,
        )
        try:
            with YoutubeDL(params) as ydl:
                ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        except DownloadCancelled as e:
            raise Cancelled() from e
        except DownloadError as e:
            if "Requested format is not available" in str(e):
                raise NoAudioStream(self.describe_combined(video_id)) from e
            raise
        path = dest_dir / f"{video_id}.{codec}"
        if not path.exists():
            raise RuntimeError(f"download produced no {path.name}")
        return path

    def describe_combined(self, video_id: str) -> str:
        """What YouTube does offer instead: e.g. '360p video, AAC ~96 kbps'."""
        try:
            with YoutubeDL(self._params(noplaylist=True)) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        except DownloadError:
            return "only a combined video stream"
        combined = [f for f in info.get("formats") or [] if f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")]
        if not combined:
            return "only a combined video stream"
        best = max(combined, key=lambda f: (f.get("abr") or 0, f.get("height") or 0))
        codec = str(best.get("acodec", "")).split(".")[0].replace("mp4a", "AAC") or "audio"
        rate = f" ~{round(best['abr'])} kbps" if best.get("abr") else ""
        return f"{best.get('height', '?')}p video, {codec}{rate}"

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
            artist=_channel(e) if tab == "search" else None,
            channel_url=e.get("channel_url") if tab == "search" else None,
            thumbnail=best_thumbnail(e),
        )
        for e in info.get("entries") or []
        if e.get("id") and (e.get("ie_key") == "YoutubeTab" or e.get("_type") == "playlist")
    ]


def ref_from_ytm_album(info: dict[str, Any]) -> SourceRef | None:
    """A resolved music.youtube.com/browse/MPREb_… album -> its OLAK5uy_ playlist."""
    if not str(info.get("id", "")).startswith("OLAK5uy_"):
        return None
    first = (info.get("entries") or [{}])[0]
    creators = [nfc(c) for c in first.get("creators") or []]
    return SourceRef(
        url=info.get("webpage_url") or f"https://www.youtube.com/playlist?list={info['id']}",
        source_id=info["id"],
        title=(nfc(info.get("title")) or info["id"]).removeprefix("Album - ").removeprefix("EP - ").removeprefix("Single - "),
        tab="ytmusic",
        artist=", ".join(creators) or _channel(first),
        channel_url=first.get("channel_url"),
        count=info.get("playlist_count"),
        thumbnail=album_thumbnail(info, best_thumbnail(first)),
    )


def album_thumbnail(info: dict[str, Any], fallback: str | None = None) -> str | None:
    """A playlist's own cover: the album art YouTube Music shows.

    Its `s_p/OLAK…` URLs come signed (`?sqp=`) and unsigned; only the signed ones work,
    and the unsigned one is usually the largest — so it must not simply win on size.
    """
    usable = {
        "thumbnails": [
            t for t in info.get("thumbnails") or []
            if t.get("url") and not ("/s_p/" in t["url"] and "sqp=" not in t["url"])
        ]
    }
    return best_thumbnail(usable) or fallback


def playlist_thumbnail(info: dict[str, Any], entries: list[Entry]) -> str | None:
    return album_thumbnail(info, next((e.thumbnail for e in entries if e.thumbnail), None))


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
        return BOT_CHECK
    return msg.split(". ")[0].strip().rstrip(".")
