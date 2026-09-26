"""`ytalbum serve`: a small web UI (installable as a PWA) on top of service.py.

Stdlib only. One worker thread runs jobs one after another (gentle on YouTube, and no two
jobs ever touch the library at once); the browser polls /api/state.

Safety: listens on 127.0.0.1 by default. Writes need the `X-Ytalbum` header (so other
websites cannot trigger them through the browser: that header forces a CORS preflight we
never answer) and the Host header must be ours (DNS rebinding). Files are only ever served
by album id, never by a path from the request.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import queue
import socket
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from . import config as config_mod
from .config import Config
from .download import COVER_STEM, PLAN_FILE, iter_plans
from .lyrics import read_sidecar, reconcile
from .models import AlbumPlan
from .plan import album_length_flag
from .service import Outcome, Service, _inside, channel_base_url
from .tag import image_mime
from .titles import natural_key
from .trim import original_path
from .youtube import Cancelled, YouTube

log = logging.getLogger(__name__)

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
MAX_LOG = 400
MAX_BODY = 1 << 20
# thumbnails are fetched by us, so the page never talks to Google and the CSP stays strict
THUMB_HOSTS = ("ytimg.com", "ggpht.com", "googleusercontent.com", "coverartarchive.org", "archive.org")
THUMB_CACHE = 300


# -- jobs ------------------------------------------------------------------------------------


@dataclass
class Job:
    id: int
    kind: str
    label: str
    lane: str = "write"  # "write" changes the library and runs alone; "read" runs beside it
    target: str | None = None  # the album (source_id) this job holds, where it knows it
    state: str = "queued"  # queued | running | done | failed | blocked | cancelled
    log: list[str] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    result: Any = None
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def summary(self, full: bool = False) -> dict[str, Any]:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "lane": self.lane,
             "created": self.created, "finished": self.finished}
        d["log"] = self.log if full else self.log[-3:]
        if full:
            d["result"] = self.result
        return d


class Jobs:
    # jobs that change the library run one at a time; reading jobs (search, preview,
    # channel listing) get their own lane so a search never waits for a download
    READ_ONLY = ("search", "preview", "channel")

    def __init__(self, make_service: Callable[[Job], Service]) -> None:
        self.make_service = make_service
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._queues: dict[str, queue.Queue[tuple[Job, Callable[[Service], Any]]]] = {"write": queue.Queue(), "read": queue.Queue()}
        self._lock = threading.Lock()
        for lane in self._queues:
            threading.Thread(target=self._work, args=(lane,), name=f"ytalbum-jobs-{lane}", daemon=True).start()

    def submit(self, kind: str, label: str, action: Callable[[Service], Any], target: str | None = None) -> Job:
        job = Job(next(self._ids), kind, label, lane="read" if kind in self.READ_ONLY else "write", target=target)
        with self._lock:
            self._jobs[job.id] = job
        self._queues[job.lane].put((job, action))
        return job

    def get(self, job_id: int) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, n: int = 20) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)[:n]

    def busy(self, lane: str | None = None) -> bool:
        """Something is queued or running (by default in any lane)."""
        return any(j.state in ("queued", "running") and lane in (None, j.lane) for j in self._jobs.values())

    def writing(self) -> Job | None:
        """The queued or running job that changes the library, if there is one."""
        return next((j for j in self._jobs.values() if j.lane == "write" and j.state in ("queued", "running")), None)

    def working_on(self, target: str) -> Job | None:
        """The queued or running write job that has this album in its hands, if there is one.

        Only jobs that know their album can be found this way: a `fetch` is named by its URL and
        learns the album id while it runs, so it is not one of them.
        """
        return next((j for j in self._jobs.values() if j.target == target and j.state in ("queued", "running")), None)

    def cancel(self, job_id: int) -> Job | None:
        """Queued: will never run. Running: stops at the next safe point."""
        job = self._jobs.get(job_id)
        if job and job.state in ("queued", "running"):
            job.cancel.set()
            job.log.append("cancel requested…" if job.state == "running" else "cancelled before it started")
            if job.state == "queued":
                job.state, job.finished = "cancelled", time.time()
        return job

    def _work(self, lane: str) -> None:
        while True:
            job, action = self._queues[lane].get()
            if job.cancel.is_set():
                continue  # cancelled while queued
            job.state = "running"
            try:
                result = action(self.make_service(job))
                job.result = _jsonable(result)
                outcomes = result if isinstance(result, list) else [result]
                if any(isinstance(o, Outcome) and o.blocked for o in outcomes):
                    job.state = "blocked"
                elif any(isinstance(o, Outcome) and o.status in ("failed", "incomplete") for o in outcomes):
                    job.state = "failed"
                else:
                    job.state = "done"
            except Cancelled:
                job.log.append("cancelled — everything finished so far is kept")
                job.state = "cancelled"
            except Exception as e:  # a job must never kill the worker
                log.debug("job %s failed", job.id, exc_info=True)
                job.log.append(f"error: {e}")
                job.log.append(traceback.format_exc(limit=3))
                job.state = "failed"
            finally:
                job.finished = time.time()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Outcome):
        return {"status": value.status, "message": value.message, "album_dir": str(value.album_dir or ""), "plan": value.plan.to_dict() if value.plan else None}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, AlbumPlan):
        return value.to_dict()
    return value


class Details:
    """Fills in what a listing does not tell us (track count, cover), one playlist at a time.

    Runs beside the job worker: slow on purpose, cached, and it backs off when YouTube
    starts refusing (a fast search must never cost a bot check).
    """

    WORKERS = 2  # together with PAUSE: about one request per second
    PAUSE = 1.0  # between playlists, per worker
    BACKOFF = 600.0  # after a bot check

    def __init__(self, youtube: Callable[[], Any]) -> None:
        self._youtube = youtube
        self.known: dict[str, dict[str, Any]] = {}
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        for i in range(self.WORKERS):
            threading.Thread(target=self._work, name=f"ytalbum-details-{i}", daemon=True).start()

    def want(self, refs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Queue what we do not know yet; return what we already have."""
        for ref in refs:
            source_id, url = str(ref.get("id", "")), str(ref.get("url", ""))
            if not source_id or not url.startswith("https://www.youtube.com/") or source_id in self.known:
                continue
            with self._lock:
                if source_id in self._pending:
                    continue
                self._pending.add(source_id)
            self._queue.put((source_id, url))
        return {r["id"]: self.known[r["id"]] for r in refs if r.get("id") in self.known}

    def _work(self) -> None:
        while True:
            source_id, url = self._queue.get()
            if time.monotonic() < self._blocked_until:
                self.known[source_id] = {"unknown": True}
            else:
                try:
                    self.known[source_id] = self._youtube().playlist_details(url) or {"unknown": True}
                except Exception as e:
                    self.known[source_id] = {"unknown": True}
                    if "not a bot" in str(e):
                        self._blocked_until = time.monotonic() + self.BACKOFF
                        log.info("details paused: YouTube is refusing requests")
                    else:
                        log.debug("details for %s: %s", url, e)
            with self._lock:
                self._pending.discard(source_id)
            time.sleep(self.PAUSE)


