"""Slice 7: the web UI's HTTP layer, on a real server with a temporary library."""

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from mutagen.oggopus import OggOpus
from test_incremental import JPEG, FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.models import Provenance
from ytalbum.plan import build_plan, refresh_derived
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


def test_settings_validate_then_apply(server, monkeypatch, tmp_path):
    app, c = server
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    st = c.get("/api/state").json()["settings"]
    assert {"library", "musicbrainz", "pot_mode", "pot_idle_minutes", "concurrency", "info"} <= set(st)
    assert c.post("/api/settings", json={"concurrency": 9}, headers=HDR).status_code == 400
    assert c.post("/api/settings", json={"pot_mode": "turbo"}, headers=HDR).status_code == 400
    assert c.post("/api/settings", json={"library": "relative/path"}, headers=HDR).status_code == 400
    new_lib = tmp_path / "Music"
    r = c.post("/api/settings", json={"concurrency": 1, "musicbrainz": False, "pot_idle_minutes": 10, "library": str(new_lib)}, headers=HDR)
    assert r.status_code == 200
    assert (app.cfg.concurrency, app.cfg.musicbrainz, app.cfg.pot_idle) == (1, False, 600)
    assert new_lib.is_dir() and app.library == new_lib and c.get("/api/state").json()["albums"] == []
    text = (tmp_path / "cfg" / "ytalbum" / "config.toml").read_text()
    assert "concurrency = 1" in text and "musicbrainz = false" in text and f'library_root = "{new_lib}"' in text


def test_thumbnails_are_proxied_only_from_allowed_hosts(server, monkeypatch):
    app, c = server
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    app.http = httpx.Client(transport=httpx.MockTransport(handler))
    ok = "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    r = c.get("/api/thumb", params={"u": ok})
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert c.get("/api/thumb", params={"u": ok}).status_code == 200 and len(calls) == 1  # cached
    for bad in ("https://evil.example/x.jpg", "http://i.ytimg.com/x.jpg", "file:///etc/passwd", "https://notytimg.com/x.jpg", ""):
        assert c.get("/api/thumb", params={"u": bad}).status_code == 404
    assert len(calls) == 1  # never fetched anything outside the allowlist


def test_details_runner_fills_counts_in_the_background(server):
    app, c = server
    resolved = []

    class FakeYT:
        def playlist_details(self, url):
            resolved.append(url)
            return {"count": 13, "thumbnail": "https://i.ytimg.com/vi/x/hq.jpg", "title": "T"}

    app.details._youtube = lambda: FakeYT()
    app.details.PAUSE = 0
    refs = [{"id": "PL1", "url": "https://www.youtube.com/playlist?list=PL1"}]
    assert c.post("/api/details", json={"refs": refs}, headers=HDR).json() == {}  # queued, nothing known yet
    for _ in range(100):
        got = c.post("/api/details", json={"refs": refs}, headers=HDR).json()
        if got:
            break
        time.sleep(0.05)
    assert got["PL1"]["count"] == 13
    assert resolved == ["https://www.youtube.com/playlist?list=PL1"]  # asked once, then cached


def test_details_ignores_foreign_urls_and_oversized_requests(server):
    app, c = server
    app.details._youtube = lambda: pytest.fail("must not fetch")
    assert c.post("/api/details", json={"refs": [{"id": "x", "url": "https://evil.example/p"}]}, headers=HDR).json() == {}
    assert c.post("/api/details", json={"refs": [{"id": str(i), "url": "https://www.youtube.com/playlist?list=x"} for i in range(201)]}, headers=HDR).status_code == 400


def test_albums_sort_naturally():
    from ytalbum.titles import natural_key

    volumes = [f"Vol. {n} - x" for n in (1, 2, 10, 11, 20, 3)]
    assert [v.split(" - ")[0] for v in sorted(volumes, key=natural_key)] == ["Vol. 1", "Vol. 2", "Vol. 3", "Vol. 10", "Vol. 11", "Vol. 20"]
    # mixed spellings and case still land in the right place
    mixed = ["Vol.9 - a", "Vol. 10 - b", "vol. 2 - c"]
    assert [m.split(" - ")[0] for m in sorted(mixed, key=natural_key)] == ["vol. 2", "Vol.9", "Vol. 10"]


