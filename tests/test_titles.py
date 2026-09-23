import json
from pathlib import Path

import pytest

from ytalbum.models import Collection
from ytalbum.plan import build_plan
from ytalbum.titles import channel_artist, clean_title, move_feat, parse_video_title, split_feat, strip_self_feat

FIXTURES = Path(__file__).parent.parent / "design-fixtures"


def test_vol1_tracks_come_out_clean():
    plan = build_plan(Collection.from_dict(json.loads((FIXTURES / "vol1_collection.json").read_text())))
    assert [(t.artist, t.title) for t in plan.tracks] == [
        ("ENEMY INSIDE", "Lullaby"),  # label channel: artist from the title
        ("DOMINUM", "The Dead Don't Die (feat. xxFEUERSCHWANZxx)"),  # handle cleanup needs a lookup (slice 5)
        ("Mono Inc", "Heile, Heile Segen"),  # fan channel
        ("Schandmaul", "Prinzessin"),  # YT Music fields
        ("Subway To Sally", "Eisblumen"),
        ("Lacrimosa", "Halt mich"),
        ("Letzte Instanz", "Wir sind allein"),  # Artist "Title" [noise]
        ("Mantus", "Ein Hauch von Wirklichkeit"),
        ("Erben der Schöpfung", "Elis"),
        ("Disturbed", "The Sound Of Silence"),
        ("Lullaby of Woe", "Ashley Serena"),  # reversed on a lyrics channel: only a lookup can tell (slice 5)
        ("LORD OF THE LOST", "One Last Song"),
        ("TUNGSTEN", "Lullaby"),
    ]


@pytest.mark.parametrize(
    ("title", "channel", "expected"),
    [
        ("SABATON - Templars (Official Music Video)", "Sabaton", ("Sabaton", "Templars")),
        ("SABATON - Crossing the Rubicon feat. Nothing More (Official Lyric Video)", "Sabaton", ("Sabaton", "Crossing the Rubicon feat. Nothing More")),
        ("Heile, Heile Segen - Mono Inc.", "Mono Inc.", ("Mono Inc.", "Heile, Heile Segen")),  # reversed, channel decides
        ("Prinzessin", "Schandmaul", (None, "Prinzessin")),
        ("Song (Live at Wacken 2019)", "Band", (None, "Song (Live at Wacken 2019)")),  # meaningful brackets stay
        ("Artist – Song [HD]", None, ("Artist", "Song")),
        ("Artist -- Song", None, ("Artist", "Song")),
        ("Well-Known Band - Song", None, ("Well-Known Band", "Song")),  # hyphen without spaces is not a separator
    ],
)
def test_parse_video_title(title, channel, expected):
    assert parse_video_title(title, channel) == expected


@pytest.mark.parametrize(
    ("channel", "artist"),
    [("Mantus - Topic", "Mantus"), ("LACRIMOSAofficial", "LACRIMOSA"), ("SabatonVEVO", "Sabaton"), ("Napalm Records", "Napalm Records"), (None, None)],
)
def test_channel_artist(channel, artist):
    assert channel_artist(channel) == artist


def test_clean_title_keeps_non_noise():
    assert clean_title('"Lullaby" (Official Video)') == "Lullaby"
    assert clean_title("One Last Song (Official Video) | Napalm Records") == "One Last Song"
    assert clean_title("Song (Acoustic Version)") == "Song (Acoustic Version)"


def test_vol20_tracks_come_out_clean():
    plan = build_plan(Collection.from_dict(json.loads((FIXTURES / "vol20_collection.json").read_text())))
    assert [(t.artist, t.title) for t in plan.tracks] == [
        ("Saltatio Mortis", "Wo sind die Clowns? (Orchesterversion)"),  # unbracketed "– Official Lyric Video"
        ("Wardruna", "Helvegen (Live)"),  # lowercase channel loses; "Official … Video" stripped, "Live" kept
        ("Letzte Instanz", "Winterträne"),
        ("Subway To Sally", "So Rot"),
        ("Mr. Hurley & die Pulveraffen", "Blau wie das Meer Version 2017"),
        ("Versengold", "Niemals sang- und klanglos"),  # German "(Offizielles Video)"
        ("In Extremo", "Sternhagelvoll"),  # "(Official 360 Grad Video)"
        ("Faun", "Von den Elben 2003"),
        ("Schandmaul", "Der Teufel hat den Schnaps gemacht . . ."),
        ("ASP", "Schneefall In Der Hölle (Plakat Mix) [MASKENHAFT-Ein Versinken in elf Bildern]"),
        ("Empyrium", "The Ensemble Of Silence"),
        ("Ulver", "Eos"),
    ]
    assert [s["video_id"] for s in plan.skipped][1] and "age" in plan.skipped[1]["reason"]  # Feuerschwanz


