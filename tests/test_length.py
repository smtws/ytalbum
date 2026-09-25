"""Slice 18: telling a song's length from what we downloaded — the rule behind the marks."""

import pytest
from test_incremental import vol1

from ytalbum.plan import (
    LENGTH_BIG,
    album_length_flag,
    build_plan,
    effective_length,
    is_stub,
    length_gap,
    reference_length,
)


def album(*lengths, mb=None, done=True):
    """An album whose tracks are `lengths` seconds long, against `mb` seconds each."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[: len(lengths)]
    for t, ours in zip(plan.tracks, lengths, strict=True):
        t.state = "done" if done else "pending"
        t.file_length = ours
        t.mb_length = mb
    return plan


def test_the_file_is_believed_over_the_video():
    t = album(180.0).tracks[0]
    t.duration = 300.0
    assert effective_length(t) == 180.0  # measured beats what YouTube said


def test_without_a_file_length_the_trims_are_taken_off_the_video():
    t = album(None).tracks[0]
    t.duration, t.trim_start, t.trim_end = 300.0, 8.0, 280.0
    assert effective_length(t) == 272.0


def test_lrclib_answers_where_musicbrainz_is_silent():
    t = album(180.0).tracks[0]
    t.mb_length, t.lyrics_length = None, 176.0
    assert reference_length(t) == 176.0
    assert length_gap(t) == pytest.approx(4.0)
    t.mb_length = 181.0
    assert reference_length(t) == 181.0  # MusicBrainz first when both have one


def test_nobody_else_has_an_opinion():
    t = album(180.0).tracks[0]
    t.mb_length = t.lyrics_length = None
    assert length_gap(t) is None
    assert album_length_flag(album(180.0, 190.0)) is None


def test_an_album_of_teasers_is_flagged_as_clips():
    # what this first caught: a Sabaton "album" of eleven track-commentary clips
    plan = album(34.0, 44.0, 39.0, 50.0, mb=200.0)
    assert album_length_flag(plan) == {"way": "stub", "n": 4, "of": 4}


def test_one_track_far_under_the_known_length_is_enough():
    """8 tracks of 3946 are one, and each was a snippet, a radio edit or a wrong match."""
    plan = album(78.0, 200.0, 200.0, 200.0, mb=198.0)
    assert album_length_flag(plan) == {"way": "stub", "n": 1, "of": 4}
    assert is_stub(plan.tracks[0]) and not is_stub(plan.tracks[1])


def test_a_short_album_that_is_not_made_of_clips():
    # 40s under a 200s song is wrong, but it is still most of the song
    plan = album(160.0, 158.0, 155.0, 200.0, mb=200.0)
    assert album_length_flag(plan) == {"way": "short", "n": 3, "of": 4}


def test_two_long_tracks_are_not_enough_to_damn_an_album():
    """A volume where only four tracks can be compared kept being flagged for two long ones."""
    plan = album(230.0, 230.0, 200.0, 200.0, mb=200.0)
    assert album_length_flag(plan) is None


def test_an_album_of_padded_uploads_is_flagged_as_long():
    plan = album(280.0, 290.0, 300.0, 200.0, mb=200.0)
    assert album_length_flag(plan) == {"way": "long", "n": 3, "of": 4}


def test_a_few_odd_tracks_are_not_an_album_problem():
    # 13% of the library is >5s longer than MusicBrainz: flagging that would flag everything
    plan = album(200.0, 200.0, 260.0, 200.0, 200.0, 200.0, mb=200.0)
    assert album_length_flag(plan) is None


def test_the_gap_has_to_be_wide():
    plan = album(*[200.0 + LENGTH_BIG for _ in range(4)], mb=200.0)
    assert album_length_flag(plan) is None  # exactly at the threshold is still fine
    plan = album(*[200.0 + LENGTH_BIG + 0.1 for _ in range(4)], mb=200.0)
    assert album_length_flag(plan)["way"] == "long"


def test_only_finished_tracks_count():
    plan = album(34.0, 44.0, 39.0, 50.0, mb=200.0, done=False)
    assert album_length_flag(plan) is None


def test_one_comparable_track_is_never_enough():
    plan = album(34.0, 200.0, mb=200.0)
    plan.tracks[1].mb_length = None
    assert album_length_flag(plan) is None


def test_renaming_a_track_gives_up_the_length_that_came_with_its_recording():
    """Otherwise the pair breaks and `repair` drops the length later, seemingly by itself."""
    from ytalbum.service import apply_user_edits

    plan = album(471.0, 200.0, mb=229.8)
    for t in plan.tracks:
        t.mbid = f"rec-{t.number}"
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "title": "Viva Vendetta (video version)"}]})

    assert (plan.tracks[0].mbid, plan.tracks[0].mb_length) == (None, None)
    assert (plan.tracks[1].mbid, plan.tracks[1].mb_length) == ("rec-2", 229.8)  # untouched
