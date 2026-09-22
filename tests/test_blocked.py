"""YouTube's bot check (seen live on 2026-09-22): partial data must never change anything."""

import json
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

import ytalbum.youtube as youtube_mod
from ytalbum import cli
from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.models import Collection
from ytalbum.plan import build_plan, merge_plans
from ytalbum.youtube import BOT_CHECK, YouTube, is_transient

FIXTURES = Path(__file__).parent.parent / "design-fixtures"
BOT = "ERROR: [youtube] j89ChkaWpi0: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication."


def vol1() -> Collection:
    return Collection.from_dict(json.loads((FIXTURES / "vol1_collection.json").read_text()))


class BotCheckedYoutubeDL:
    """Lists the playlist fine (as YouTube did), then refuses every video."""

    video_requests = 0

    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if "list=" in url:
            return json.loads((FIXTURES / "vol1.json").read_text())
        type(self).video_requests += 1
        raise DownloadError(BOT)


@pytest.mark.parametrize(
    ("message", "transient"),
    [
        (BOT, True),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", True),
        ("ERROR: [youtube] x: Read timed out.", True),
        ("ERROR: [youtube] x: Video unavailable. This video is private", False),
        ("ERROR: [youtube] x: Sign in to confirm your age.", False),
    ],
)
def test_transient_or_not(message, transient):
    assert is_transient(message) is transient


def test_fetch_stops_asking_after_the_first_bot_check(monkeypatch):
    monkeypatch.setattr(youtube_mod, "YoutubeDL", BotCheckedYoutubeDL)
    BotCheckedYoutubeDL.video_requests = 0
    collection = YouTube(Config(concurrency=1)).fetch("https://www.youtube.com/playlist?list=PL1SYtzVzb0GJJ5KixpEZjWvXjV96uIyd3")
    assert BotCheckedYoutubeDL.video_requests == 1
    assert len(collection.unreadable) == 14
    assert all(e.skipped == BOT_CHECK for e in collection.entries)


def test_partial_data_changes_nothing_on_disk(tmp_path, monkeypatch, capsys):
    # an album that is already in the library
    plan = build_plan(vol1())
    album_dir = tmp_path / plan.folder
    save_plan(plan, album_dir)
    before = (album_dir / ".ytalbum.json").read_text()

    class BlockedYT:
        cfg = Config(musicbrainz=False)

        def fetch(self, url):
            c = vol1()
            for e in c.entries[1:]:
                e.skipped, e.transient = BOT_CHECK, True
            return c

    code = cli._fetch_one(plan.source_url, tmp_path, BlockedYT(), "fetch", False)
    assert code == cli.BLOCKED
    assert "Nothing was changed" in capsys.readouterr().err
    assert (album_dir / ".ytalbum.json").read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["My Dark Lullabies"]


def test_skipped_videos_are_still_in_the_source():
    existing = build_plan(vol1())
    fresh_coll = vol1()
    fresh_coll.entries[4].skipped = "Video unavailable"  # Schandmaul, e.g. private for a while
    merged = merge_plans(existing, build_plan(fresh_coll))
    assert next(t for t in merged.tracks if t.video_id == "Lkrs1eggmBg").in_source


def test_download_stops_at_the_first_bot_check(tmp_path):
    class BlockedDownloads:
        calls = 0

        def download_audio(self, video_id, dest_dir):
            self.calls += 1
            raise DownloadError(BOT)

        def fetch_bytes(self, url):
            raise OSError("offline")

    yt = BlockedDownloads()
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, yt)
    assert yt.calls == 1  # no retry, no further tracks
    saved = load_plan(tmp_path / plan.folder)
    assert saved.tracks[0].error == BOT_CHECK
    assert all(t.state == "pending" for t in saved.tracks[1:])
