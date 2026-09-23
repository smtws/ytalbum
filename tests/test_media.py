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


def test_only_the_performer_is_taken_from_youtubes_artist_list():
    # yt-dlp lists writers and producers too: "Feuerschwanz, Benjamin Metzner, Peter Henrici"
    e = entry_from_info({"id": "x" * 11, "title": "t", "artists": ["Feuerschwanz", "Peter Henrici"], "track": "Metvernichter"}, 2)
    assert e.music.artist == "Feuerschwanz"
    assert e.position == 2


def test_short_error_messages():
    from ytalbum.youtube import _short_error

    assert _short_error("ERROR: [youtube] abc: Sign in to confirm your age. Use --cookies…").startswith("age-restricted")
    assert _short_error("ERROR: [youtube] abc: Video unavailable. This video is private") == "Video unavailable"


def test_best_thumbnail_prefers_preference_then_size():
    info = {"thumbnails": [{"url": "a", "width": 1280, "height": 720}, {"url": "b", "preference": 1, "width": 120, "height": 90}]}
    assert best_thumbnail(info) == "b"
    assert best_thumbnail({"thumbnail": "c"}) == "c"


def test_pot_provider_is_only_used_when_built(tmp_path):
    from ytalbum.config import Config
    from ytalbum.youtube import YouTube

    home = tmp_path / "server"
    cfg = Config(pot_provider_home=home, js_runtime="node", pot_mode="script")
    assert cfg.resolved_pot_provider() is None
    assert "extractor_args" not in YouTube(cfg)._params()
    (home / "build").mkdir(parents=True)
    (home / "build" / "generate_once.js").write_text("")
    assert YouTube(cfg)._params()["extractor_args"] == {"youtubepot-bgutilscript": {"server_home": [str(home)]}}

    cfg.pot_mode = "server"  # server preferred, script kept as the plugin's fallback
    args = YouTube(cfg)._params()["extractor_args"]
    assert args["youtubepot-bgutilhttp"] == {"base_url": ["http://127.0.0.1:4416"]}
    assert args["youtubepot-bgutilscript"] == {"server_home": [str(home)]}

    cfg.pot_mode = "off"
    assert "extractor_args" not in YouTube(cfg)._params()


def test_album_art_prefers_the_signed_urls_over_the_biggest():
    from ytalbum.models import Entry
    from ytalbum.youtube import playlist_thumbnail

    tracks = [Entry(video_id="a", position=1, title="t", thumbnail="https://i.ytimg.com/vi/a/hq.jpg")]
    # YouTube lists the album art three times; only the signed ones work, the plain one 404s
    album = {"thumbnails": [
        {"url": "https://i9.ytimg.com/s_p/OLAK5uy_x/mqdefault.jpg?sqp=abc", "width": 180, "height": 180},
        {"url": "https://i9.ytimg.com/s_p/OLAK5uy_x/sddefault.jpg?sqp=abc", "width": 640, "height": 640},
        {"url": "https://i9.ytimg.com/s_p/OLAK5uy_x/maxresdefault.jpg", "width": 1200, "height": 1200},
    ]}
    assert playlist_thumbnail(album, tracks) == "https://i9.ytimg.com/s_p/OLAK5uy_x/sddefault.jpg?sqp=abc"
    only_dead = {"thumbnails": [{"url": "https://i9.ytimg.com/s_p/OLAK5uy_x/maxresdefault.jpg", "width": 1200, "height": 1200}]}
    assert playlist_thumbnail(only_dead, tracks) == "https://i.ytimg.com/vi/a/hq.jpg"  # a track's instead
    assert playlist_thumbnail({}, tracks) == "https://i.ytimg.com/vi/a/hq.jpg"
    assert playlist_thumbnail(only_dead, []) is None  # nothing usable at all


def m4a_file(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "t.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "aac", str(path)], check=True)
    return path


def test_m4a_gets_the_same_tags(tmp_path):
    from mutagen.mp4 import MP4

    path = m4a_file(tmp_path)
    plan = make_plan()
    plan.year = 2001
    plan.tracks[0].ext = "m4a"
    tag_file(path, plan, plan.tracks[0], cover=JPEG)
    tags = MP4(path)
    assert tags["\xa9nam"] == ["T1"] and tags["\xa9ART"] == ["A1"]
    assert tags["aART"] == ["My Dark Lullabies"] and tags["\xa9alb"] == ["Vol. 1 - Heavy Sleeping"]
    assert tags["trkn"] == [(1, 2)] and tags["\xa9day"] == ["2001"] and tags["cpil"] is True
    assert bytes(tags["covr"][0]) == JPEG
    assert tags["----:com.apple.iTunes:YOUTUBE_ID"][0] == b"vid1"


def test_filenames_follow_the_format(tmp_path):
    from ytalbum.plan import wanted_filename

    plan = make_plan()
    plan.tracks[0].ext = "m4a"
    assert wanted_filename(plan, plan.tracks[0]).endswith(" - A1 - T1.m4a")
