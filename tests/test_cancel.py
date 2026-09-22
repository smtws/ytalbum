"""Cancelling jobs: never half-done, finished work is kept."""

import json
import threading
import time
from pathlib import Path

import pytest

from test_incremental import FakeYouTube, opus_template, vol1  # noqa: F401 (fixture)
from ytalbum.config import Config
from ytalbum.download import load_plan
from ytalbum.models import SourceRef
from ytalbum.plan import build_plan
from ytalbum.service import Service
from ytalbum.web import Jobs
from ytalbum.youtube import Cancelled


class SlowFake(FakeYouTube):
    """Downloads 'take a while'; cancels itself after `after` downloads."""

    def __init__(self, template, cancel, after):
        super().__init__(template)
        self.cancel, self.after = cancel, after

    def download_audio(self, video_id, dest_dir, choice="best"):
        out = super().download_audio(video_id, dest_dir)
        if len(self.downloads) == self.after:
            self.cancel.set()
        return out


def test_cancel_stops_between_tracks_and_keeps_what_is_done(tmp_path, opus_template):
    cancel = threading.Event()
    yt = SlowFake(opus_template, cancel, after=3)
    plan = build_plan(vol1())
    service = Service(Config(musicbrainz=False), tmp_path, yt=yt, cancel=cancel)
    with pytest.raises(Cancelled):
        service.execute(plan, tmp_path / plan.folder)
    saved = load_plan(tmp_path / plan.folder)
    assert [t.state for t in saved.tracks[:3]] == ["done"] * 3
    assert all(t.state == "pending" for t in saved.tracks[3:])
    assert len(list((tmp_path / plan.folder).glob("*.opus"))) == 3


def test_cancel_ends_a_multi_source_job_instead_of_skipping_one_source(tmp_path):
    cancel = threading.Event()
    calls = []

    class Svc(Service):
        def fetch(self, url, **kw):
            calls.append(url)
            cancel.set()
            self.check()

    refs = [SourceRef(url=f"u{i}", source_id=f"s{i}", title=f"t{i}", tab="search") for i in range(3)]
    with pytest.raises(Cancelled):
        Svc(Config(musicbrainz=False), tmp_path, yt=object(), cancel=cancel).fetch_many(refs)
    assert calls == ["u0"]


def wait(job, timeout=5):
    end = time.time() + timeout
    while job.state in ("queued", "running") and time.time() < end:
        time.sleep(0.02)
    return job


def test_queued_job_never_runs_and_running_job_reports_cancelled(tmp_path):
    started = threading.Event()
    ran = []

    def slow(service):
        started.set()
        while True:
            service.check()
            time.sleep(0.01)

    jobs = Jobs(lambda job: Service(Config(musicbrainz=False), tmp_path, yt=object(), cancel=job.cancel))
    first = jobs.submit("x", "slow", slow)
    second = jobs.submit("x", "second", lambda s: ran.append(1))
    started.wait(2)
    jobs.cancel(second.id)
    assert second.state == "cancelled"
    jobs.cancel(first.id)
    assert wait(first).state == "cancelled"
    time.sleep(0.1)
    assert ran == [] and second.state == "cancelled"


def test_a_search_runs_while_a_download_is_running(tmp_path):
    """The write lane is serialized; reading jobs must not wait for it."""
    downloading = threading.Event()
    release = threading.Event()
    jobs = Jobs(lambda job: Service(Config(musicbrainz=False), tmp_path, yt=object(), cancel=job.cancel))

    def long_download(service):
        downloading.set()
        release.wait(5)

    fetch = jobs.submit("fetch", "download", long_download)
    downloading.wait(2)
    search = jobs.submit("search", "search", lambda s: "results")
    wait(search, timeout=3)
    assert (search.state, search.result) == ("done", "results")
    assert fetch.state == "running"  # still going, untouched
    assert jobs.busy("write") and not jobs.busy("read")
    release.set()
    assert wait(fetch).state == "done"


def test_saving_waits_for_the_running_download(tmp_path):
    order = []
    release = threading.Event()
    jobs = Jobs(lambda job: Service(Config(musicbrainz=False), tmp_path, yt=object(), cancel=job.cancel))
    first = jobs.submit("fetch", "download", lambda s: (order.append("download"), release.wait(5)))
    time.sleep(0.05)
    second = jobs.submit("edit", "save", lambda s: order.append("save"))
    time.sleep(0.2)
    assert order == ["download"]  # the save waits: both change the library
    release.set()
    wait(first), wait(second)
    assert order == ["download", "save"]