# -- the app ---------------------------------------------------------------------------------


class App:
    def __init__(self, cfg: Config, library: Path, host: str = "127.0.0.1", port: int = 8765, service_factory=None) -> None:
        self.cfg, self.library, self.host, self.port = cfg, library.expanduser(), host, port
        self.last_request = time.monotonic()
        self.http = httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": "ytalbum"})
        self._thumbs: dict[str, tuple[bytes, str]] = {}
        self._service_factory = service_factory or (lambda job: Service(cfg, self.library, log=lambda s: _append(job, s), on_track=lambda t, what: _append(job, f"{what}: {t.number:02d} {t.artist} - {t.title}"), cancel=job.cancel))
        self.jobs = Jobs(self._service_factory)
        self.details = Details(lambda: YouTube(self.cfg))
        self._track_index: dict[str, Any] = {"version": "", "albums": {}}

    # read side

    def library_version(self) -> str:
        """Changes whenever any plan file does — cheap enough to compute on every poll."""
        files = sorted(self.library.rglob(PLAN_FILE)) if self.library.exists() else []
        stamp = "".join(f"{p}:{p.stat().st_mtime_ns}" for p in files)
        return hashlib.sha1(stamp.encode()).hexdigest()[:12]

    #: one track in the index: video id, artist, title, downloaded, trim start, trim end
    TRACK_FIELDS = ("video_id", "artist", "title", "done", "trim_start", "trim_end")

    def track_index(self) -> dict[str, Any]:
        """Every track by album, as compact rows — the UI filters and plays songs with it.

        Sent once and re-fetched only when `library_version` changes, so typing costs nothing.
        Rows are lists, not objects: the field names would otherwise repeat 1300 times.
        """
        version = self.library_version()
        if self._track_index["version"] != version:
            albums = (
                {
                    plan.source_id: [
                        [t.video_id, t.artist, t.title, int(t.state == "done"), t.trim_start, t.trim_end] for t in plan.tracks
                    ]
                    for _, plan in iter_plans(self.library)
                }
                if self.library.exists()
                else {}
            )
            self._track_index = {"version": version, "fields": list(self.TRACK_FIELDS), "albums": albums}
        return self._track_index

    def albums(self) -> list[dict[str, Any]]:
        out = []
        for album_dir, plan in iter_plans(self.library) if self.library.exists() else []:
            out.append(
                {
                    "id": plan.source_id,
                    "albumartist": plan.albumartist,
                    "album": plan.album,
                    "year": plan.year,
                    "kind": plan.kind,
                    "tracks": len(plan.tracks),
                    "done": sum(t.state == "done" for t in plan.tracks),
                    "failed": sum(t.state == "failed" and t.in_source for t in plan.tracks),
                    "cover": any(album_dir.glob(f"{COVER_STEM}.*")),
                    "mb": bool(plan.mbid) or any(t.mbid for t in plan.tracks),
                    "lyrics": sum(t.lyrics in ("synced", "plain") for t in plan.tracks),
                    "length": album_length_flag(plan),  # set only when most of the album disagrees
                    "needs_choice": sum(t.error_kind == "no_audio_stream" for t in plan.tracks),
                }
            )
        # artist, then chronological, then by name. Albums with no year all tie, so compilations
        # keep their natural order (Vol. 1 … Vol. 20) until someone fills a year in.
        return sorted(
            out,
            key=lambda a: (natural_key(a["albumartist"]), a["year"] is None, a["year"] or 0, natural_key(a["album"])),
        )

    def album(self, source_id: str) -> tuple[Path, AlbumPlan] | None:
        return next(((d, p) for d, p in iter_plans(self.library) if p.source_id == source_id), None) if self.library.exists() else None

    def album_view(self, source_id: str) -> AlbumPlan | None:
        """The album as the view opens it: checked against the `.lrc` files on disk first.

        A sidecar edited or deleted outside the UI used to go unnoticed until some pass walked the
        album — the row could show ♪ for words that were gone (DESIGN.md §9.30). Opening the album
        now applies the same `reconcile` rules the passes apply, so the view tells the truth at
        once; when they found something, a write job makes it durable and brings the tags along.
        """
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        changed = [t for t in plan.tracks if t.state == "done" and reconcile(album_dir, t, album_dir / t.filename)[1]]
        if changed and not self.jobs.working_on(source_id):
            self.jobs.submit("lyrics", f"Check the lyrics of {plan.album}",
                             lambda s: s.sync_lyrics(source_id), target=source_id)
        return plan

    def cover(self, source_id: str) -> tuple[bytes, str] | None:
        found = self.album(source_id)
        if not found:
            return None
        for path in sorted(found[0].glob(f"{COVER_STEM}.*")):
            data = path.read_bytes()
            if mime := image_mime(data):
                return data, mime
        return None

    def lyrics(self, source_id: str, video_id: str) -> dict[str, Any] | None:
        """The lyrics of one track, read from the `.lrc` beside it (that file is the original)."""
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return None
        return {"status": track.lyrics, "lrclib_id": track.lyrics_id, "text": read_sidecar(album_dir, track) or "",
                "owner": track.provenance.get("lyrics"), "state": track.state}

    def audio_path(self, source_id: str, video_id: str, original: bool = False) -> Path | None:
        """The finished track's file — looked up in the plan, never taken from the request.

        `original` asks for the untouched download of a track that has been trimmed: the
        player works in the trim's coordinates (they count from the start of the video), so
        it must hear the file those numbers describe, not the one already cut to them.
        """
        found = self.album(source_id)
        if not found:
            return None
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id and t.state == "done"), None)
        if not track:
            return None
        if original and (uncut := original_path(album_dir, track)).is_file():
            return uncut
        path = _inside(album_dir, track.filename)
        return path if path and path.is_file() else None

    def thumbnail(self, url: str) -> tuple[bytes, str] | None:
        """Fetch a thumbnail for the page (allowlisted hosts only), with a small memory cache."""
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in THUMB_HOSTS):
            return None
        if hit := self._thumbs.get(url):
            return hit
        try:
            r = self.http.get(url)
        except httpx.HTTPError as e:
            log.debug("thumbnail %s: %s", url, e)
            return None
        if r.status_code != 200 or not (mime := image_mime(r.content)):
            return None
        if len(self._thumbs) >= THUMB_CACHE:
            self._thumbs.pop(next(iter(self._thumbs)))
        self._thumbs[url] = (r.content, mime)
        return self._thumbs[url]

    def settings(self) -> dict[str, Any]:
        runtime = self.cfg.resolved_js_runtime()
        pot = self.cfg.resolved_pot_provider()
        return {
            "library": str(self.library),
            "cookies_from_browser": self.cfg.cookies_from_browser,
            "cookies_file": str(self.cfg.cookies_file or ""),
            "browsers": config_mod.detect_browsers(),
            "musicbrainz": self.cfg.musicbrainz,
            "pot_mode": self.cfg.pot_mode,
            "pot_idle_minutes": round(self.cfg.pot_idle / 60),
            "concurrency": self.cfg.concurrency,
            "info": {
                "config file": str(config_mod.config_path()),
                "JavaScript runtime": " ".join(filter(None, runtime)) if runtime else "none found",
                "token generator": str(pot) if pot else "not set up (see README)",
                "audio": "Opus, the best stream YouTube offers, never re-encoded",
            },
        }

    def save_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        """Validate everything first, then apply to the running app and the config file."""
        changes: dict[str, Any] = {}
        if "cookies_from_browser" in body:
            browser = body["cookies_from_browser"] or None
            if browser not in (None, *config_mod.detect_browsers()):
                raise ValueError(f"unknown browser {browser!r}")
            changes["cookies_from_browser"] = browser
        if "musicbrainz" in body:
            changes["musicbrainz"] = bool(body["musicbrainz"])
        if "pot_mode" in body:
            if body["pot_mode"] not in ("server", "script", "off"):
                raise ValueError("token helper mode must be server, script or off")
            changes["pot_mode"] = body["pot_mode"]
        if "pot_idle_minutes" in body:
            minutes = int(body["pot_idle_minutes"])
            if not 1 <= minutes <= 120:
                raise ValueError("token server idle time must be 1–120 minutes")
            changes["pot_idle"] = minutes * 60
        if "concurrency" in body:
            n = int(body["concurrency"])
            if not 1 <= n <= 4:
                raise ValueError("parallel requests must be 1–4 (more trips YouTube's bot check)")
            changes["concurrency"] = n
        library = None
        if "library" in body and str(body["library"]).strip() != str(self.library):
            library = Path(str(body["library"]).strip()).expanduser()
            if not library.is_absolute():
                raise ValueError("the library folder must be an absolute path")
            if self.jobs.busy():
                raise ValueError("wait until the running jobs are finished before changing the library")
            library.mkdir(parents=True, exist_ok=True)  # ValueError-free: OSError surfaces as 400 below
            changes["library_root"] = str(library)

        for name, value in changes.items():
            if name != "library_root":
                setattr(self.cfg, name, value)  # job services share this Config: effective from the next job
            config_mod.save_setting(name, value)
        if library is not None:
            self.library = library
        return self.settings()

    def state(self) -> dict[str, Any]:
        return {
            "settings": self.settings(),
            "library": str(self.library),
            "albums": self.albums(),
            "jobs": [j.summary() for j in self.jobs.recent()],
            "busy": self.jobs.busy(),
            "busy_write": self.jobs.busy("write"),
            "musicbrainz": self.cfg.musicbrainz,
            "tracks_version": self.library_version(),
        }

    # write side (each returns a queued job)

    def submit(self, action: str, body: dict[str, Any]) -> Job:
        match action:
            case "open":  # a URL or an artist name, whatever the user typed
                text = str(body.get("q", "")).strip()
                if not text:
                    raise ValueError("empty input")
                if text.startswith(("http://", "https://")):
                    if channel_base_url(text):
                        return self.jobs.submit("channel", f"channel {text}", lambda s: {"groups": _groups(s.channel(text))})
                    return self.jobs.submit("preview", f"preview {text}", lambda s: s.fetch(text, dry=True))
                return self.jobs.submit("search", f"search {text}", lambda s: _search_result(s.search(text)))
            case "fetch":
                urls = [str(u) for u in body.get("urls") or [] if str(u).startswith(("http://", "https://"))]
                if not urls:
                    raise ValueError("no URLs")
                label = self.describe(urls[0]) if len(urls) == 1 else f"{len(urls)} sources"

                def fetch_all(s: Service):
                    outcomes = []
                    for i, url in enumerate(urls, 1):
                        if len(urls) > 1:
                            s.log(f"=== [{i}/{len(urls)}] {url}")
                        outcomes.append(s._guarded(lambda: s.fetch(url)))
                        if outcomes[-1].blocked:
                            s.log("stopping: YouTube is blocking requests")
                            break
                    return outcomes

                return self.jobs.submit("fetch", f"Fetch {label}" if len(urls) > 1 else f"Update {label}" if " — " in label else f"Fetch {label}", fetch_all)
            case "update":
                deep = bool(body.get("deep"))
                artist = str(body.get("artist") or "").strip() or None
                label = f"Update {artist}" if artist else "Update the library"
                return self.jobs.submit("update", label + (" (full)" if deep else ""), lambda s: s.update_all(deep=deep, artist=artist))
            case "repair":
                if running := self.jobs.writing():
                    # it renames folders all over the library, so it must not run beside a writer
                    raise ValueError(f"“{running.label}” is running — wait for it, then repair")
                return self.jobs.submit("repair", "Repair the library", lambda s: s.repair())
            case "prune":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                album_dir = found[0]
                return self.jobs.submit("prune", f"Remove gone tracks from {found[1].album}", lambda s: s.prune(album_dir), target=source_id)
            case "trim_channel":
                channel = str(body.get("channel", "")).strip()
                if not channel:
                    raise ValueError("no channel")
                from .service import parse_time

                start, end = parse_time(body.get("start")), parse_time(body.get("end"))
                if start is not None and end is not None and end <= start:
                    raise ValueError("the end must come after the start")
                return self.jobs.submit("trim", f"Trim all tracks from {channel}", lambda s: s.trim_channel(channel, start, end))
            case "delete_track":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                label = f"{track.artist} - {track.title}" if track else video_id
                return self.jobs.submit("delete", f"Delete {label}", lambda s: s.delete_track(source_id, video_id), target=source_id)
            case "delete_album":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                return self.jobs.submit("delete", f"Delete album {found[1].album}", lambda s: s.delete_album(source_id), target=source_id)
            case "lyrics":
                source_id = str(body.get("id", ""))
                found = self.album(source_id)
                if not found:
                    raise ValueError("unknown album")
                refetch = bool(body.get("refetch"))
                verb = "Look up all lyrics of" if refetch else "Fetch lyrics for"
                return self.jobs.submit("lyrics", f"{verb} {found[1].album}", lambda s: s.fetch_lyrics(refetch=refetch, source_id=source_id), target=source_id)
            case "save_lyrics":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if track.state != "done":
                    raise ValueError(f"{track.title}: there is no file yet to put lyrics beside")
                if running := self.jobs.working_on(source_id):
                    # the pass would retag from the file this save is about to write
                    raise ValueError(f"“{running.label}” is working on this album — wait for it, then save again")
                text = str(body.get("text", ""))
                what = "Clear the lyrics of" if not text.strip() else "Save your lyrics for"
                return self.jobs.submit("lyrics", f"{what} {track.title}",
                                        lambda s: s.save_lyrics(source_id, video_id, text), target=source_id)
            case "lyrics_track":
                source_id, video_id = str(body.get("id", "")), str(body.get("video_id", ""))
                found = self.album(source_id)
                if not found or not video_id:
                    raise ValueError("unknown album or track")
                track = next((t for t in found[1].tracks if t.video_id == video_id), None)
                if not track:
                    raise ValueError("no such track in this album")
                if track.state != "done":
                    raise ValueError(f"{track.title}: there is no file yet to match lyrics against")
                if track.provenance.get("lyrics") == "user":
                    raise ValueError(f"{track.title}: these lyrics are yours — delete them first")
                reject = bool(body.get("reject"))
                if reject and not track.lyrics_id:
                    raise ValueError(f"{track.title}: there is no lrclib match to reject")
                if running := self.jobs.working_on(source_id):
                    raise ValueError(f"“{running.label}” is working on this album — wait for it, then try again")
                what = "Reject the lyrics of" if reject else "Look up the lyrics of"
                return self.jobs.submit("lyrics", f"{what} {track.title}",
                                        lambda s: s.lookup_track(source_id, video_id, reject=reject), target=source_id)
            case "edit":
                source_id = str(body.get("id", ""))
                if not self.album(source_id):
                    raise ValueError("unknown album")
                edits = body.get("edits") or {}
                return self.jobs.submit("edit", f"Save {self.describe(source_id)}", lambda s: s.apply_edits(source_id, edits), target=source_id)
        raise ValueError(f"unknown action {action!r}")

    def describe(self, url: str) -> str:
        """A readable job label: the album's name if the URL is one we have, else a short URL."""
        for _, plan in iter_plans(self.library) if self.library.exists() else []:
            if plan.source_url == url or plan.source_id in url:
                return f"{plan.albumartist} — {plan.album}"
        return url.replace("https://", "").replace("www.", "")[:60]

    # server

    def touch(self) -> None:
        self.last_request = time.monotonic()

    def idle_for(self) -> float:
        """Seconds without requests and without queued/running jobs (0 while busy)."""
        return 0.0 if self.jobs.busy() else time.monotonic() - self.last_request

    def allowed_host(self, host_header: str | None) -> bool:
        if self.host in ("0.0.0.0", "::"):
            return True  # explicitly opened to the network
        name = (host_header or "").rsplit(":", 1)[0].strip("[]")
        return name in {"localhost", "127.0.0.1", "::1", self.host}

    def make_server(self, sock: socket.socket | None = None) -> ThreadingHTTPServer:
        """A server on (host, port), or on an already listening socket (systemd socket activation)."""
        app = self

        class Handler(_Handler):
            pass

        Handler.app = app
        if sock is None:
            return ThreadingHTTPServer((self.host, self.port), Handler)
        server = ThreadingHTTPServer(sock.getsockname()[:2], Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
        server.server_address = sock.getsockname()[:2]
        return server


def _asset(name: str) -> bytes:
    return resources.files("ytalbum").joinpath("webui", name).read_bytes()


def _asset_hash(name: str) -> str:
    """Changes with the file, so a new version is never served from a browser cache."""
    return hashlib.sha1(_asset(name)).hexdigest()[:10]


def _append(job: Job, line: str) -> None:
    job.log.append(line)
    if len(job.log) > MAX_LOG:
        del job.log[: len(job.log) - MAX_LOG]


def _ref(r) -> dict[str, Any]:
    return {"url": r.url, "id": r.source_id, "title": r.title, "tab": r.tab, "artist": r.artist, "count": r.count, "thumbnail": r.thumbnail}


def _groups(groups) -> list[dict[str, Any]]:
    return [{"label": label, "refs": [_ref(r) for r in refs]} for label, refs in groups]


def _search_result(result) -> dict[str, Any]:
    return {"groups": _groups(result.groups), "channel": result.channel_url, "missing": result.missing}


class _Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "ytalbum"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    # routing

    def do_GET(self) -> None:
        self.app.touch()
        if not self.app.allowed_host(self.headers.get("Host")):
            return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            body = _asset(name)
            if url.path == "/":  # never cached itself; points at content-hashed assets
                for asset in ("app.js", "style.css"):
                    body = body.replace(f'"/{asset}"'.encode(), f'"/{asset}?v={_asset_hash(asset)}"'.encode())
            return self._send(HTTPStatus.OK, body, ctype, cache=url.path != "/")
        match url.path:
            case "/api/state":
                return self._json(self.app.state())
            case "/api/tracks":
                return self._json(self.app.track_index())
            case "/api/album":
                plan = self.app.album_view(q.get("id", ""))
                return self._json(plan.to_dict()) if plan else self._error(HTTPStatus.NOT_FOUND, "no such album")
            case "/api/cover":
                cover = self.app.cover(q.get("id", ""))
                return self._send(HTTPStatus.OK, cover[0], cover[1]) if cover else self._error(HTTPStatus.NOT_FOUND, "no cover")
            case "/api/thumb":
                thumb = self.app.thumbnail(q.get("u", ""))
                return self._send(HTTPStatus.OK, thumb[0], thumb[1], cache=True) if thumb else self._error(HTTPStatus.NOT_FOUND, "no thumbnail")
            case "/api/lyrics":
                found = self.app.lyrics(q.get("id", ""), q.get("v", ""))
                return self._json(found) if found else self._error(HTTPStatus.NOT_FOUND, "no such track")
            case "/api/audio":
                path = self.app.audio_path(q.get("id", ""), q.get("v", ""), original=q.get("o") == "1")
                return self._file(path, "audio/ogg") if path else self._error(HTTPStatus.NOT_FOUND, "no such track")
            case "/api/job":
                job = self.app.jobs.get(int(q.get("id", "0") or 0)) if q.get("id", "").isdigit() else None
                return self._json(job.summary(full=True)) if job else self._error(HTTPStatus.NOT_FOUND, "no such job")
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        self.app.touch()
        if not self.app.allowed_host(self.headers.get("Host")):
            return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
        if self.headers.get("X-Ytalbum") != "1" or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._error(HTTPStatus.FORBIDDEN, "missing X-Ytalbum header or JSON content type")
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            body = body if isinstance(body, dict) else {}
            if url.path == "/api/details":
                refs = body.get("refs") or []
                if not isinstance(refs, list) or len(refs) > 200:
                    raise ValueError("refs must be a list of at most 200 entries")
                return self._json(self.app.details.want([r for r in refs if isinstance(r, dict)]))
            if url.path == "/api/cancel":
                job = self.app.jobs.cancel(int(body.get("id", 0)))
                return self._json(job.summary()) if job else self._error(HTTPStatus.NOT_FOUND, "no such job")
            if url.path == "/api/settings":
                try:
                    return self._json(self.app.save_settings(body))
                except OSError as e:
                    return self._error(HTTPStatus.BAD_REQUEST, f"cannot use that folder: {e.strerror or e}")
            job = self.app.submit(url.path.removeprefix("/api/"), body)
        except (ValueError, json.JSONDecodeError) as e:
            return self._error(HTTPStatus.BAD_REQUEST, str(e))
        return self._json({"job": job.summary()}, HTTPStatus.ACCEPTED)

    def do_OPTIONS(self) -> None:  # never answer CORS preflights: other origins must not write
        self._error(HTTPStatus.FORBIDDEN, "no cross-origin access")

    # responses

    def _file(self, path: Path, ctype: str) -> None:
        """Stream a file, honouring a single `Range: bytes=a-b` so players can seek."""
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        partial = rng.startswith("bytes=") and "," not in rng
        if partial:
            a, _, b = rng.removeprefix("bytes=").partition("-")
            try:
                if a:
                    start, end = int(a), min(int(b), size - 1) if b else size - 1
                else:  # suffix range: the last N bytes
                    start = max(size - int(b), 0)
            except ValueError:
                partial = False
            if partial and (start > end or start >= size):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0 and (chunk := f.read(min(remaining, 1 << 16))):
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player skipped or seeked away

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _send(self, status: HTTPStatus, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")  # static: always revalidate, so updates show up
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'")
        self.end_headers()
        self.wfile.write(body)


def systemd_socket() -> socket.socket | None:
    """The listening socket systemd passed us (sd_listen_fds protocol), if any."""
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or int(os.environ.get("LISTEN_FDS", "0")) < 1:
        return None
    for name in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(name, None)  # not for our children (the token server)
    return socket.socket(fileno=3)


def serve(cfg: Config, library: Path, host: str = "127.0.0.1", port: int = 8765, idle_exit: float = 0) -> None:
    """Run the web UI. With idle_exit > 0 the server stops after that many seconds without
    requests or jobs — meant for socket activation, where the next request starts it again."""
    sock = systemd_socket()
    app = App(cfg, library, host, port)
    if sock is not None:
        app.host, app.port = sock.getsockname()[:2]
    server = app.make_server(sock)
    shown = "localhost" if app.host in ("127.0.0.1", "::1") else app.host
    print(f"ytalbum: http://{shown}:{app.port}/  (library {library})" + (" [socket-activated]" if sock else " — Ctrl+C to stop"), flush=True)
    if app.host in ("0.0.0.0", "::"):
        print("warning: reachable from your network without a login — anyone there can start downloads")
    if idle_exit > 0:

        def watch() -> None:
            while True:
                time.sleep(min(30.0, idle_exit / 4))
                if app.idle_for() > idle_exit:
                    print(f"idle for {idle_exit:.0f}s, stopping", flush=True)
                    server.shutdown()
                    return

        threading.Thread(target=watch, name="ytalbum-idle", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