def test_decomposed_unicode_is_composed():
    # the fixture's ASP title really contains "o" + U+0308 (YouTube sends it that way)
    assert parse_video_title("ASP - Ho\u0308lle", None) == ("ASP", "H\u00f6lle")


@pytest.mark.parametrize(
    "title",
    ["Phantom (Music of the Night)", "Song (New Version)", "Track - Audio Slave Remix (Live)"],
)
def test_meaningful_brackets_and_segments_survive(title):
    assert clean_title(title) == title


# -- guest credits belong in the title, not in the artist field -----------------------------


@pytest.mark.parametrize(
    ("artist", "main", "guests"),
    [
        ("Feuerschwanz ft. Melissa Bonny", "Feuerschwanz", "ft. Melissa Bonny"),
        ("DOMINUM feat. Feuerschwanz", "DOMINUM", "feat. Feuerschwanz"),
        ("Van Canto featuring Victor Smolski", "Van Canto", "featuring Victor Smolski"),
        ("Eluveitie (feat. Anna Murphy)", "Eluveitie", "feat. Anna Murphy"),
        ("Sabaton", "Sabaton", None),  # nothing to move
        ("Simon & Garfunkel", "Simon & Garfunkel", None),  # a duo is not a guest credit
        ("feat. Melissa Bonny", "feat. Melissa Bonny", None),  # no main artist left: leave it alone
    ],
)
def test_split_feat(artist, main, guests):
    assert split_feat(artist) == (main, guests)


def test_move_feat_matches_the_users_example():
    assert move_feat("Feuerschwanz ft. Melissa Bonny", "Ding (SEEED Cover)") == (
        "Feuerschwanz",
        "Ding (SEEED Cover) ft. Melissa Bonny",
    )


@pytest.mark.parametrize(
    "title",
    ["The Dead Don't Die (feat. Feuerschwanz)", "Ding feat. Melissa Bonny", "Song (feat. X) [Live]"],
)
def test_move_feat_does_not_name_the_guest_twice(title):
    assert move_feat("DOMINUM feat. Feuerschwanz", title) == ("DOMINUM", title)


def test_plan_moves_the_guest_credit_into_the_title():
    collection = Collection.from_dict(
        {
            "source_url": "https://www.youtube.com/playlist?list=PLx",
            "source_id": "PLx",
            "is_playlist": True,
            "title": "Metalfest",
            "channel": "Metalfest",
            "thumbnail": None,
            "fetched_at": "2026-09-23T00:00:00",
            "entries": [
                {
                    "video_id": "aaaaaaaaaaa",
                    "position": 1,
                    "title": "Feuerschwanz ft. Melissa Bonny - Ding (SEEED Cover)",
                    "duration": 200,
                }
            ],
        }
    )
    t = build_plan(collection).tracks[0]
    assert (t.artist, t.title) == ("Feuerschwanz", "Ding (SEEED Cover) ft. Melissa Bonny")


# -- a dash does not always separate artist and title ---------------------------------------


@pytest.mark.parametrize(
    ("title", "channel", "parsed"),
    [
        # what follows the dash only labels the video: the whole text is the song
        ('"Mad World" (feat. Gary Jules) - Official Music Video', "Gary Jules Official", (None, "Mad World (feat. Gary Jules)")),
        ("Nachtblume - Official Lyric Video", "ASP", (None, "Nachtblume")),
        ("Bismarck - Official Music Video", "Sabaton", (None, "Bismarck")),
        # ...but a real title after the dash still is one
        ("Sabaton - Bismarck (Official Music Video)", "Sabaton", ("Sabaton", "Bismarck")),
        ("Wardruna - Helvegen - Live", "Wardruna", ("Wardruna", "Helvegen - Live")),
    ],
)
def test_video_label_after_the_dash_is_not_a_title(title, channel, parsed):
    assert parse_video_title(title, channel) == parsed


@pytest.mark.parametrize(
    ("artist", "title", "kept"),
    [
        ("Gary Jules", "Mad World (feat. Gary Jules)", "Mad World"),  # the guest is us
        ("Gary Jules", "Mad World feat. Gary Jules", "Mad World"),
        ("Feuerschwanz", "Ding ft. Melissa Bonny", "Ding ft. Melissa Bonny"),  # a real guest stays
        ("Mono Inc.", "Children of the Dark (feat. Tilo Wolff)", "Children of the Dark (feat. Tilo Wolff)"),
    ],
)
def test_strip_self_feat(artist, title, kept):
    assert strip_self_feat(artist, title) == kept
