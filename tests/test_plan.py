import json
from pathlib import Path

import pytest

from ytalbum.models import AlbumPlan, Collection, Kind, Provenance
from ytalbum.plan import build_plan, classify, compilation_album_title, safe_name, track_filename
from ytalbum.youtube import entry_from_info

FIXTURES = Path(__file__).parent.parent / "design-fixtures"


def load_collection(name: str) -> Collection:
    return Collection.from_dict(json.loads((FIXTURES / name).read_text()))


def flat_collection(name: str) -> Collection:
    """A Collection from a flat `yt-dlp -J` dump (no per-video music fields)."""
    d = json.loads((FIXTURES / name).read_text())
    return Collection(
        source_url=d["webpage_url"],
        source_id=d["id"],
        is_playlist=True,
        title=d["title"],
        channel=d["channel"],
        thumbnail=None,
        fetched_at="2026-09-22T00:00:00+00:00",
        entries=[entry_from_info(e, i) for i, e in enumerate(d["entries"], 1)],
    )


@pytest.fixture
def vol1() -> Collection:
    return load_collection("vol1_collection.json")


def test_vol1_is_a_compilation_by_the_curator(vol1):
    plan = build_plan(vol1)
    assert plan.kind == Kind.COMPILATION
    assert plan.albumartist == "My Dark Lullabies"
    assert plan.album == "Vol. 1 - Heavy Sleeping"
    assert plan.folder == "My Dark Lullabies/Vol. 1 - Heavy Sleeping"
    assert plan.provenance == {"albumartist": Provenance.PLAYLIST, "album": Provenance.PLAYLIST}


def test_vol1_skips_the_intro_card(vol1):
    plan = build_plan(vol1)
    assert len(vol1.entries) == 14
    assert len(plan.tracks) == 13
    assert [s["video_id"] for s in plan.skipped] == ["0gr0bwQgTSo"]
    assert [t.number for t in plan.tracks] == list(range(1, 14))


def test_vol1_uses_youtube_music_fields_where_present(vol1):
    by_id = {t.video_id: t for t in build_plan(vol1).tracks}
    schandmaul = by_id["Lkrs1eggmBg"]
    assert (schandmaul.artist, schandmaul.title) == ("Schandmaul", "Prinzessin")
    assert schandmaul.provenance == {"artist": Provenance.YT_MUSIC, "title": Provenance.YT_MUSIC}
    mantus = by_id["ON7dZX0HPoI"]
    assert (mantus.artist, mantus.title) == ("Mantus", "Ein Hauch von Wirklichkeit")


def test_compilation_filenames_always_carry_the_track_artist(vol1):
    track = build_plan(vol1).tracks[3]
    assert track.filename == "My Dark Lullabies - Vol. 1 - Heavy Sleeping - 04 - Schandmaul - Prinzessin.opus"


def test_all_twenty_volume_titles_normalise():
    d = json.loads((FIXTURES / "tab_playlists.json").read_text())
    titles = [compilation_album_title(e["title"], "My Dark Lullabies") for e in d["entries"]]
    assert len(titles) == 20
    assert titles[-1] == "Vol. 1 - Heavy Sleeping"
    assert "Vol. 4 - Haunted Nursery" in titles  # source uses an en dash
    assert "Vol. 17 - 25 Shadows Later" in titles  # source has no space after "Vol."
    assert all(t.startswith("Vol. ") for t in titles)


@pytest.mark.parametrize(
    "raw",
    ["My Dark Lullabies Vol.1 - Heavy Sleeping", "MyDarkLullabies vol 1: Heavy Sleeping", "my dark lullabies – Vol. 1 – Heavy Sleeping"],
)
def test_curator_prefix_is_matched_by_words(raw):
    assert compilation_album_title(raw, "My Dark Lullabies") == "Vol. 1 - Heavy Sleeping"


def test_title_without_curator_prefix_is_kept():
    assert compilation_album_title("Best of Goth 2024", "My Dark Lullabies") == "Best of Goth 2024"


def test_artist_full_album_playlist_is_not_an_official_album():
    legends = flat_collection("legends.json")
    assert classify(legends) == Kind.ARTIST_PLAYLIST
    plan = build_plan(legends)
    assert plan.albumartist == "Sabaton"
    assert plan.album == "Legends"  # "SABATON - " prefix and "(Full Album)" stripped
    # the 17-entry playlist is kept as-is for now; recognising the 11 real songs is MB's job (slice 5)
    assert len(plan.tracks) == 17


