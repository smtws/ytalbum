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


# -- the original is kept in the track's own format (DESIGN.md §9.20) --------------------


@pytest.fixture
def aac(tmp_path):
    """What a track taken from the combined stream looks like: AAC in an MP4 container."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "tone.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=10", "-c:a", "aac", str(path)], check=True)
    return path


def m4a_track(plan):
    t = plan.tracks[0]
    t.ext, t.filename = "m4a", Path(t.filename).with_suffix(".m4a").name
    return t


def test_an_m4a_track_is_cut_into_its_own_container(tmp_path, aac):
    from ytalbum.trim import holds, original_path

    plan = build_plan(vol1())
    track = m4a_track(plan)
    shutil.copy(aac, tmp_path / track.filename)
    track.trim_start, track.trim_end = 2.0, 7.0

    assert apply(tmp_path, track, tmp_path / track.filename) is True
    assert original_path(tmp_path, track).name.endswith(".m4a")  # not ".opus"
    assert holds(tmp_path / track.filename, "m4a"), "the cut must stay an MP4 file"
    assert holds(original_path(tmp_path, track), "m4a")
    assert 4.5 < duration(tmp_path / track.filename) < 5.5


def test_an_m4a_track_round_trips(tmp_path, aac):
    plan = build_plan(vol1())
    track = m4a_track(plan)
    path = tmp_path / track.filename
    shutil.copy(aac, path)
    before = path.read_bytes()

    track.trim_start = 3.0
    apply(tmp_path, track, path)
    track.trim_start = None
    apply(tmp_path, track, path)
    assert path.read_bytes() == before  # restored byte for byte
    track.trim_start = 4.0
    assert apply(tmp_path, track, path) is True  # and can be cut again


def test_an_original_of_another_format_is_never_cut_from(tmp_path, aac, tone):
    """The B6 case: a track switched to the combined stream still had its opus original."""
    from ytalbum.trim import ORIGINALS

    plan = build_plan(vol1())
    track = m4a_track(plan)
    path = tmp_path / track.filename
    shutil.copy(aac, path)
    (tmp_path / ORIGINALS).mkdir()
    shutil.copy(tone, tmp_path / ORIGINALS / f"{track.video_id}.opus")  # the leftover
    track.trimmed = "1.00-"  # the file on disk is already cut: nothing to take a fresh one from
    track.trim_start = 2.0

    with pytest.raises(RuntimeError, match="neither cut again nor put back"):
        apply(tmp_path, track, path)
    assert path.read_bytes() == aac.read_bytes(), "the audio must be left alone"


def test_a_leftover_original_is_replaced_when_the_file_is_untouched(tmp_path, aac, tone):
    from ytalbum.trim import ORIGINALS, holds, original_path

    plan = build_plan(vol1())
    track = m4a_track(plan)
    path = tmp_path / track.filename
    shutil.copy(aac, path)
    (tmp_path / ORIGINALS).mkdir()
    stale = tmp_path / ORIGINALS / f"{track.video_id}.opus"
    shutil.copy(tone, stale)
    track.trimmed = None  # the file on disk *is* the untouched download
    track.trim_start = 2.0

    assert apply(tmp_path, track, path) is True
    assert holds(original_path(tmp_path, track), "m4a")
    assert not stale.exists(), "the previous format's copy is dead weight"


def test_ffmpeg_missing_is_reported_and_changes_nothing(tmp_path, tone, monkeypatch):
    plan = build_plan(vol1())
    track = plan.tracks[0]
    path = tmp_path / track.filename
    shutil.copy(tone, path)
    before = path.read_bytes()
    monkeypatch.setenv("PATH", "/nonexistent")
    track.trim_start = 2.0

    with pytest.raises(RuntimeError, match="could not trim"):
        apply(tmp_path, track, path)
    assert path.read_bytes() == before
    assert track.trimmed is None


# -- a failed trim is never silent, and one bad file is not the album's problem ----------


def test_a_failed_trim_is_written_down_and_reported(tmp_path, opus_template, monkeypatch):
    """It used to set track.error and then never save the plan, so nothing survived the run."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:2]
    album_dir = tmp_path / "album"
    yt = FakeYouTube(opus_template)
    run(plan, album_dir, yt)

    events = []
    plan.tracks[0].trim_start = 2.0
    monkeypatch.setenv("PATH", "/nonexistent")  # no ffmpeg
    run(plan, album_dir, yt, on_track=lambda t, what: events.append((t.number, what)))

    saved = load_plan(album_dir)
    assert "could not trim" in (saved.tracks[0].error or ""), "the reason must survive in the plan"
    assert (1, "trim failed") in events, "and reach the job log"
    assert saved.tracks[0].trim_start == 2.0, "the request itself stays, to be retried"
    assert saved.tracks[0].trimmed is None


