"""Deleting tracks and albums: only our files go, and nothing is remembered."""

import json
from pathlib import Path

import pytest
from mutagen.oggopus import OggOpus

from test_incremental import FakeYouTube, opus_template, vol1  # noqa: F401 (fixture)
from ytalbum.config import Config
from ytalbum.download import load_plan, run
from ytalbum.plan import build_plan, merge_plans
from ytalbum.service import Service
from ytalbum.trim import ORIGINALS


@pytest.fixture
def library(tmp_path, opus_template):
    yt = FakeYouTube(opus_template)
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, yt)
    return tmp_path, plan, yt


def test_delete_track_removes_files_renumbers_and_retags(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)

    victim = load_plan(album_dir).tracks[1]
    (album_dir / ORIGINALS).mkdir()
    (album_dir / ORIGINALS / f"{victim.video_id}.opus").write_bytes(b"original")
    assert service.delete_track(plan.source_id, victim.video_id).status == "ok"

    saved = load_plan(album_dir)
    assert len(saved.tracks) == 12 and victim.video_id not in {t.video_id for t in saved.tracks}
    assert [t.number for t in saved.tracks] == list(range(1, 13))
    assert not (album_dir / victim.filename).exists()
    assert not (album_dir / ORIGINALS / f"{victim.video_id}.opus").exists()
    assert len(list(album_dir.glob("*.opus"))) == 12
    assert OggOpus(album_dir / saved.tracks[0].filename)["tracktotal"] == ["12"]
    assert len(yt.downloads) == 13  # nothing downloaded again


def test_a_deleted_track_comes_back_on_the_next_update(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)
    victim = load_plan(album_dir).tracks[1]
    service.delete_track(plan.source_id, victim.video_id)

    merged = merge_plans(load_plan(album_dir), build_plan(vol1()))  # the video is still in the playlist
    run(merged, album_dir, yt)
    assert victim.video_id in {t.video_id for t in load_plan(album_dir).tracks}
    assert yt.downloads[13:] == [victim.video_id]


def test_delete_album_removes_the_folder(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    outcome = Service(Config(musicbrainz=False), tmp_path, yt=yt).delete_album(plan.source_id)
    assert outcome.status == "ok"
    assert not album_dir.exists()
    assert not album_dir.parent.exists()  # the artist folder went too, it was empty
    assert list(tmp_path.iterdir()) == []


def test_delete_album_keeps_files_that_are_not_ours(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    (album_dir / "my notes.txt").write_text("keep me")
    (album_dir / "booklet.pdf").write_bytes(b"%PDF-")
    messages = []
    Service(Config(musicbrainz=False), tmp_path, yt=yt, log=messages.append).delete_album(plan.source_id)

    assert album_dir.exists()
    assert sorted(p.name for p in album_dir.iterdir()) == ["booklet.pdf", "my notes.txt"]
    assert (album_dir / "my notes.txt").read_text() == "keep me"
    assert any("not ours" in m for m in messages)


def test_deleting_something_unknown_fails_cleanly(library):
    tmp_path, plan, yt = library
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt)
    assert service.delete_track("nope", "x").status == "failed"
    assert service.delete_track(plan.source_id, "nope").status == "failed"
    assert service.delete_album("nope").status == "failed"
    assert len(list((tmp_path / plan.folder).glob("*.opus"))) == 13  # nothing touched
