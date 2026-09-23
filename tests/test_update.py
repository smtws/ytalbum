"""The cheap update: skip unchanged albums, but never miss a real change."""

import json
from pathlib import Path

import pytest

from test_incremental import FakeYouTube, opus_template, vol1  # noqa: F401 (fixture)
from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.plan import build_plan
from ytalbum.service import Service


class CountingYouTube(FakeYouTube):
    """Answers the cheap check from a list of ids; counts what was asked."""

    def __init__(self, template, ids, modified="20260101"):
        super().__init__(template)
        self.ids, self.modified = ids, modified
        self.state_calls, self.full_fetches = 0, 0

    def source_state(self, url):
        self.state_calls += 1
        return {"ids": list(self.ids), "modified": self.modified}

    def fetch(self, url):
        self.full_fetches += 1
        collection = vol1()
        collection.entries = [e for e in collection.entries if e.video_id in self.ids]
        collection.modified = self.modified
        return collection


@pytest.fixture
def library(tmp_path, opus_template):
    collection = vol1()
    collection.modified = "20260101"  # as a real read records it
    plan = build_plan(collection)
    ids = [e.video_id for e in collection.entries]
    yt = CountingYouTube(opus_template, ids)
    run(plan, tmp_path / plan.folder, yt)
    save_plan(plan, tmp_path / plan.folder)
    return tmp_path, plan, yt


def service(tmp_path, yt):
    return Service(Config(musicbrainz=False), tmp_path, yt=yt)


def test_unchanged_album_costs_one_request(library):
    tmp_path, plan, yt = library
    outcomes = service(tmp_path, yt).update_all()
    assert [o.message for o in outcomes] == ["unchanged"]
    assert (yt.state_calls, yt.full_fetches) == (1, 0)


def test_a_changed_playlist_is_read_in_full(library):
    tmp_path, plan, yt = library
    yt.ids = yt.ids[:-1]  # a video left the playlist
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_new_modification_date_is_read_in_full(library):
    tmp_path, plan, yt = library
    yt.modified = "20260202"
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_an_incomplete_album_is_always_read(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.tracks[0].state = "failed"
    save_plan(saved, album_dir)
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_track_waiting_for_a_decision_does_not_force_a_full_read(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.tracks[0].state, saved.tracks[0].error_kind = "failed", "no_audio_stream"
    save_plan(saved, album_dir)
    outcomes = service(tmp_path, yt).update_all()
    assert yt.full_fetches == 0 and outcomes[0].message == "unchanged"


def test_deep_reads_everything(library):
    tmp_path, plan, yt = library
    service(tmp_path, yt).update_all(deep=True)
    assert (yt.state_calls, yt.full_fetches) == (0, 1)


def test_an_album_from_before_this_existed_is_read_once(library):
    tmp_path, plan, yt = library
    album_dir = tmp_path / plan.folder
    saved = load_plan(album_dir)
    saved.source_state = {}  # an album from before this existed
    save_plan(saved, album_dir)
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_a_failing_quick_check_falls_back_to_reading(library):
    tmp_path, plan, yt = library

    def boom(url):
        raise RuntimeError("network hiccup")

    yt.source_state = boom
    service(tmp_path, yt).update_all()
    assert yt.full_fetches == 1


def test_update_can_be_limited_to_one_artist(tmp_path, opus_template):
    from ytalbum.models import Collection

    first = vol1()
    plan_one = build_plan(first)
    yt = CountingYouTube(opus_template, [e.video_id for e in first.entries])
    run(plan_one, tmp_path / plan_one.folder, yt)

    other = vol1()  # a second album under a different artist
    other.source_id, other.source_url, other.channel = "PLother", "https://www.youtube.com/playlist?list=PLother", "Someone Else"
    plan_two = build_plan(other)
    plan_two.albumartist = "Someone Else"
    from ytalbum.plan import refresh_derived

    refresh_derived(plan_two)
    run(plan_two, tmp_path / plan_two.folder, yt)

    yt.state_calls = 0
    Service(Config(musicbrainz=False), tmp_path, yt=yt).update_all(artist="Someone Else")
    assert yt.state_calls == 1  # only that artist's album was checked
