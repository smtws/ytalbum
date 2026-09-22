"""Slice 6: artist search — offline, modelled on the live Faun search of 2026-09-22."""

import json
from pathlib import Path

from ytalbum.models import SourceRef
from ytalbum.search import dedupe_by_title, main_channel, search_artist
from ytalbum.youtube import ref_from_ytm_album

FIXTURES = Path(__file__).parent.parent / "design-fixtures"
FAUNTUBE = "https://www.youtube.com/channel/UCxWwz-uZkTNwEM_duLUrWkQ"


def ref(title, tab, source_id=None, artist=None, channel_url=None, count=None):
    sid = source_id or f"{tab}-{title}"
    return SourceRef(url=f"https://www.youtube.com/playlist?list={sid}", source_id=sid, title=title, tab=tab, artist=artist, channel_url=channel_url, count=count)


class FakeYouTube:
    def search_albums(self, query):
        return [
            ref("XV - Best Of (Deluxe Edition)", "ytmusic", "OLAK-xv", "fauntube", FAUNTUBE, 26),
            ref("Von den Elben", "ytmusic", "OLAK-elben", "fauntube", FAUNTUBE, 13),
            ref("HEX", "ytmusic", "OLAK-hex-2", "fauntube", FAUNTUBE, 12),
            ref("Faun", "ytmusic", "OLAK-other", "Facundo Mohrr - Topic", "https://www.youtube.com/channel/UCother", 3),
            ref("The Tale of the Faun", "ytmusic", "OLAK-tale", "Emperiom", "https://www.youtube.com/channel/UCemp", 3),
        ]

    def list_channel(self, url):
        assert url == FAUNTUBE
        return [
            ref("HEX", "releases", "OLAK-hex"),
            ref("Pagan", "releases", "OLAK-pagan"),
            ref("Liam", "releases", "OLAK-liam"),
            ref("FAUN - Official Videos", "playlists", "PL-videos"),
        ]

    def search_playlists(self, query):
        return [ref("Faun - Luna (Full album) - 2014", "search", "PL-luna", "Mel Satyria"), ref("FAUN - Official Videos", "search", "PL-videos", "fauntube")]


class FakeMB:
    def artist_albums(self, artist):
        return [{"title": "Pagan"}, {"title": "Luna"}, {"title": "Totem"}]


def test_finds_the_real_channel_even_when_it_is_not_named_like_the_artist():
    result = search_artist(FakeYouTube(), "Faun", FakeMB())
    assert result.channel_url == FAUNTUBE
    groups = dict(result.groups)
    assert [r.title for r in groups["Albums"]] == ["HEX", "Pagan", "XV - Best Of (Deluxe Edition)", "Von den Elben"]
    assert groups["Albums"][0].count == 12  # merged from the duplicate YouTube Music playlist
    assert [r.title for r in groups["Singles, EPs and other releases"]] == ["Liam"]
    assert [r.title for r in groups["Playlists on the artist's channel"]] == ["FAUN - Official Videos"]
    assert [r.title for r in groups["Other playlists found by search"]] == ["Faun - Luna (Full album) - 2014"]
    assert all("Facundo" not in (r.artist or "") and "Tale" not in r.title for r in result.refs)
    assert result.missing == ["Totem"]  # "Luna" was found as a fan playlist


def test_without_musicbrainz_the_track_count_decides():
    groups = dict(search_artist(FakeYouTube(), "Faun").groups)
    assert [r.title for r in groups["Albums"]] == ["HEX", "XV - Best Of (Deluxe Edition)", "Von den Elben"]
    assert [r.title for r in groups["Singles, EPs and other releases"]] == ["Pagan", "Liam"]


def test_curator_channel_found_through_its_playlists():
    class Curator(FakeYouTube):
        def search_albums(self, query):
            return []

        def search_playlists(self, query):
            return [ref("My Dark Lullabies Vol.15 - Sleep Distortion", "search", "PL15", "My Dark Lullabies", "https://www.youtube.com/@MyDarkLullabies")]

        def list_channel(self, url):
            assert url == "https://www.youtube.com/@MyDarkLullabies"
            return [ref(f"My Dark Lullabies Vol. {n}", "playlists", f"PL{n}") for n in (20, 15, 1)]

    result = search_artist(Curator(), "My Dark Lullabies")
    assert [r.source_id for r in dict(result.groups)["Playlists on the artist's channel"]] == ["PL20", "PL15", "PL1"]
    assert "Other playlists found by search" not in dict(result.groups)  # PL15 already listed


def test_no_channel_without_a_name_match():
    hits = FakeYouTube().search_albums("x")[3:]
    assert main_channel(hits, "Faun") is None


def test_dedupe_by_title_keeps_first_and_fills_gaps():
    hex_ytm = ref("Hex", "ytmusic", "b", count=12, channel_url=FAUNTUBE)
    hex_ytm.thumbnail = "https://i.ytimg.com/hex.jpg"
    out = dedupe_by_title([ref("HEX", "releases", "a"), hex_ytm, ref("Pagan", "releases", "c")])
    assert [(r.source_id, r.count, r.thumbnail) for r in out] == [("a", 12, "https://i.ytimg.com/hex.jpg"), ("c", None, None)]


def test_youtube_music_album_resolves_to_its_olak_playlist():
    r = ref_from_ytm_album(json.loads((FIXTURES / "ytm_album_faun_xv.json").read_text()))
    assert r.thumbnail and "/s_p/" not in r.thumbnail  # those 404 for many albums
    assert r.source_id.startswith("OLAK5uy_")
    assert r.title == "XV - Best Of (Deluxe Edition)"  # "Album - " prefix removed
    assert r.channel_url == FAUNTUBE
    assert r.count == 26
