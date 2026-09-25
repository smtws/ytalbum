import json
from pathlib import Path

import pytest

from ytalbum.models import Collection, PlanTrack
from ytalbum.plan import build_plan, drop_album_name
from ytalbum.titles import (
    channel_artist,
    clean_title,
    drop_label,
    move_feat,
    parse_video_title,
    split_feat,
    strip_leading_artist,
    strip_self_feat,
    title_by_artist,
)

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


# -- shapes found in the library, where the uploader stood in as the artist -----------------


@pytest.mark.parametrize(
    ("title", "channel", "parsed"),
    [
        ("NEBELUNG 'Mittwinter'", "ToTSweden", ("NEBELUNG", "Mittwinter")),  # single quotes
        ("Fever Ray 'If I Had A Heart'", "Fever Ray", ("Fever Ray", "If I Had A Heart")),
        ("In The Nursery ‎– Compulsion", "Zoo", ("In The Nursery", "Compulsion")),  # bidi mark
        ("Arcana- Innocent Child", "Angelheart", ("Arcana", "Innocent Child")),  # dash, no space
        ("Metallica: Nothing Else Matters", "Metallica", ("Metallica", "Nothing Else Matters")),
    ],
)
def test_the_artist_hidden_in_the_title_is_found(title, channel, parsed):
    assert parse_video_title(title, channel) == parsed


def test_a_german_compound_ellipsis_is_not_a_separator():
    # "sang- und klanglos" is one word pair; a lowercase continuation never starts a title
    assert clean_title("Niemals sang- und klanglos") == "Niemals sang- und klanglos"
    assert parse_video_title("Versengold - Niemals sang- und klanglos", "Versengold") == (
        "Versengold",
        "Niemals sang- und klanglos",
    )


def test_title_by_artist_reads_the_credit_at_the_end():
    assert title_by_artist("No Sound But The Wind by The Editors") == ("The Editors", "No Sound But The Wind")
    assert title_by_artist("Tyrs Återkomst (The Return of Tyr) by Hindarfjäll") == (
        "Hindarfjäll",
        "Tyrs Återkomst (The Return of Tyr)",
    )
    assert title_by_artist("Nothing Else Matters") is None


def test_by_is_only_read_as_a_credit_when_nothing_else_names_the_artist():
    # a song may simply contain the word; the plan only applies it to channel-only entries
    made = {
        "source_url": "u", "source_id": "PLx", "is_playlist": True, "title": "Mix",
        "channel": "Some Channel", "thumbnail": None, "fetched_at": "2026-09-23T00:00:00",
        "entries": [
            {"video_id": "a" * 11, "position": 1, "title": "Motörhead - Killed by Death", "duration": 200},
            {"video_id": "b" * 11, "position": 2, "title": "No Sound But The Wind by The Editors", "duration": 200},
        ],
    }
    plan = build_plan(Collection.from_dict(made))
    assert (plan.tracks[0].artist, plan.tracks[0].title) == ("Motörhead", "Killed by Death")
    assert (plan.tracks[1].artist, plan.tracks[1].title) == ("The Editors", "No Sound But The Wind")


def test_strip_leading_artist():
    assert strip_leading_artist("Metallica", "Metallica: Nothing Else Matters") == "Nothing Else Matters"
    assert (
        strip_leading_artist("Ye Banished Privateers", "Ye Banished Privateers & Umeå Musiksällskap: Annabel")
        == "Annabel"
    )
    assert strip_leading_artist("Sabaton", "Bismarck") == "Bismarck"  # nothing to strip


# -- a resolution is a video fact; a remaster is an audio one -------------------------------


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("Aeternus (1080p)", "Aeternus"),
        ("Strange World HD 1080p", "Strange World"),
        ("Perséfone (Oficial) Full HD", "Perséfone"),  # the label word in another language
        ("Even In Death (Remastered 1080p)", "Even In Death (Remastered)"),  # keep the audio half
        # a remaster is real information about the recording and always stays
        ("Broken Heroes (Remaster)", "Broken Heroes (Remaster)"),
        ("Leif Erikson (2012 Remaster)", "Leif Erikson (2012 Remaster)"),
        ("My Last Breath (Remastered 2023)", "My Last Breath (Remastered 2023)"),
        # words that only label a video in company, not alone
        ("Life Is Full", "Life Is Full"),
        ("Full Moon", "Full Moon"),
        ("Video Killed the Radio Star", "Video Killed the Radio Star"),
    ],
)
def test_video_quality_markers_go_and_audio_facts_stay(raw, cleaned):
    assert clean_title(raw) == cleaned


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("U-Gra (Tagelharpa playthrough) / Napalm Records", "U-Gra (Tagelharpa playthrough)"),
        ("Song / Nuclear Blast Records", "Song"),
        ("Heilung | Season of Mist", "Heilung"),  # the pipe form needs no evidence
        # a slash is part of plenty of real titles, so only publisher words justify cutting
        ("Intro / Outro", "Intro / Outro"),
        ("Wardruna / Skald", "Wardruna / Skald"),
        ("Highway to Hell (AC/DC cover)", "Highway to Hell (AC/DC cover)"),
        ("Working 24/7", "Working 24/7"),
    ],
)
def test_publisher_suffixes_are_dropped(raw, cleaned):
    assert clean_title(raw) == cleaned


