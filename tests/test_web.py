"""Slice 7: the web UI's HTTP layer, on a real server with a temporary library."""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from mutagen.oggopus import OggOpus

from test_incremental import JPEG, FakeYouTube, opus_template, vol1  # noqa: F401 (fixture)
from ytalbum.config import Config
from ytalbum.download import load_plan, run
from ytalbum.models import Provenance
from ytalbum.plan import build_plan
from ytalbum.service import Service, apply_user_edits
from ytalbum.web import App

HDR = {"X-Ytalbum": "1", "Content-Type": "application/json"}


@pytest.fixture
def library(tmp_path, opus_template):
    plan = build_plan(vol1())
    run(plan, tmp_path / plan.folder, FakeYouTube(opus_template))
    return tmp_path


@pytest.fixture
def server(library, opus_template):
    yt = FakeYouTube(opus_template)
    app = App(Config(musicbrainz=False), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False), library, log=job.log.append, yt=yt))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with httpx.Client(base_url=base, timeout=10) as client:
        yield app, client
    srv.shutdown()


def wait(client, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/job?id={job_id}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_static_and_state(server):
    _, c = server
    page = c.get("/")
    assert "ytalbum" in page.text and page.headers["cache-control"] == "no-store"
    assert '"/app.js?v=' in page.text and '"/style.css?v=' in page.text  # content-hashed: never stale
    assert "renderSettings" in c.get("/app.js?v=whatever").text
    assert c.get("/manifest.webmanifest").headers["content-type"] == "application/manifest+json"
    state = c.get("/api/state").json()
    assert [(a["albumartist"], a["album"], a["done"]) for a in state["albums"]] == [("My Dark Lullabies", "Vol. 1 - Heavy Sleeping", 13)]


def test_cover_only_by_album_id(server):
    _, c = server
    album_id = c.get("/api/state").json()["albums"][0]["id"]
    r = c.get(f"/api/cover?id={album_id}")
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/cover?id=../../../etc/passwd").status_code == 404
    assert c.get("/api/album?id=nope").status_code == 404


def test_writes_need_the_header(server):
    _, c = server
    assert c.post("/api/update", json={}).status_code == 403  # plain cross-site-able POST
    assert c.post("/api/update", content=b"{}", headers={"X-Ytalbum": "1", "Content-Type": "text/plain"}).status_code == 403
    assert c.request("OPTIONS", "/api/update").status_code == 403


def test_foreign_host_is_refused(server):
    _, c = server
    assert c.get("/api/state", headers={"Host": "evil.example:8765"}).status_code == 403
    assert c.get("/api/state", headers={"Host": "localhost:8765"}).status_code == 200


def test_bad_requests(server):
    _, c = server
    assert c.post("/api/open", json={"q": ""}, headers=HDR).status_code == 400
    assert c.post("/api/nope", json={}, headers=HDR).status_code == 400
    assert c.post("/api/fetch", json={"urls": ["file:///etc/passwd"]}, headers=HDR).status_code == 400


def test_job_labels_name_the_album(server):
    app, c = server
    url = c.get(f"/api/album?id={c.get('/api/state').json()['albums'][0]['id']}").json()["source_url"]
    assert app.describe(url) == "My Dark Lullabies — Vol. 1 - Heavy Sleeping"
    assert app.describe("https://www.youtube.com/playlist?list=PLnew") == "youtube.com/playlist?list=PLnew"


def test_edit_round_trip_renames_retags_and_marks_the_users_values(server, library):
    _, c = server
    album_id = c.get("/api/state").json()["albums"][0]["id"]
    plan = c.get(f"/api/album?id={album_id}").json()
    edits = {"album": "Heavy Sleeping", "tracks": [{"video_id": plan["tracks"][10]["video_id"], "artist": "Ashley Serena", "title": "Lullaby of Woe"}]}
    r = c.post("/api/edit", json={"id": album_id, "edits": edits}, headers=HDR)
    assert r.status_code == 202
    job = wait(c, r.json()["job"]["id"])
    assert job["state"] == "done", job["log"]

    album_dir = library / "My Dark Lullabies" / "Heavy Sleeping"
    saved = load_plan(album_dir)
    assert saved.provenance["album"] == Provenance.USER
    assert saved.tracks[10].provenance == {"artist": Provenance.USER, "title": Provenance.USER}
    f = album_dir / "My Dark Lullabies - Heavy Sleeping - 11 - Ashley Serena - Lullaby of Woe.opus"
    assert OggOpus(f)["album"] == ["Heavy Sleeping"]
    assert not (library / "My Dark Lullabies" / "Vol. 1 - Heavy Sleeping").exists()


def test_apply_user_edits_ignores_blanks_and_unknown_tracks():
    plan = build_plan(vol1())
    apply_user_edits(plan, {"album": "  ", "year": "abc", "tracks": [{"video_id": "nope", "title": "x"}, {"video_id": plan.tracks[0].video_id, "title": ""}]})
    assert plan.album == "Vol. 1 - Heavy Sleeping" and plan.year is None
    assert "album" not in plan.provenance or plan.provenance["album"] != Provenance.USER
    assert plan.tracks[0].provenance["title"] != Provenance.USER


def test_browser_setting(server, monkeypatch, tmp_path):
    app, c = server
    monkeypatch.setattr("ytalbum.config.detect_browsers", lambda: ["firefox", "chrome"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert c.get("/api/state").json()["settings"]["browsers"] == ["firefox", "chrome"]
    assert c.post("/api/settings", json={"cookies_from_browser": "netscape"}, headers=HDR).status_code == 400
    r = c.post("/api/settings", json={"cookies_from_browser": "firefox"}, headers=HDR)
    assert r.json()["cookies_from_browser"] == "firefox" and app.cfg.cookies_from_browser == "firefox"
    assert 'cookies_from_browser = "firefox"' in (tmp_path / "cfg" / "ytalbum" / "config.toml").read_text()
    assert c.post("/api/settings", json={"cookies_from_browser": "firefox"}).status_code == 403  # header still required


def test_audio_streams_with_ranges_and_only_known_tracks(server):
    _, c = server
    plan = c.get(f"/api/album?id={c.get('/api/state').json()['albums'][0]['id']}").json()
    url = f"/api/audio?id={plan['source_id']}&v={plan['tracks'][0]['video_id']}"
    full = c.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/ogg" and full.headers["accept-ranges"] == "bytes"
    part = c.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == full.content[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(full.content)}"
    assert c.get(url, headers={"Range": "bytes=-5"}).content == full.content[-5:]
    assert c.get(url, headers={"Range": f"bytes={len(full.content) + 10}-"}).status_code == 416
    assert c.get(f"/api/audio?id={plan['source_id']}&v=../../etc/passwd").status_code == 404
    assert c.get("/api/audio?id=nope&v=x").status_code == 404
