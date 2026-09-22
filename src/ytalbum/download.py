"""Stages 6-7: execute an AlbumPlan. The plan file doubles as the progress manifest."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from yt_dlp.utils import DownloadError

from .models import AlbumPlan, PlanTrack
from .tag import tag_file
from .youtube import YouTube

log = logging.getLogger(__name__)

PLAN_FILE = ".ytalbum.json"
PARTS_DIR = ".parts"
ATTEMPTS = 2  # YouTube sporadically answers 403 for a stream URL; a fresh extraction usually works
RETRY_DELAY = 5


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


def run(
    plan: AlbumPlan,
    album_dir: Path,
    yt: YouTube,
    on_track: Callable[[PlanTrack], None] = lambda t: None,
) -> AlbumPlan:
    """Download, tag and place every track that is not done yet. Saves after each track."""
    save_plan(plan, album_dir)
    cover = _fetch_cover(plan, yt)
    parts = album_dir / PARTS_DIR

    for track in plan.tracks:
        final = album_dir / track.filename
        if track.state == "done" and final.exists():
            continue
        for attempt in range(1, ATTEMPTS + 1):
            try:
                tmp = yt.download_audio(track.video_id, parts)
                tag_file(tmp, plan, track, cover)
                os.replace(tmp, final)
                track.state, track.error = "done", None
                break
            except (DownloadError, RuntimeError, OSError) as e:
                track.state, track.error = "failed", str(e).removeprefix("ERROR: ").strip()
                log.debug("track %s attempt %d failed", track.video_id, attempt, exc_info=True)
                if attempt < ATTEMPTS:
                    time.sleep(RETRY_DELAY)
        save_plan(plan, album_dir)
        on_track(track)

    if all(t.state == "done" for t in plan.tracks) and parts.exists():
        shutil.rmtree(parts)  # only our own scratch dir, and only when nothing is left to resume
    return plan


def cover_candidates(url: str) -> list[str]:
    """Best-first URLs for a cover: YouTube video thumbnails get their max-res variant first."""
    if m := re.match(r"https://i\.ytimg\.com/vi(?:_webp)?/([\w-]{11})/", url):
        return [f"https://i.ytimg.com/vi/{m[1]}/maxresdefault.jpg", url]
    return [url]


def _fetch_cover(plan: AlbumPlan, yt: YouTube) -> bytes | None:
    if not plan.cover_url:
        return None
    for url in cover_candidates(plan.cover_url):
        try:
            return yt.fetch_bytes(url)
        except Exception as e:  # a missing cover must never stop the album
            log.debug("cover %s not available: %s", url, e)
    log.warning("could not fetch any cover for %s", plan.cover_url)
    return None
