"""Slice 18: telling a song's length from what we downloaded — the rule behind the marks."""

import pytest
from test_incremental import vol1

from ytalbum.plan import LENGTH_BIG, album_length_flag, build_plan, effective_length, length_gap, reference_length


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


def test_an_album_of_teasers_is_flagged_as_short():
    # what this first caught: a Sabaton "album" of eleven track-commentary clips
    plan = album(34.0, 44.0, 39.0, 50.0, mb=200.0)
    assert album_length_flag(plan) == {"way": "short", "n": 4, "of": 4}


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
