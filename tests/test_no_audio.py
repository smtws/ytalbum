"""A missing audio-only format is not proof: YouTube withholds them now and then.

Seen on 2026-09-23: DOMINUM "One of Us" failed with "no separate audio stream", and a plain
re-fetch got the full-quality Opus. Skeeter Davis' 1963 upload fails every time — that one
really has only a 360p combined stream. Both look identical on the first attempt.
"""

from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

import ytalbum.youtube as youtube_mod
from ytalbum.config import Config
from ytalbum.youtube import NoAudioStream, YouTube

FORMAT_GONE = "ERROR: [youtube] abc: Requested format is not available. Use --list-formats for a list of available formats"


class FormatMissing:
    """Refuses the audio-only format `fails` times, then downloads normally."""

    def __init__(self, params, fails, calls):
        self.params, self.fails, self.calls = params, fails, calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if not download:  # describe_combined asks what is offered instead
            return {"formats": [{"acodec": "mp4a.40.2", "vcodec": "avc1", "height": 360, "abr": 96}]}
        self.calls.append(url)
        if len(self.calls) <= self.fails:
            raise DownloadError(FORMAT_GONE)
        template = self.params["outtmpl"]
        template = template["default"] if isinstance(template, dict) else template
        out = Path(template.replace("%(id)s", "abc").replace("%(ext)s", "opus"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"OggS-fake")
        return {"id": "abc"}


@pytest.fixture
def youtube(monkeypatch):
    yt = YouTube(Config(pot_mode="off"))
    monkeypatch.setattr(yt, "ensure_pot_server", lambda: None)
    return yt


def patch(monkeypatch, fails, calls):
    monkeypatch.setattr(youtube_mod, "YoutubeDL", lambda params: FormatMissing(params, fails, calls))


def test_one_refusal_is_retried_and_succeeds(youtube, monkeypatch, tmp_path):
    calls = []
    patch(monkeypatch, fails=1, calls=calls)
    path = youtube.download_audio("abc", tmp_path)
    assert path.exists() and path.suffix == ".opus"  # full quality, no question asked
    assert len(calls) == 2


def test_twice_is_taken_as_the_answer(youtube, monkeypatch, tmp_path):
    calls = []
    patch(monkeypatch, fails=2, calls=calls)
    with pytest.raises(NoAudioStream) as e:
        youtube.download_audio("abc", tmp_path)
    assert "360p" in str(e.value)  # says what is offered instead, for the user's choice
    assert len(calls) == 2  # asked twice, then gave up — no endless retrying


def test_other_errors_are_not_retried(youtube, monkeypatch, tmp_path):
    calls = []

    class Broken(FormatMissing):
        def extract_info(self, url, download=False):
            calls.append(url)
            raise DownloadError("ERROR: [youtube] abc: Video unavailable")

    monkeypatch.setattr(youtube_mod, "YoutubeDL", lambda params: Broken(params, 0, calls))
    with pytest.raises(DownloadError):
        youtube.download_audio("abc", tmp_path)
    assert len(calls) == 1