def test_olak_playlists_are_official_albums(vol1):
    vol1.source_id = "OLAK5uy_example"
    assert classify(vol1) == Kind.OFFICIAL_ALBUM


def test_single_video_is_a_single(vol1):
    vol1.is_playlist = False
    assert classify(vol1) == Kind.SINGLE


def test_track_artist_omitted_when_same_as_albumartist():
    assert track_filename("Sabaton", "Legends", 3, None, "Templars") == "Sabaton - Legends - 03 - Templars.opus"


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("AC/DC", "AC-DC"),
        ('ENEMY INSIDE - "Lullaby"', "ENEMY INSIDE - 'Lullaby'"),
        ("Who? What: Why*", "Who What - Why"),
        ("  trailing dots... ", "trailing dots"),
        ("", "_"),
    ],
)
def test_safe_name(raw, safe):
    assert safe_name(raw) == safe


def test_safe_name_limits_bytes():
    assert len(safe_name("ö" * 500).encode()) <= 240


def test_plan_round_trips_through_json(vol1):
    plan = build_plan(vol1)
    again = AlbumPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again == plan


def test_unknown_plan_schema_is_refused(vol1):
    d = build_plan(vol1).to_dict() | {"schema": 99}
    with pytest.raises(ValueError, match="schema"):
        AlbumPlan.from_dict(d)


# -- a playlist on the artist's own channel is not a compilation of that channel -----------


@pytest.fixture
def methaemmer():
    return load_collection("artist_channel_playlist.json")


@pytest.fixture
def elfte_gebot():
    return load_collection("artist_channel_playlist2.json")


def test_album_title_glued_to_the_artist_is_not_a_second_artist(methaemmer):
    # titles on the band's channel read "Feuerschwanz Methämmer - Song by Song - …"
    assert classify(methaemmer) == Kind.ARTIST_PLAYLIST
    plan = build_plan(methaemmer)
    assert plan.albumartist == "Feuerschwanz"  # not the channel handle "xxFEUERSCHWANZxx"
    assert {t.artist for t in plan.tracks} == {"Feuerschwanz"}
    assert plan.tracks[5].title == "Song by Song - Schubsetanz"  # the album name is not the artist


def test_playlist_title_as_artist_and_a_guest_credit_are_not_extra_artists(elfte_gebot):
    # "Das Elfte Gebot - Unboxing" names no artist; "FEUERSCHWANZ ft. Melissa Bonny" is one
    assert classify(elfte_gebot) == Kind.ARTIST_PLAYLIST
    plan = build_plan(elfte_gebot)
    assert plan.albumartist == "FEUERSCHWANZ"  # the library harmonises the spelling later
    assert {t.artist for t in plan.tracks} == {"FEUERSCHWANZ"}
    assert plan.tracks[2].title == "Ding (SEEED Cover) ft. Melissa Bonny"
    assert plan.tracks[8].title == "Unboxing"


def test_a_real_compilation_still_is_one(vol1):
    assert classify(vol1) == Kind.COMPILATION  # 13 bands, no channel of their own


def test_the_uploader_names_the_album_when_nothing_else_does():
    """An album playlist carries no channel, but its videos do — "Unknown Artist" otherwise."""
    entries = [
        {"video_id": f"{i:011d}", "position": i, "title": f"Der Derwisch {i} 🌀 Epic Fantasy Ambience", "duration": 600,
         "channel": "Saltatio Mortis"}
        for i in range(1, 5)
    ]
    collection = Collection.from_dict({
        "source_url": "u", "source_id": "OLAK5uy_x", "is_playlist": True,
        "title": "Träume von Staub & Schatten, Vol. 1", "channel": None, "thumbnail": None,
        "fetched_at": "2026-09-24T00:00:00", "entries": entries,
    })
    plan = build_plan(collection)
    assert plan.albumartist == "Saltatio Mortis"
    assert {t.artist for t in plan.tracks} == {"Saltatio Mortis"}
    assert plan.folder.startswith("Saltatio Mortis/")
