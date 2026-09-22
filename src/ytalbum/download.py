"""Stages 6-7: execute an AlbumPlan. The plan file doubles as the progress manifest.

Running a plan is idempotent: finished tracks are only renamed/retagged when the plan
changed, missing ones are downloaded, and the plan is saved after every track.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from yt_dlp.utils import DownloadError

from .models import AlbumPlan, PlanTrack
from .plan import refresh_derived, wanted_filename, wanted_folder
from .tag import image_mime, signature, tag_file
from .youtube import YouTube

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
) -> AlbumPlan:
    """Download, tag and place every track that is not done yet; rename/retag finished ones."""
    refresh_derived(plan)
    save_plan(plan, album_dir)
    cover = _cover(plan, album_dir, yt)
    parts = album_dir / PARTS_DIR

    for track in plan.tracks:
        wanted = wanted_filename(plan, track)
        if track.state == "done" and track.filename != wanted:
            old, new = album_dir / track.filename, album_dir / wanted
            if old.exists() and not new.exists():
                old.rename(new)
                on_track(track, "renamed")
            track.filename = wanted
            save_plan(plan, album_dir)
        final = album_dir / track.filename

        if track.state == "done" and final.exists():
            if track.tagged != signature(plan, track, cover):
                track.tagged = tag_file(final, plan, track, cover)
                save_plan(plan, album_dir)
                on_track(track, "retagged")
            continue
        if not track.in_source:
            continue  # gone from the playlist before we got it

        for attempt in range(1, ATTEMPTS + 1):
            try:
                tmp = yt.download_audio(track.video_id, parts)
                track.tagged = tag_file(tmp, plan, track, cover)
                os.replace(tmp, final)
                track.state, track.error = "done", None
                break
            except (DownloadError, RuntimeError, OSError) as e:
                track.state, track.error = "failed", str(e).removeprefix("ERROR: ").strip()
                log.debug("track %s attempt %d failed", track.video_id, attempt, exc_info=True)
                if attempt < ATTEMPTS:
                    time.sleep(RETRY_DELAY)
        save_plan(plan, album_dir)
        on_track(track, "downloaded" if track.state == "done" else "failed")

    if all(t.state == "done" or not t.in_source for t in plan.tracks) and parts.exists():
        shutil.rmtree(parts)  # only our own scratch dir, and only when nothing is left to resume
    return plan


# -- cover -------------------------------------------------------------------------------


def cover_candidates(url: str) -> list[str]:
    """Best-first URLs for a cover: YouTube video thumbnails get their max-res variant first."""
    if m := re.match(r"https://i\.ytimg\.com/vi(?:_webp)?/([\w-]{11})/", url):
        return [f"https://i.ytimg.com/vi/{m[1]}/maxresdefault.jpg", url]
    return [url]


def _cover(plan: AlbumPlan, album_dir: Path, yt: YouTube) -> bytes | None:
    """The album's cover: cover.* in the folder if present (the user may replace it), else fetched."""
    for existing in sorted(album_dir.glob(f"{COVER_STEM}.*")):
        data = existing.read_bytes()
        if image_mime(data):
            return data
    if not plan.cover_url:
        return None
    for url in cover_candidates(plan.cover_url):
        try:
            data = yt.fetch_bytes(url)
        except Exception as e:  # a missing cover must never stop the album
            log.debug("cover %s not available: %s", url, e)
            continue
        if mime := image_mime(data):
            (album_dir / f"{COVER_STEM}.{mime.split('/')[1].replace('jpeg', 'jpg')}").write_bytes(data)
            return data
    log.warning("could not fetch any cover for %s", plan.cover_url)
    return None
