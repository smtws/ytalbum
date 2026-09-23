"""Manual trim: lossless, always from the untouched original, restorable."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.oggopus import OggOpus
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run
from ytalbum.plan import build_plan
from ytalbum.service import Service, apply_user_edits, parse_time
from ytalbum.trim import ORIGINALS, apply, signature


@pytest.fixture
def tone(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "tone.opus"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=10", "-c:a", "libopus", str(path)], check=True)
    return path


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


@pytest.mark.parametrize(("text", "seconds"), [("8", 8), ("0:08", 8), ("1:02.5", 62.5), ("", None), (None, None), ("1:00:00", 3600)])
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["abc", "1:", "-5", "1:2:3:4"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_cut_front_and_back_losslessly_then_restore(tmp_path, tone):
    plan = build_plan(vol1())
    track = plan.tracks[0]
    album_dir = tmp_path
    shutil.copy(tone, album_dir / "t.opus")
    before = (album_dir / "t.opus").read_bytes()

    track.trim_start, track.trim_end = 2.0, 7.0
    assert apply(album_dir, track, album_dir / "t.opus") is True
    assert 4.5 < duration(album_dir / "t.opus") < 5.5
    assert track.trimmed == signature(track)
    assert (album_dir / ORIGINALS / f"{track.video_id}.opus").read_bytes() == before

    assert apply(album_dir, track, album_dir / "t.opus") is False  # already in that shape

    track.trim_start = 4.0  # re-trim comes from the original, never from the cut file
    apply(album_dir, track, album_dir / "t.opus")
    assert 2.5 < duration(album_dir / "t.opus") < 3.5

    track.trim_start = track.trim_end = None
    assert apply(album_dir, track, album_dir / "t.opus") is True
    assert (album_dir / "t.opus").read_bytes() == before  # byte for byte the original


def test_trim_through_the_service_keeps_tags(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)

    plan = load_plan(album_dir)
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "trim_start": "0:00.2", "trim_end": "0:00.6"}]})
    from ytalbum.download import save_plan

    save_plan(plan, album_dir)
    service.download_existing(album_dir)

    saved = load_plan(album_dir)
    assert saved.tracks[0].trimmed and saved.tracks[0].tagged
    file = album_dir / saved.tracks[0].filename
    assert duration(file) < 0.8
    assert OggOpus(file)["title"] == [saved.tracks[0].title]  # tags survive the cut
    assert (album_dir / ORIGINALS).is_dir()


def test_end_before_start_is_refused():
    plan = build_plan(vol1())
    with pytest.raises(ValueError, match="end must come after"):
        apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "trim_start": "10", "trim_end": "5"}]})


def test_trim_channel_covers_the_whole_library(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)

    channel = load_plan(album_dir).tracks[1].channel
    assert channel == "Napalm Records"
    service.trim_channel(channel, 0.2, None)
    saved = load_plan(album_dir)
    napalm = [t for t in saved.tracks if t.channel == channel]
    assert len(napalm) == 2 and all(t.trim_start == 0.2 and t.trimmed for t in napalm)
    assert all(t.trimmed is None for t in saved.tracks if t.channel != channel)