# -- an album name repeated in every track is a label, not part of the songs ----------------


def album_with(album, titles):
    tracks = [
        PlanTrack(video_id=f"{i:011d}", number=i, artist="A", title=t, filename="", provenance={}, auto={"title": t})
        for i, t in enumerate(titles, 1)
    ]
    return album, tracks


def test_audio_play_parts_lose_the_repeated_release_name():
    album, tracks = album_with(
        "Folge 1: Der Kuss des Kometen",
        ["1 - Der Kuss des Kometen (Intro)"] + [f"1 - Der Kuss des Kometen (Teil {n:02d})" for n in range(1, 30)],
    )
    assert drop_album_name(album, tracks) == 30
    assert [t.title for t in tracks][:3] == ["Intro", "Teil 01", "Teil 02"]
    assert tracks[0].auto["title"] == "Intro"  # the derived value moves too, or a merge undoes it


def test_case_and_number_prefixes_do_not_matter():
    album, tracks = album_with(
        "Folge 8: beim Lass Knacken-Festival",
        [f"8 - Beim Lass Knacken-Festival (Teil {n:02d})" for n in range(1, 6)],
    )
    assert drop_album_name(album, tracks) == 5
    assert [t.title for t in tracks] == [f"Teil {n:02d}" for n in range(1, 6)]


def test_a_lone_title_track_keeps_its_name():
    # the trap: stripping per title would turn this into "Swedish version"
    album, tracks = album_with(
        "Carolus Rex",
        ["Carolus Rex (Swedish version)", "The Lion From the North", "Gott mit uns", "A Lifetime of War"],
    )
    assert drop_album_name(album, tracks) == 0
    assert tracks[0].title == "Carolus Rex (Swedish version)"


def test_a_single_word_album_never_strips():
    album, tracks = album_with("Methämmer", ["Methämmer", "Methämmer (live)", "Methämmer (radio edit)"])
    assert drop_album_name(album, tracks) == 0


def test_the_release_name_is_taken_out_wherever_it_sits():
    """Folge 4-8 put it in the middle and the number at the end: 'Kapitel 01: <name> (Folge 4)'."""
    album, tracks = album_with(
        "Folge 4: Die Hexenmeister des Metal",
        ["Intro: Die Hexenmeister des Metal (Folge 4)"]
        + [f"Kapitel {n:02d}: Die Hexenmeister des Metal (Folge 4)" for n in range(1, 6)],
    )
    assert drop_album_name(album, tracks) == 6
    assert [t.title for t in tracks] == ["Intro"] + [f"Kapitel {n:02d}" for n in range(1, 6)]


def test_sharp_s_survives_the_comparison():
    """casefold() maps 'ß' to 'ss', which silently stopped Folge 2 from matching."""
    album, tracks = album_with(
        "Folge 2: Auf großer Tour", [f"2 - Auf großer Tour (Teil {n:02d})" for n in range(1, 5)]
    )
    assert drop_album_name(album, tracks) == 4
    assert [t.title for t in tracks] == [f"Teil {n:02d}" for n in range(1, 5)]


def test_no_empty_brackets_are_left_behind():
    """A live album names itself inside the brackets: '(Live in Hamburg)' must go whole."""
    album, tracks = album_with(
        "Live in Hamburg",
        ["Louder Than Hell (Live in Hamburg)", "Funeral Song (Live in Hamburg)", "Seligkeit (Live in Hamburg)"],
    )
    assert drop_album_name(album, tracks) == 3
    assert [t.title for t in tracks] == ["Louder Than Hell", "Funeral Song", "Seligkeit"]
    assert not any("()" in t.title for t in tracks)


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        # the pipe inside the bracket is part of the text; cutting there left it hanging open
        (
            "Der Derwisch 🌀 Epic Fantasy Ambience (Saltatio Mortis | Reading, Yoga & RPG Music)",
            "Der Derwisch 🌀 Epic Fantasy Ambience (Saltatio Mortis | Reading, Yoga & RPG Music)",
        ),
        ("Song (Live | 2024) | Napalm Records", "Song (Live | 2024)"),  # outside it still cuts
        ("Heilung | Season of Mist", "Heilung"),
    ],
)
def test_a_pipe_only_ends_the_title_outside_brackets(raw, cleaned):
    assert clean_title(raw) == cleaned


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("Viva Vendetta | Napalm Records", "Viva Vendetta"),
        ("Judas (Deluxe Version) | Napalm Records", "Judas (Deluxe Version)"),
        ("Legends", "Legends"),  # nothing to drop
        ("Der Derwisch (Saltatio Mortis | Reading, Yoga & RPG Music)", "Der Derwisch (Saltatio Mortis | Reading, Yoga & RPG Music)"),
        ("Album - Chronik | Some Media", "Album - Chronik"),
    ],
)
def test_drop_label(raw, want):
    assert drop_label(raw) == want
