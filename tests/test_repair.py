"""Offline repair: performer-only artists and one spelling per artist, without asking YouTube."""

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.models import Provenance
from ytalbum.plan import build_plan, refresh_derived
from ytalbum.service import Service


class NoNetwork(FakeYouTube):
    """Any call to YouTube fails the test."""

    def download_audio(self, *a, **k):
        raise AssertionError("repair must not download")

    def fetch_bytes(self, *a, **k):
        raise AssertionError("repair must not fetch")


def library_with(tmp_path, opus_template, changes):
    plan = build_plan(vol1())
    changes(plan)
    refresh_derived(plan)
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, FakeYouTube(opus_template))
    save_plan(plan, album_dir)
    return tmp_path, plan


def service(tmp_path, opus_template):
    return Service(Config(musicbrainz=False), tmp_path, yt=NoNetwork(opus_template))


def test_writer_lists_are_reduced_to_the_performer(tmp_path, opus_template):
    def pollute(plan):
        plan.kind = "artist_playlist"
        for t in plan.tracks:
            t.artist = t.auto["artist"] = "Feuerschwanz, Benjamin Metzner, Peter Henrici"
            t.provenance["artist"] = Provenance.YT_MUSIC
        plan.albumartist = plan.auto["albumartist"] = "Feuerschwanz, Benjamin Metzner, Peter Henrici"
        plan.provenance["albumartist"] = Provenance.YT_MUSIC

    tmp_path, plan = library_with(tmp_path, opus_template, pollute)
    service(tmp_path, opus_template).repair()

    saved = load_plan(tmp_path / "Feuerschwanz" / plan.album)
    assert saved.albumartist == "Feuerschwanz"
    assert {t.artist for t in saved.tracks} == {"Feuerschwanz"}
    assert (tmp_path / "Feuerschwanz" / plan.album / saved.tracks[0].filename).exists()
    assert not (tmp_path / "Feuerschwanz, Benjamin Metzner, Peter Henrici").exists()


def test_one_spelling_per_artist(tmp_path, opus_template):
    def shout(plan):
        plan.kind = "artist_playlist"
        plan.albumartist = plan.auto["albumartist"] = "SCHANDMAUL"
        plan.provenance["albumartist"] = Provenance.YT_TITLE
        for t in plan.tracks:
            t.artist = t.auto["artist"] = "Schandmaul"

    tmp_path, plan = library_with(tmp_path, opus_template, shout)
    (tmp_path / "Schandmaul" / "Another Album").mkdir(parents=True)  # the spelling used elsewhere
    other = build_plan(vol1())
    other.source_id, other.albumartist, other.album = "PLother", "Schandmaul", "Another Album"
    save_plan(other, tmp_path / "Schandmaul" / "Another Album")

    service(tmp_path, opus_template).repair()
    assert (tmp_path / "Schandmaul" / plan.album / ".ytalbum.json").exists()
    assert not (tmp_path / "SCHANDMAUL").exists()


def test_your_own_artist_name_is_left_alone(tmp_path, opus_template):
    def mine(plan):
        plan.kind = "artist_playlist"
        plan.albumartist = "MY BAND"
        plan.provenance["albumartist"] = Provenance.USER

    tmp_path, plan = library_with(tmp_path, opus_template, mine)
    service(tmp_path, opus_template).repair()
    assert (tmp_path / "MY BAND" / plan.album / ".ytalbum.json").exists()


def test_compilations_keep_their_curator(tmp_path, opus_template):
    tmp_path, plan = library_with(tmp_path, opus_template, lambda p: None)
    service(tmp_path, opus_template).repair()
    saved = load_plan(tmp_path / plan.folder)
    assert saved.albumartist == "My Dark Lullabies"
    assert saved.tracks[1].artist.startswith("DOMINUM")  # track artists stay as they are


def test_a_harmonised_artist_lands_in_the_right_folder_at_once(tmp_path, opus_template):
    """The spelling was unified but the album stayed in the old folder until the next run."""
    first = build_plan(vol1())
    first.albumartist, first.provenance["albumartist"] = "Saltatio Mortis", Provenance.MB
    refresh_derived(first)
    run(first, tmp_path / first.folder, FakeYouTube(opus_template))
    save_plan(first, tmp_path / first.folder)

    shouting = vol1()
    shouting.source_id = shouting.source_url = "PL-second"
    for e in shouting.entries:
        e.video_id = "x" + e.video_id[1:]
        e.music.artist = "SALTATIO MORTIS"
    service = Service(Config(library_root=tmp_path, musicbrainz=False), tmp_path, yt=FakeYouTube(opus_template))
    service.yt.fetch = lambda url: shouting

    outcome = service.fetch("https://www.youtube.com/playlist?list=PL-second")
    assert outcome.plan.albumartist == "Saltatio Mortis"  # the library's spelling wins
    assert outcome.album_dir.parent.name == "Saltatio Mortis"  # and the folder follows immediately
    assert not (tmp_path / "SALTATIO MORTIS").exists()
