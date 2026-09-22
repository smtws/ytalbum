"""Slice 3: re-running a source, user edits, renames and retags — fully offline."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.oggopus import OggOpus

from ytalbum.download import find_plan, load_plan, relocate, run
from ytalbum.models import Collection, Provenance
from ytalbum.plan import build_plan, merge_plans

FIXTURES = Path(__file__).parent.parent / "design-fixtures"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64


def vol1() -> Collection:
    return Collection.from_dict(json.loads((FIXTURES / "vol1_collection.json").read_text()))


class FakeYouTube:
    def __init__(self, template: Path) -> None:
        self.template = template
        self.downloads: list[str] = []

    def download_audio(self, video_id: str, dest_dir: Path) -> Path:
        self.downloads.append(video_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"{video_id}.opus"
        shutil.copy(self.template, out)
        return out

    def fetch_bytes(self, url: str) -> bytes:
        return JPEG


@pytest.fixture(scope="module")
def opus_template(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("tpl") / "t.opus"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "libopus", str(path)], check=True)
    return path


@pytest.fixture
def yt(opus_template) -> FakeYouTube:
    return FakeYouTube(opus_template)


# -- merge (pure) ----------------------------------------------------------------------


def test_new_videos_at_the_end_of_the_playlist_are_appended():
    old_coll = vol1()
    old_coll.entries = old_coll.entries[:-1]  # the playlist before TUNGSTEN was added
    existing = build_plan(old_coll)
    merged = merge_plans(existing, build_plan(vol1()))
    assert [t.number for t in merged.tracks] == list(range(1, 14))
    assert merged.tracks[-1].title == "Lullaby"
    assert merged.tracks[-1].state == "pending"


def test_a_video_that_becomes_available_later_lands_at_its_playlist_position():
    # Vol. 20 live: Feuerschwanz (12th in the playlist) was age-restricted at first,
    # became readable later and was appended as 13 - the curator's order is the content
    first = vol1()
    first.entries[5].skipped = "age-restricted: needs cookies"  # Subway to Sally, 5th song
    existing = build_plan(first)
    assert [t.number for t in existing.tracks] == list(range(1, 13))

    merged = merge_plans(existing, build_plan(vol1()))
    assert [t.video_id for t in merged.tracks] == [t.video_id for t in build_plan(vol1()).tracks]
    assert [t.number for t in merged.tracks] == list(range(1, 14))
    assert merged.tracks[4].title == "Eisblumen"


def test_reordered_playlist_renumbers_and_gone_tracks_go_last():
    existing = build_plan(vol1())
    fresh_coll = vol1()
    fresh_coll.entries[1], fresh_coll.entries[2] = fresh_coll.entries[2], fresh_coll.entries[1]  # curator swapped 1 and 2
    del fresh_coll.entries[5]  # and removed Subway to Sally
    merged = merge_plans(existing, build_plan(fresh_coll))
    assert [t.title for t in merged.tracks[:2]] == ["The Dead Don't Die (feat. xxFEUERSCHWANZxx)", "Lullaby"]
    assert merged.tracks[-1].title == "Eisblumen" and not merged.tracks[-1].in_source
    assert [t.number for t in merged.tracks] == list(range(1, 14))


def test_removed_videos_are_kept_and_flagged():
    existing = build_plan(vol1())
    fresh_coll = vol1()
    del fresh_coll.entries[4]  # Schandmaul left the playlist
    merged = merge_plans(existing, build_plan(fresh_coll))
    schandmaul = next(t for t in merged.tracks if t.artist == "Schandmaul")
    assert not schandmaul.in_source
    assert len(merged.tracks) == 13


def test_user_edits_survive_and_untouched_fields_follow_new_derivations():
    existing = build_plan(vol1())
    existing.tracks[10].artist, existing.tracks[10].title = "Ashley Serena", "Lullaby of Woe"  # user fixes the reversal
    existing.album = "Vol. 1 - Heavy Sleeping (2025)"

    fresh = build_plan(vol1())
    fresh.tracks[0].title = fresh.tracks[0].auto["title"] = "Lullaby (improved)"  # a smarter future derivation

    merged = merge_plans(existing, fresh)
    assert (merged.tracks[10].artist, merged.tracks[10].title) == ("Ashley Serena", "Lullaby of Woe")
    assert merged.tracks[10].provenance == {"artist": Provenance.USER, "title": Provenance.USER}
    assert merged.album == "Vol. 1 - Heavy Sleeping (2025)"
    assert merged.provenance["album"] == Provenance.USER
    assert merged.tracks[0].title == "Lullaby (improved)"


def test_merge_does_not_touch_the_existing_plan():
    existing = build_plan(vol1())
    before = copy.deepcopy(existing)
    merge_plans(existing, build_plan(vol1()))
    assert existing == before


# -- running plans (fake YouTube, real files) ------------------------------------------


def test_first_run_downloads_tags_and_saves_cover(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    assert len(yt.downloads) == 13
    assert (album_dir / "cover.jpg").read_bytes() == JPEG
    assert not (album_dir / ".parts").exists()
    saved = load_plan(album_dir)
    assert all(t.state == "done" and t.tagged for t in saved.tracks)
    assert OggOpus(album_dir / saved.tracks[3].filename)["title"] == ["Prinzessin"]


def test_second_run_does_nothing(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    events = []
    run(load_plan(album_dir), album_dir, yt, on_track=lambda t, what: events.append(what))
    assert events == []
    assert len(yt.downloads) == 13


def test_edited_title_renames_and_retags_without_downloading(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    plan = load_plan(album_dir)
    old_name = plan.tracks[10].filename
    plan.tracks[10].artist, plan.tracks[10].title = "Ashley Serena", "Lullaby of Woe"

    events = []
    run(plan, album_dir, yt, on_track=lambda t, what: events.append(what))
    assert events == ["renamed", "retagged"]
    assert len(yt.downloads) == 13
    assert not (album_dir / old_name).exists()
    new = album_dir / "My Dark Lullabies - Vol. 1 - Heavy Sleeping - 11 - Ashley Serena - Lullaby of Woe.opus"
    assert OggOpus(new)["artist"] == ["Ashley Serena"]


def test_edited_album_moves_the_folder(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    plan = load_plan(album_dir)
    plan.album = "Heavy Sleeping"

    new_dir = relocate(album_dir, plan, tmp_path)
    run(plan, new_dir, yt)
    assert new_dir == tmp_path / "My Dark Lullabies" / "Heavy Sleeping"
    assert not album_dir.exists()
    assert sorted(p.name for p in new_dir.glob("*.opus"))[0].startswith("My Dark Lullabies - Heavy Sleeping - 01 - ")
    assert find_plan(tmp_path, plan.source_id)[0] == new_dir
    assert OggOpus(next(new_dir.glob("*.opus")))["album"] == ["Heavy Sleeping"]


def test_relocate_never_overwrites(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    (tmp_path / "My Dark Lullabies" / "Taken").mkdir()
    plan.album = "Taken"
    assert relocate(album_dir, plan, tmp_path) == album_dir
    assert album_dir.exists()


def test_growing_playlist_downloads_only_the_new_track_and_retags_the_total(tmp_path, yt):
    old_coll = vol1()
    old_coll.entries = old_coll.entries[:-1]
    plan = build_plan(old_coll)
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    assert len(yt.downloads) == 12

    merged = merge_plans(load_plan(album_dir), build_plan(vol1()))
    events = []
    run(merged, album_dir, yt, on_track=lambda t, what: events.append(what))
    assert yt.downloads[12:] == ["QToaDJAtdxI"]
    assert events.count("retagged") == 12  # tracktotal 12 -> 13
    assert OggOpus(album_dir / merged.tracks[0].filename)["tracktotal"] == ["13"]


def test_deleted_file_is_downloaded_again(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    (album_dir / plan.tracks[2].filename).unlink()
    run(load_plan(album_dir), album_dir, yt)
    assert yt.downloads[13:] == [plan.tracks[2].video_id]


def test_user_supplied_cover_wins(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    better = b"\x89PNG\r\n\x1a\n" + b"\1" * 64
    (album_dir / "cover.jpg").unlink()
    (album_dir / "cover.png").write_bytes(better)
    events = []
    run(load_plan(album_dir), album_dir, yt, on_track=lambda t, what: events.append(what))
    assert events.count("retagged") == 13


def test_our_cover_is_upgraded_when_a_better_source_appears(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    assert load_plan(album_dir).cover_fetched["url"] == plan.cover_url

    square = b"\xff\xd8\xff\xe0" + b"\2" * 64
    yt.fetch_bytes = lambda url: square if "coverartarchive" in url else JPEG
    plan = load_plan(album_dir)
    plan.cover_fallback_url, plan.cover_url = plan.cover_url, "https://coverartarchive.org/release-group/x/front-500"
    events = []
    run(plan, album_dir, yt, on_track=lambda t, what: events.append(what))
    assert (album_dir / "cover.jpg").read_bytes() == square
    assert events.count("retagged") == 13
    assert load_plan(album_dir).cover_fetched["url"].startswith("https://coverartarchive.org/")


def test_user_cover_is_never_upgraded(tmp_path, yt):
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    mine = b"\xff\xd8\xff\xe0" + b"\3" * 64
    (album_dir / "cover.jpg").write_bytes(mine)
    plan = load_plan(album_dir)
    plan.cover_url = "https://coverartarchive.org/release-group/x/front-500"
    run(plan, album_dir, yt)
    assert (album_dir / "cover.jpg").read_bytes() == mine


def test_missing_cover_art_falls_back(tmp_path, yt):
    def fetch(url):
        if "coverartarchive" in url:
            raise OSError("404")
        return JPEG

    yt.fetch_bytes = fetch
    plan = build_plan(vol1())
    plan.cover_fallback_url, plan.cover_url = plan.cover_url, "https://coverartarchive.org/release-group/x/front-500"
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    assert (album_dir / "cover.jpg").read_bytes() == JPEG


def test_new_album_folder_follows_enriched_names():
    plan = build_plan(vol1())
    plan.albumartist = "Someone Else"
    from ytalbum.plan import refresh_derived

    assert refresh_derived(plan).folder == "Someone Else/Vol. 1 - Heavy Sleeping"


def test_merge_never_downgrades_to_a_weaker_source():
    existing = build_plan(vol1())
    t = existing.tracks[10]
    t.artist = t.auto["artist"] = "Ashley Serena"  # what MusicBrainz said last time
    t.title = t.auto["title"] = "Lullaby of Woe"
    t.provenance = {"artist": Provenance.MB, "title": Provenance.MB}

    merged = merge_plans(existing, build_plan(vol1()))  # this time without MusicBrainz
    assert (merged.tracks[10].artist, merged.tracks[10].title) == ("Ashley Serena", "Lullaby of Woe")
    assert merged.tracks[10].provenance["artist"] == Provenance.MB


def test_prune_deletes_only_gone_tracks_and_retags_the_rest(tmp_path, yt):
    from ytalbum.config import Config
    from ytalbum.service import Service

    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    fresh_coll = vol1()
    del fresh_coll.entries[5]  # Subway to Sally removed from the playlist
    merged = merge_plans(load_plan(album_dir), build_plan(fresh_coll))
    run(merged, album_dir, yt)
    gone_file = album_dir / next(t.filename for t in merged.tracks if not t.in_source)
    assert gone_file.exists()

    outcome = Service(Config(musicbrainz=False), tmp_path, yt=yt).prune(album_dir)
    assert outcome.status == "ok"
    saved = load_plan(album_dir)
    assert len(saved.tracks) == 12 and all(t.in_source for t in saved.tracks)
    assert [t.number for t in saved.tracks] == list(range(1, 13))
    assert not gone_file.exists()
    assert len(list(album_dir.glob("*.opus"))) == 12
    assert OggOpus(album_dir / saved.tracks[0].filename)["tracktotal"] == ["12"]
    assert len(yt.downloads) == 13  # nothing downloaded again


def test_prune_never_deletes_outside_the_album(tmp_path, yt):
    from ytalbum.config import Config
    from ytalbum.download import save_plan
    from ytalbum.service import Service

    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, yt)
    victim = tmp_path / "precious.txt"
    victim.write_text("keep me")
    plan = load_plan(album_dir)
    plan.tracks[0].in_source = False
    plan.tracks[0].filename = "../../precious.txt"  # a tampered plan
    save_plan(plan, album_dir)
    Service(Config(musicbrainz=False), tmp_path, yt=yt).prune(album_dir)
    assert victim.read_text() == "keep me"
