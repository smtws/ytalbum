"""Slice 4: channel URLs — offline, from captured tab listings."""

import json
from pathlib import Path

import pytest

from ytalbum.cli import parse_pick
from ytalbum.youtube import channel_base_url, refs_from_tab

FIXTURES = Path(__file__).parent.parent / "design-fixtures"


@pytest.mark.parametrize(
    ("url", "base"),
    [
        ("https://www.youtube.com/@MyDarkLullabies", "https://www.youtube.com/@MyDarkLullabies"),
        ("https://www.youtube.com/@Sabaton/playlists", "https://www.youtube.com/@Sabaton"),
        ("https://youtube.com/@Sabaton/releases/", "https://youtube.com/@Sabaton"),
        ("https://www.youtube.com/channel/UCjQhd1APsd5NQhiVZV7GYzg/videos?view=0", "https://www.youtube.com/channel/UCjQhd1APsd5NQhiVZV7GYzg"),
        ("https://music.youtube.com/channel/UCjQhd1APsd5NQhiVZV7GYzg", "https://music.youtube.com/channel/UCjQhd1APsd5NQhiVZV7GYzg"),
        ("https://www.youtube.com/playlist?list=PL1SYtzVzb0GJJ5KixpEZjWvXjV96uIyd3", None),
        ("https://www.youtube.com/watch?v=Lkrs1eggmBg", None),
        ("https://www.youtube.com/watch?v=Lkrs1eggmBg&list=PL1SYtzVzb0GJJ5KixpEZjWvXjV96uIyd3", None),
        ("https://www.youtube.com/@Sabaton/some-video-path/x", None),
    ],
)
def test_channel_base_url(url, base):
    assert channel_base_url(url) == base


def test_releases_tab_gives_official_album_playlists():
    refs = refs_from_tab(json.loads((FIXTURES / "sabaton_releases.json").read_text()), "releases")
    assert len(refs) == 46
    assert all(r.source_id.startswith("OLAK5uy_") for r in refs)
    legends = next(r for r in refs if r.title == "Legends")
    assert legends.url.endswith(legends.source_id)


def test_curator_playlists_tab():
    refs = refs_from_tab(json.loads((FIXTURES / "tab_playlists.json").read_text()), "playlists")
    assert [r.title for r in refs][-1] == "My Dark Lullabies Vol. 1 - Heavy Sleeping"
    assert len(refs) == 20


@pytest.mark.parametrize(
    ("spec", "picked"),
    [("1", [0]), ("1,3-5", [0, 2, 3, 4]), ("3-4, 1, 3", [2, 3, 0]), ("all", [0, 1, 2, 3, 4, 5]), ("", [])],
)
def test_parse_pick(spec, picked):
    assert parse_pick(spec, 6) == picked


@pytest.mark.parametrize("spec", ["0", "7", "2-9", "x"])
def test_parse_pick_rejects(spec):
    with pytest.raises(ValueError):
        parse_pick(spec, 6)