def test_the_retry_clears_the_error_and_announces_itself(tmp_path, opus_template, monkeypatch):
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:1]
    album_dir = tmp_path / "album"
    yt = FakeYouTube(opus_template)
    run(plan, album_dir, yt)
    plan.tracks[0].trim_start = 0.2
    monkeypatch.setenv("PATH", "/nonexistent")
    run(plan, album_dir, yt)
    monkeypatch.undo()

    events = []
    run(plan, album_dir, yt, on_track=lambda t, what: events.append(what))
    saved = load_plan(album_dir)
    assert "trimmed" in events, "applying it later is an event of its own"
    assert saved.tracks[0].error is None
    assert saved.tracks[0].trimmed == "0.20-"


def test_one_unreadable_file_fails_its_own_track_only(tmp_path, opus_template):
    """A file whose contents do not match its name used to abort the whole album's run."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:3]
    album_dir = tmp_path / "album"
    yt = FakeYouTube(opus_template)
    run(plan, album_dir, yt)

    broken = album_dir / plan.tracks[1].filename
    broken.write_bytes(b"this is not audio")
    plan.tracks[1].tagged = None  # force a retag of that track

    events = []
    run(plan, album_dir, yt, download=False, on_track=lambda t, what: events.append((t.number, what)))
    saved = load_plan(album_dir)
    assert saved.tracks[1].state == "failed"
    assert "cannot be tagged" in (saved.tracks[1].error or "")
    assert (2, "failed") in events
    assert [t.state for t in saved.tracks] == ["done", "failed", "done"], "the others are untouched"


def test_clearing_a_trim_with_nothing_to_restore_from_is_refused(tmp_path, tone):
    """The mirror of the refusal above: the plan must not call a cut file untouched."""
    plan = build_plan(vol1())
    track = plan.tracks[0]
    path = tmp_path / track.filename
    shutil.copy(tone, path)
    track.trim_start = 2.0
    apply(tmp_path, track, path)
    cut = path.read_bytes()
    for kept in (tmp_path / ORIGINALS).glob("*"):
        kept.unlink()  # the originals folder is gone, the file stays cut

    track.trim_start = None
    with pytest.raises(RuntimeError, match="neither cut again nor put back"):
        apply(tmp_path, track, path)
    assert path.read_bytes() == cut, "the audio is left alone"
    assert track.trimmed == "2.00-", "and the plan still says what the file really is"


def test_a_re_downloaded_track_does_not_inherit_the_old_trim_state(tmp_path, opus_template):
    """A tagging failure re-downloads the track; the fresh file is untouched, so `trimmed` must be."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:1]
    album_dir = tmp_path / "album"
    yt = FakeYouTube(opus_template)
    run(plan, album_dir, yt)
    plan.tracks[0].trim_start = 0.2
    run(plan, album_dir, yt)
    assert load_plan(album_dir).tracks[0].trimmed == "0.20-"

    (album_dir / plan.tracks[0].filename).write_bytes(b"not audio")  # make the tagging fail
    plan.tracks[0].tagged = None
    run(plan, album_dir, yt, download=False)
    assert load_plan(album_dir).tracks[0].state == "failed"

    run(plan, album_dir, yt)  # it is fetched again
    saved = load_plan(album_dir)
    assert saved.tracks[0].state == "done"
    assert saved.tracks[0].trimmed is None, "the fresh file is untouched"
    assert saved.tracks[0].trim_start == 0.2, "and the request is still there"

    run(plan, album_dir, yt, download=False)  # the following pass applies it
    assert load_plan(album_dir).tracks[0].trimmed == "0.20-"
