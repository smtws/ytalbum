"""Cut intros and outros off a track — losslessly, and always from the untouched original.

The trim points live in the plan; the file on disk is produced from `.originals/<id>.opus`.
Clearing the trim restores the original byte for byte.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .models import PlanTrack

log = logging.getLogger(__name__)

ORIGINALS = ".originals"


def signature(track: PlanTrack) -> str:
    """What the file should be cut to; '' means untouched."""
    if track.trim_start is None and track.trim_end is None:
        return ""
    return f"{track.trim_start or 0:.2f}-{'' if track.trim_end is None else f'{track.trim_end:.2f}'}"


def original_path(album_dir: Path, track: PlanTrack) -> Path:
    return album_dir / ORIGINALS / f"{track.video_id}.opus"


def apply(album_dir: Path, track: PlanTrack, path: Path) -> bool:
    """Bring `path` in line with the track's trim points. True if the file changed."""
    wanted = signature(track)
    if wanted == (track.trimmed or ""):
        return False
    original = original_path(album_dir, track)
    if not original.exists():
        if not wanted:  # nothing to do and nothing kept
            track.trimmed = None
            return False
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original)

    if not wanted:  # back to the untouched original
        shutil.copy2(original, path)
        track.trimmed = None
        return True

    cut = path.with_suffix(".trim.opus")
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
