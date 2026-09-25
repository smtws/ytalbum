"""Stages 6-7: execute an AlbumPlan. The plan file doubles as the progress manifest.

Running a plan is idempotent: finished tracks are only renamed/retagged when the plan
changed, missing ones are downloaded, and the plan is saved after every track.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from yt_dlp.utils import DownloadError

from .cover import square_if_padded
from .lyrics import LyricsAPI, read_sidecar, rename_sidecar, update_track
from .models import AlbumPlan, PlanTrack
from .plan import refresh_derived, wanted_filename, wanted_folder
from .tag import audio_length, image_mime, signature, tag_file
from .trim import apply as apply_trim
from .youtube import BOT_CHECK, NoAudioStream, YouTube, is_bot_check

log = logging.getLogger(__name__)

PLAN_FILE = ".ytalbum.json"
PARTS_DIR = ".parts"
COVER_STEM = "cover"
ATTEMPTS = 2  # YouTube sporadically answers 403 for a stream URL; a fresh extraction usually works
RETRY_DELAY = 5


# -- plan files ------------------------------------------------------------------------


def load_plan(album_dir: Path) -> AlbumPlan | None:
    path = album_dir / PLAN_FILE
    if not path.exists():
        return None
    return AlbumPlan.from_dict(json.loads(path.read_text()))


def save_plan(plan: AlbumPlan, album_dir: Path) -> Path:
    album_dir.mkdir(parents=True, exist_ok=True)
    path = album_dir / PLAN_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def iter_plans(library: Path) -> Iterator[tuple[Path, AlbumPlan]]:
    """Every album folder in the library (<library>/<artist>/<album>/.ytalbum.json)."""
    for path in sorted(library.glob(f"*/*/{PLAN_FILE}")):
        try:
            yield path.parent, AlbumPlan.from_dict(json.loads(path.read_text()))
        except (ValueError, KeyError, TypeError) as e:
            log.warning("ignoring unreadable plan %s: %s", path, e)


def find_plan(library: Path, source_id: str) -> tuple[Path, AlbumPlan] | None:
    """The album folder already made from this source, wherever the user renamed it to."""
    return next(((d, p) for d, p in iter_plans(library) if p.source_id == source_id), None)


def relocate(album_dir: Path, plan: AlbumPlan, library: Path) -> Path:
    """Move the album folder to where the (edited) plan says it belongs. Never overwrites."""
    target = library / wanted_folder(plan)
    if not album_dir.exists() or album_dir.resolve() == target.resolve():
        plan.folder = wanted_folder(plan)
        return target
    if target.exists():
        log.warning("not moving %s: %s already exists", album_dir, target)
        return album_dir
    target.parent.mkdir(parents=True, exist_ok=True)
    album_dir.rename(target)
    plan.folder = wanted_folder(plan)
    try:
        album_dir.parent.rmdir()  # the old artist folder, only if now empty
    except OSError:
        pass
    return target


# -- running a plan ----------------------------------------------------------------------


def run(
    plan: AlbumPlan,
    album_dir: Path,
    yt: YouTube,
    on_track: Callable[[PlanTrack, str], None] = lambda t, what: None,
    check: Callable[[], None] = lambda: None,
    download: bool = True,
    lyrics: LyricsAPI | None = None,
) -> AlbumPlan:
    """Download, tag and place every track that is not done yet; rename/retag finished ones.

    `check()` is called between tracks and may raise to stop (cancel); the plan is always saved.
    With a `lyrics` client, tracks that were never looked up get their `.lrc` sidecar here.
    """
    refresh_derived(plan)
    save_plan(plan, album_dir)
    cover = _cover(plan, album_dir, yt, fetch=download)
    parts = album_dir / PARTS_DIR

    for track in plan.tracks:
        check()
        wanted = wanted_filename(plan, track)
        if track.state == "done" and track.filename != wanted:
            old, new = album_dir / track.filename, album_dir / wanted
            if old.exists() and not new.exists():
                old.rename(new)
                on_track(track, "renamed")
            rename_sidecar(album_dir, track.filename, wanted)  # the lyrics follow the audio
            track.filename = wanted
            save_plan(plan, album_dir)
        final = album_dir / track.filename

        if track.state == "done" and final.exists():
            cut = False
            try:
                if cut := apply_trim(album_dir, track, final):
                    track.tagged = None  # the new file needs its tags again
                    if track.lyrics is not None and lyrics is not None:
                        track.lyrics = None  # the file is a different length: match it again
                    on_track(track, "trimmed")
            except RuntimeError as e:
                track.error = str(e)
                log.warning("%s: %s", track.filename, e)
            measured = cut or track.file_length is None  # measured once, then only when it changes
            if measured:
                track.file_length = audio_length(final)
            looked_up = lyrics is not None and track.lyrics is None
            text = update_track(lyrics, plan, track, album_dir, final) if looked_up else read_sidecar(album_dir, track)
            if track.tagged != signature(plan, track, cover, text):
                track.tagged = tag_file(final, plan, track, cover, text)
                save_plan(plan, album_dir)
                on_track(track, f"lyrics ({track.lyrics})" if looked_up and text else "retagged")
            elif looked_up or measured:
                save_plan(plan, album_dir)  # the lookup or the length we just measured
            continue
        if not track.in_source or not download:
            continue  # gone from the playlist, or we are only tidying up files

        for attempt in range(1, ATTEMPTS + 1):
            try:
                tmp = yt.download_audio(track.video_id, parts, track.audio_choice)
                text = update_track(lyrics, plan, track, album_dir, tmp) if lyrics else None
                track.file_length = audio_length(tmp)
                track.tagged = tag_file(tmp, plan, track, cover, text)
                os.replace(tmp, final)
                track.state, track.error, track.error_kind = "done", None, None
                break
            except NoAudioStream as e:
                track.state = "failed"
                track.error = f"YouTube offers no separate audio stream ({e.description})"
                track.error_kind = "no_audio_stream"
                break  # retrying changes nothing; the user picks what to do
            except (DownloadError, RuntimeError, OSError) as e:
                message = str(e).removeprefix("ERROR: ").strip()
                track.state, track.error = "failed", BOT_CHECK if is_bot_check(message) else message
                log.debug("track %s attempt %d failed", track.video_id, attempt, exc_info=True)
                if track.error == BOT_CHECK:
                    break  # retrying only makes it worse
                if attempt < ATTEMPTS:
                    time.sleep(RETRY_DELAY)
        save_plan(plan, album_dir)
        on_track(track, "downloaded" if track.state == "done" else "failed")
        if track.error == BOT_CHECK:
            log.warning("YouTube is blocking requests (bot check) - stopping this album")
            break

    if all(t.state == "done" or not t.in_source for t in plan.tracks) and parts.exists():
        shutil.rmtree(parts)  # only our own scratch dir, and only when nothing is left to resume
    return plan


# -- cover -------------------------------------------------------------------------------


def cover_candidates(url: str) -> list[str]:
    """Best-first URLs for a cover: YouTube video thumbnails get their max-res variant first."""
    if m := re.match(r"https://i\.ytimg\.com/vi(?:_webp)?/([\w-]{11})/", url):
        return [f"https://i.ytimg.com/vi/{m[1]}/maxresdefault.jpg", url]
    return [url]


def _cover(plan: AlbumPlan, album_dir: Path, yt: YouTube, fetch: bool = True) -> bytes | None:
    """The album cover, kept as cover.* in the album folder.

    A cover the user put there is always used. One we saved ourselves is replaced once a
    better source is known (e.g. the Cover Art Archive after MusicBrainz matched).
    """
    existing = next((p for p in sorted(album_dir.glob(f"{COVER_STEM}.*")) if image_mime(p.read_bytes())), None)
    if existing:
        data = existing.read_bytes()
        ours = plan.cover_fetched.get("sha1") == _sha1(data)
        if ours and (square := square_if_padded(data)):  # saved before covers were squared
            existing.unlink()
            data = _save_cover(plan, album_dir, plan.cover_fetched.get("url", ""), square)
            existing = next(album_dir.glob(f"{COVER_STEM}.*"))
        if not ours or not plan.cover_url or plan.cover_url in (plan.cover_fetched.get("url"), plan.cover_fetched.get("tried")):
            return data
        new = _download_cover(plan.cover_url, yt)
        plan.cover_fetched["tried"] = plan.cover_url
        if not new:
            return data
        existing.unlink()
        return _save_cover(plan, album_dir, *new)

    if not fetch:
        return None
    for url in filter(None, (plan.cover_url, plan.cover_fallback_url)):
        if found := _download_cover(url, yt):
            return _save_cover(plan, album_dir, *found)
    if plan.cover_url:
        log.warning("could not fetch any cover for %s", plan.cover_url)
    return None


def _download_cover(url: str, yt: YouTube) -> tuple[str, bytes] | None:
    for candidate in cover_candidates(url):
        try:
            data = yt.fetch_bytes(candidate)
        except Exception as e:  # a missing cover must never stop the album
            log.debug("cover %s not available: %s", candidate, e)
            continue
        if image_mime(data):
            return url, data
    return None


def _save_cover(plan: AlbumPlan, album_dir: Path, url: str, data: bytes) -> bytes:
    data = square_if_padded(data) or data  # pillarboxed YouTube thumbnails -> the square art
    ext = image_mime(data).split("/")[1].replace("jpeg", "jpg")
    album_dir.mkdir(parents=True, exist_ok=True)
    (album_dir / f"{COVER_STEM}.{ext}").write_bytes(data)
    plan.cover_fetched = {"url": url, "sha1": _sha1(data)}
    return data


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()
