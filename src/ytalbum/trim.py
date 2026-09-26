"""Cut intros and outros off a track — losslessly, and always from the untouched original.

The trim points live in the plan; the file on disk is produced from `.originals/<id>.<ext>`.
Clearing the trim restores the original byte for byte.

The original is kept in the track's **own** format. It used to be named `.opus` whatever the
track was, so a track switched to the combined stream was re-cut from the previous format's
original: ffmpeg copied Opus into a file named `.m4a` and the tagger then choked on it
(DESIGN.md §9.20). An original whose format does not match the track is never used as a
source — and never invented either: it is only replaced when the file on disk is still the
untouched download.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus

from .models import PlanTrack

log = logging.getLogger(__name__)

ORIGINALS = ".originals"
RETAKE = "switch the track's audio source to take it again"


def signature(track: PlanTrack) -> str:
    """What the file should be cut to; '' means untouched."""
    if track.trim_start is None and track.trim_end is None:
        return ""
    return f"{track.trim_start or 0:.2f}-{'' if track.trim_end is None else f'{track.trim_end:.2f}'}"


def original_path(album_dir: Path, track: PlanTrack) -> Path:
    """Where this track's untouched download is kept, in the track's own format."""
    return album_dir / ORIGINALS / f"{track.video_id}.{track.ext}"


def kept_originals(album_dir: Path, track: PlanTrack) -> list[Path]:
    """Every original kept for this track, whatever format it was taken in."""
    return sorted((album_dir / ORIGINALS).glob(f"{track.video_id}.*")) if (album_dir / ORIGINALS).is_dir() else []


def holds(path: Path, ext: str) -> bool:
    """Is this file really the format its name claims? A copy is not proof of a container."""
    try:
        MP4(path) if ext in ("m4a", "mp4") else OggOpus(path)
    except (MutagenError, OSError):
        return False
    return True


def apply(album_dir: Path, track: PlanTrack, path: Path) -> bool:
    """Bring `path` in line with the track's trim points. True if the file changed."""
    wanted = signature(track)
    if wanted == (track.trimmed or ""):
        return False
    original = original_path(album_dir, track)
    if original.exists() and not holds(original, track.ext):
        # a copy that is not what its name says — a leftover from another format
        log.warning("%s: the kept original is not %s", path.name, track.ext)
        if not track.trimmed:
            original.unlink()  # the file on disk is untouched, so a fresh one can be taken below
    if not original.exists() or not holds(original, track.ext):
        if not wanted:  # nothing to do and nothing kept
            track.trimmed = None
            return False
        if track.trimmed:
            # the file on disk is already cut, so copying it would not give an untouched
            # original — it would give a shorter one, and the next trim would cut that again
            raise RuntimeError(
                f"no untouched {track.ext} original is kept for this track and the file on disk is "
                f"already cut, so there is nothing to cut from: {RETAKE}"
            )
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original)
        for stale in kept_originals(album_dir, track):  # the previous format's copy is dead weight
            if stale != original:
                stale.unlink()

    if not wanted:  # back to the untouched original
        shutil.copy2(original, path)
        track.trimmed = None
        return True

    cut = path.with_suffix(f".trim{path.suffix}")  # the cut keeps the track's own container
    command = ["ffmpeg", "-v", "error", "-y", "-ss", f"{track.trim_start or 0:.3f}"]
    if track.trim_end is not None:
        command += ["-to", f"{track.trim_end:.3f}"]
    command += ["-i", str(original), "-c", "copy", str(cut)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        cut.unlink(missing_ok=True)
        raise RuntimeError(f"could not trim: {getattr(e, 'stderr', e)}".strip()) from e
    cut.replace(path)
    track.trimmed = wanted
    return True
