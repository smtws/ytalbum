"""Tagging and download helpers — offline (ffmpeg generates the test audio)."""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

from ytalbum.download import cover_candidates
from ytalbum.models import AlbumPlan, Kind, PlanTrack
from ytalbum.tag import image_mime, tag_file
from ytalbum.youtube import best_thumbnail, entry_from_info

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 32


@pytest.fixture
def opus_file(tmp_path: Path) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "t.opus"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "libopus", "-metadata", "title=junk", str(path)],
        check=True,
    )
    return path


def make_plan(kind: Kind = Kind.COMPILATION, discs: int = 1) -> AlbumPlan:
    tracks = [
        PlanTrack(video_id=f"vid{i}", number=i, artist=f"A{i}", title=f"T{i}", filename=f"{i}.opus", provenance={}, disc=discs if i == 2 else 1)
        for i in (1, 2)
    ]
    return AlbumPlan(
        source_url="https://www.youtube.com/playlist?list=PLx",
        source_id="PLx",
        kind=kind,
        album="Vol. 1 - Heavy Sleeping",
        albumartist="My Dark Lullabies",
        year=None,
        cover_url=None,
        folder="x",
        tracks=tracks,
    )


def test_tags_and_cover_are_written(opus_file):
    plan = make_plan()
    tag_file(opus_file, plan, plan.tracks[0], cover=JPEG)
    tags = OggOpus(opus_file)
    assert tags["title"] == ["T1"]  # ffmpeg's "junk" title is gone
    assert tags["artist"] == ["A1"]
    assert tags["albumartist"] == ["My Dark Lullabies"]
    assert tags["album"] == ["Vol. 1 - Heavy Sleeping"]
    assert tags["tracknumber"] == ["1"]
    assert tags["tracktotal"] == ["2"]
    assert tags["compilation"] == ["1"]
    assert tags["youtube_id"] == ["vid1"]
    assert "discnumber" not in tags
    assert "date" not in tags
    pic = Picture(base64.b64decode(tags["metadata_block_picture"][0]))
    assert (pic.type, pic.mime, pic.data) == (3, "image/jpeg", JPEG)


def test_disc_number_only_for_multi_disc(opus_file):
    plan = make_plan(kind=Kind.OFFICIAL_ALBUM, discs=2)
    tag_file(opus_file, plan, plan.tracks[1])
    tags = OggOpus(opus_file)
    assert tags["discnumber"] == ["2"]
    assert "compilation" not in tags


def test_unknown_cover_bytes_are_not_embedded(opus_file):
    plan = make_plan()
    tag_file(opus_file, plan, plan.tracks[0], cover=b"<html>consent page</html>")
    assert "metadata_block_picture" not in OggOpus(opus_file)


def test_image_mime():
    assert image_mime(JPEG) == "image/jpeg"
    assert image_mime(b"\x89PNG\r\n") == "image/png"
    assert image_mime(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert image_mime(b"GIF89a") is None


def test_cover_candidates_prefer_maxres():
    url = "https://i.ytimg.com/vi/0gr0bwQgTSo/hqdefault.jpg?sqp=abc"
    assert cover_candidates(url) == ["https://i.ytimg.com/vi/0gr0bwQgTSo/maxresdefault.jpg", url]
    assert cover_candidates("https://example.org/a.jpg") == ["https://example.org/a.jpg"]


def test_entry_from_info_joins_multiple_artists():
    e = entry_from_info({"id": "x" * 11, "title": "t", "artists": ["Dominum", "Feuerschwanz"], "track": "The Dead Don't Die"}, 2)
    assert e.music.artist == "Dominum, Feuerschwanz"
    assert e.position == 2


def test_best_thumbnail_prefers_preference_then_size():
    info = {"thumbnails": [{"url": "a", "width": 1280, "height": 720}, {"url": "b", "preference": 1, "width": 120, "height": 90}]}
    assert best_thumbnail(info) == "b"
    assert best_thumbnail({"thumbnail": "c"}) == "c"