def test_library_grid_sorts_by_artist_then_year_then_name(tmp_path, opus_template, monkeypatch):
    """Artist, then chronological; albums without a year keep their natural order."""
    made = [
        ("Sabaton", "The Great War", 2019),
        ("Sabaton", "Attero Dominatus", 2006),
        ("Sabaton", "Carolus Rex", 2012),
        ("My Dark Lullabies", "Vol. 10 - b", None),
        ("My Dark Lullabies", "Vol. 2 - a", None),
        ("My Dark Lullabies", "Vol. 1 - c", None),
        ("Mono Inc.", "Ravenblack", 2023),
        ("Mono Inc.", "Terlingua", None),  # no year: after the dated ones
    ]
    plan = build_plan(vol1())
    for artist, album, year in made:
        p = build_plan(vol1())
        p.source_id, p.albumartist, p.album, p.year = f"{artist}-{album}", artist, album, year
        refresh_derived(p)  # folder and filenames follow the changed fields
        run(p, tmp_path / p.folder, FakeYouTube(opus_template))

    app = App(Config(library_root=tmp_path), tmp_path)
    got = [(a["albumartist"], a["album"]) for a in app.albums() if (a["albumartist"], a["album"]) != (plan.albumartist, plan.album)]
    assert got == [
        ("Mono Inc.", "Ravenblack"),  # dated first
        ("Mono Inc.", "Terlingua"),
        ("My Dark Lullabies", "Vol. 1 - c"),  # all undated: natural order, unchanged
        ("My Dark Lullabies", "Vol. 2 - a"),
        ("My Dark Lullabies", "Vol. 10 - b"),
        ("Sabaton", "Attero Dominatus"),  # 2006, 2012, 2019 - not alphabetical
        ("Sabaton", "Carolus Rex"),
        ("Sabaton", "The Great War"),
    ]


def test_track_index_lets_the_ui_filter_by_song(library, opus_template):
    app = App(Config(library_root=library), library)
    index = app.track_index()
    plan = load_plan(next(library.glob("*/*/.ytalbum.json")).parent)
    assert index["fields"] == ["video_id", "artist", "title", "done", "trim_start", "trim_end"]
    assert index["albums"][plan.source_id] == [
        [t.video_id, t.artist, t.title, int(t.state == "done"), t.trim_start, t.trim_end] for t in plan.tracks
    ]
    assert index["version"] and app.track_index()["version"] == index["version"]  # cached


def test_track_index_version_follows_the_plans(library, opus_template):
    app = App(Config(library_root=library), library)
    before = app.track_index()
    album_dir = next(library.glob("*/*/.ytalbum.json")).parent
    plan = load_plan(album_dir)
    plan.tracks[0].title = "Renamed by hand"
    save_plan(plan, album_dir)
    after = app.track_index()
    assert after["version"] != before["version"]  # the UI refetches only when this changes
    assert after["albums"][plan.source_id][0][2] == "Renamed by hand"  # [video_id, artist, title, …]


def test_state_carries_the_track_index_version(library):
    app = App(Config(library_root=library), library)
    assert app.state()["tracks_version"] == app.track_index()["version"]


def test_the_disc_can_be_edited_and_each_disc_counts_from_one():
    plan = build_plan(vol1())
    edits = {"tracks": [{"video_id": t.video_id, "disc": "2"} for t in plan.tracks[6:]]}
    apply_user_edits(plan, edits)
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, n) for n in range(1, 7)] + [(2, n) for n in range(1, 8)]
    assert plan.tracks[6].filename.count(" - 2-01 - ") == 1


def test_putting_everything_back_on_one_disc_renumbers_straight_through():
    plan = build_plan(vol1())
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "2"} for t in plan.tracks[6:]]})
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "1"} for t in plan.tracks]})
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, n) for n in range(1, 14)]
    assert " - 1-01 - " not in plan.tracks[0].filename  # no disc prefix on a single-disc album


@pytest.mark.parametrize("value", ["", "0", "-3", "abc", None])
def test_nonsense_disc_values_are_ignored(value):
    plan = build_plan(vol1())
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "disc": value}]})
    assert plan.tracks[0].disc == 1
