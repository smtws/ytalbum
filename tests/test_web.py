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
from ytalbum.download import iter_plans, load_plan, run, save_plan
from ytalbum.models import Collection, Provenance
from ytalbum.plan import build_plan, merge_plans, refresh_derived
from ytalbum.service import Service, apply_user_edits
from ytalbum.web import App

FIXTURES = Path(__file__).parent.parent / "design-fixtures"

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


def as_shown(plan, disc=None):
    """What the browser posts on save: every row, with the number and disc it shows."""
    return {"tracks": [{"video_id": t.video_id, "number": str(t.number),
                        "disc": str(disc if disc else t.disc)} for t in plan.tracks]}


def reordered(plan, sequence):
    return apply_user_edits(plan, {"tracks": [{"video_id": v, "number": n} for n, v in enumerate(sequence, 1)]})


def five():
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:5]
    return refresh_derived(plan)


def typed(plan, positions):
    """Post what the browser posts, with a number typed into some of the rows."""
    edits = as_shown(plan)
    for index, number in positions.items():
        edits["tracks"][index]["number"] = str(number)
    order = [t.video_id for t in plan.tracks]
    apply_user_edits(plan, edits)
    return [order.index(t.video_id) for t in plan.tracks]


@pytest.mark.parametrize(("positions", "expected"), [
    ({4: 3}, [0, 1, 4, 2, 3]),  # up, the case that always worked
    ({0: 3}, [1, 2, 0, 3, 4]),  # down: used to land on 2, one short of what was typed
    ({1: 4}, [0, 2, 3, 1, 4]),  # down: used to land on 3
    ({0: 5}, [1, 2, 3, 4, 0]),  # to the last position, which sorting could never reach
    ({4: 1}, [4, 0, 1, 2, 3]),  # to the first
    ({0: 5, 4: 1}, [4, 1, 2, 3, 0]),  # one down and one up in the same save
])
def test_a_typed_number_is_the_position_the_track_lands_on(positions, expected):
    """Sorting by the numbers can place a track before the one whose number it typed, never
    after it — so a move down always fell one short and the end was unreachable."""
    plan = five()
    assert typed(plan, positions) == expected
    assert [t.number for t in plan.tracks] == [1, 2, 3, 4, 5]
    assert plan.provenance["order"] == Provenance.USER


def test_collapsing_a_split_keeps_the_arrangement_the_user_set():
    """The numbers of a split are per disc, so sorting by them merges the discs like a zipper."""
    plan = build_plan(vol1())
    order = [t.video_id for t in reversed(plan.tracks)]
    reordered(plan, order)
    apply_user_edits(plan, as_shown(plan) | {"tracks": [{"video_id": t.video_id, "number": str(t.number),
                                                         "disc": "2" if i >= 6 else "1"}
                                                        for i, t in enumerate(plan.tracks)]})
    assert [t.video_id for t in plan.tracks] == order  # splitting moves nothing
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, n) for n in range(1, 7)] + [(2, n) for n in range(1, 8)]

    apply_user_edits(plan, as_shown(plan, disc=1))  # and putting it back on one disc moves nothing
    assert [t.video_id for t in plan.tracks] == order
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, n) for n in range(1, 14)]


def test_a_merge_and_a_renumber_in_one_save_put_the_track_where_it_was_asked():
    """A number the user *typed* counts in the disc they are putting the track on (§9.32).

    Changed in P15: it used to count inside the disc the track came from, so this track landed
    seventh — first of its former disc-2 block — after a save that collapsed both discs into one.
    Under one disc, "1" means first, and the same rule is what makes a row dragged into another
    disc land where it was dropped. A number left *alone* still counts where the track was, which
    is what keeps a plain collapse from interleaving the two discs (§9.22).
    """
    plan = build_plan(vol1())
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "2"} for t in plan.tracks[6:]]})
    was = [t.video_id for t in plan.tracks]
    moved = plan.tracks[9].video_id  # 2-04, asked to lead the album while the discs collapse
    edits = as_shown(plan, disc=1)
    for te in edits["tracks"]:
        if te["video_id"] == moved:
            te["number"] = "1"
    apply_user_edits(plan, edits)
    assert [t.video_id for t in plan.tracks] == [moved] + [v for v in was if v != moved]
    assert [t.number for t in plan.tracks] == list(range(1, 14))
    assert plan.provenance["order"] == Provenance.USER


def test_a_dropped_row_that_keeps_its_number_is_still_a_move():
    """2-02 dropped at 1-02 keeps the number 2, so only `moved` can say the user put it there."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:4]
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "2" if i >= 2 else "1"}
                                       for i, t in enumerate(plan.tracks)]})
    ids = [t.video_id for t in plan.tracks]
    dropped = ids[3]  # 2-02, dragged between 1-01 and 1-02 — where it is also number 2

    rows = [{"video_id": ids[0], "number": "1", "disc": "1"},
            {"video_id": dropped, "number": "2", "disc": "1", "moved": True},
            {"video_id": ids[1], "number": "3", "disc": "1"},
            {"video_id": ids[2], "number": "1", "disc": "2"}]
    apply_user_edits(plan, {"tracks": rows})
    assert [t.video_id for t in plan.tracks] == [ids[0], dropped, ids[1], ids[2]]
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, 1), (1, 2), (1, 3), (2, 1)]


def test_without_the_moved_flag_a_coincidental_number_is_no_move():
    """The flag is what makes it a move; the same payload without it leaves the row where it was."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:4]
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "2" if i >= 2 else "1"}
                                       for i, t in enumerate(plan.tracks)]})
    ids = [t.video_id for t in plan.tracks]
    rows = [{"video_id": ids[0], "number": "1", "disc": "1"},
            {"video_id": ids[3], "number": "2", "disc": "1"},
            {"video_id": ids[1], "number": "3", "disc": "1"},
            {"video_id": ids[2], "number": "1", "disc": "2"}]
    apply_user_edits(plan, {"tracks": rows})
    assert [t.video_id for t in plan.tracks][1] != ids[3]  # it went with its old disc's block


def test_a_row_dropped_into_another_disc_lands_where_it_was_dropped():
    """What the browser posts after a cross-disc drag: every row, in its new order and disc."""
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:4]
    apply_user_edits(plan, {"tracks": [{"video_id": t.video_id, "disc": "2" if i >= 2 else "1"}
                                       for i, t in enumerate(plan.tracks)]})
    ids = [t.video_id for t in plan.tracks]
    dropped = ids[2]  # 2-01, dragged between 1-01 and 1-02

    arrangement = [ids[0], dropped, ids[1], ids[3]]
    discs = {v: (2 if v == ids[3] else 1) for v in arrangement}
    counts: dict[int, int] = {}
    rows = []
    for v in arrangement:
        counts[discs[v]] = counts.get(discs[v], 0) + 1
        rows.append({"video_id": v, "number": str(counts[discs[v]]), "disc": str(discs[v]),
                     "moved": v == dropped})
    apply_user_edits(plan, {"tracks": rows})

    assert [t.video_id for t in plan.tracks] == arrangement
    assert [(t.disc, t.number) for t in plan.tracks] == [(1, 1), (1, 2), (1, 3), (2, 1)]


@pytest.mark.parametrize("value", ["", "0", "-3", "abc", None])
def test_nonsense_disc_values_are_ignored(value):
    plan = build_plan(vol1())
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "disc": value}]})
    assert plan.tracks[0].disc == 1


# -- lyrics -------------------------------------------------------------------------------


class FakeLyrics:
    """One lrclib answer for every track, or a list of candidates to be picked through."""

    LRC = "[00:01.00] one\n[00:04.00] two"
    SECOND = "[00:02.00] the other entry's words"

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.skipped: list[list[int]] = []
        self.candidates = [(11, self.LRC)]  # tests that need a second match extend this

    def get(self, artist, title, album=None, length=None, skip=()):
        from ytalbum.lyrics import Lyrics

        self.asked.append(title)
        self.skipped.append(list(skip))
        for entry_id, text in self.candidates:
            if entry_id not in skip:
                return Lyrics(synced=text, lrclib_id=entry_id)
        return None  # every candidate was rejected for this track

    def by_id(self, lrclib_id):
        from ytalbum.lyrics import Lyrics

        return Lyrics(synced=self.LRC, lrclib_id=lrclib_id)


@pytest.fixture
def lyrics_server(library, opus_template):
    """A server whose jobs find lyrics, so the button can be exercised offline."""
    yt = FakeYouTube(opus_template)
    api = FakeLyrics()
    app = App(Config(musicbrainz=False), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False), library, log=job.log.append, yt=yt, lrclib=api))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as client:
        yield app, client, api
    srv.shutdown()


def second_album(library, opus_template):
    """A second album in the library, so "this album" can be told from "the library"."""
    other = build_plan(Collection.from_dict(json.loads((FIXTURES / "vol20_collection.json").read_text())))
    run(other, library / other.folder, FakeYouTube(opus_template))
    return other


def test_the_fetch_lyrics_button_fills_one_album(lyrics_server, opus_template):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    other = second_album(app.library, opus_template)
    assert app.albums()[0]["lyrics"] == 0

    job = c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]
    assert wait(c, job["id"])["state"] == "done"
    assert len(api.asked) == len(app.album(album_id)[1].tracks)
    assert app.albums()[0]["lyrics"] == len(api.asked)  # the card can show the count
    # the other album was not touched: one button, one album
    assert all(t.lyrics is None for t in app.album(other.source_id)[1].tracks)

    # a second press asks nothing: every track has been looked up
    job = c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]
    assert wait(c, job["id"])["state"] == "done"
    assert len(api.asked) == len(app.album(album_id)[1].tracks)


def test_the_marker_reads_the_lrc_file_beside_the_track(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]

    got = c.get(f"/api/lyrics?id={album_id}&v={track.video_id}").json()
    assert got == {"status": "synced", "lrclib_id": 11, "text": FakeLyrics.LRC, "owner": None, "state": "done"}

    # the file is the original: remove it and the UI says so instead of showing a stale tag
    from ytalbum.lyrics import sidecar_path

    sidecar_path(app.album(album_id)[0], track.filename).unlink()
    assert c.get(f"/api/lyrics?id={album_id}&v={track.video_id}").json()["text"] == ""


def test_lyrics_of_an_unknown_track_are_not_found(server):
    app, c = server
    album_id = app.albums()[0]["id"]
    assert c.get(f"/api/lyrics?id={album_id}&v=nope").status_code == 404
    assert c.get("/api/lyrics?id=nope&v=nope").status_code == 404
    assert c.post("/api/lyrics", json={"id": "nope"}, headers=HDR).status_code == 400


def test_a_trimmed_track_can_be_played_from_its_untouched_original(server, tmp_path):
    """The trim points count from the start of the video, so the player needs that file.

    Without it the head is skipped twice: the file is already cut and the player cuts again.
    """
    from ytalbum.trim import apply as apply_trim

    app, c = server
    album_dir, plan = app.album(app.albums()[0]["id"])
    track = plan.tracks[0]
    track.trim_start = 0.2
    apply_trim(album_dir, track, album_dir / track.filename)
    save_plan(plan, album_dir)

    cut = app.audio_path(plan.source_id, track.video_id)
    uncut = app.audio_path(plan.source_id, track.video_id, original=True)
    assert cut != uncut
    assert uncut.parent.name == ".originals"
    assert c.get(f"/api/audio?id={plan.source_id}&v={track.video_id}&o=1").content == uncut.read_bytes()
    assert c.get(f"/api/audio?id={plan.source_id}&v={track.video_id}").content == cut.read_bytes()


def test_without_a_trim_there_is_no_original_and_the_file_is_served(server):
    app, c = server
    album_dir, plan = app.album(app.albums()[0]["id"])
    track = plan.tracks[0]
    assert app.audio_path(plan.source_id, track.video_id, original=True) == album_dir / track.filename


def test_the_grid_carries_the_length_flag(server):
    """The card badge is a count the server works out: the page never opens a file for it."""
    app, c = server
    album_dir, plan = app.album(app.albums()[0]["id"])
    assert app.albums()[0]["length"] is None

    for t in plan.tracks:  # a playlist of teasers: every track far shorter than the song
        t.file_length, t.mb_length = 40.0, 200.0
    save_plan(plan, album_dir)
    assert app.albums()[0]["length"] == {"way": "stub", "n": len(plan.tracks), "of": len(plan.tracks)}


# -- the lyrics editor (DESIGN.md §9.26) --------------------------------------------------


def saved(c, album_id, video_id, text):
    """POST what the editor posts, and wait for the write job."""
    job = c.post("/api/save_lyrics", json={"id": album_id, "video_id": video_id, "text": text}, headers=HDR).json()["job"]
    return wait(c, job["id"])


def sidecar_of(app, album_id, track):
    from ytalbum.lyrics import sidecar_path

    return sidecar_path(app.album(album_id)[0], track.filename)


def tagged_of(app, album_id, track):
    from ytalbum.tag import tagged_lyrics

    return tagged_lyrics(app.album(album_id)[0] / track.filename)


MINE = "[00:02.00] words of my own\n[00:09.00] second line"


def test_the_editor_writes_the_sidecar_the_mark_the_hash_and_the_tag(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    assert track.lyrics is None  # nothing looked up yet: the editor is the only writer here

    assert saved(c, album_id, track.video_id, MINE)["state"] == "done"
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert sidecar_of(app, album_id, fresh).read_text() == MINE + "\n"  # one trailing newline
    assert fresh.provenance["lyrics"] == "user"
    assert fresh.lyrics == "synced"  # derived from the text, not from anything lrclib said
    assert fresh.lyrics_sha and len(fresh.lyrics_sha) == 16
    assert tagged_of(app, album_id, fresh) == MINE  # the tag is a copy of the file
    assert api.asked == []  # nothing was looked up
    got = c.get(f"/api/lyrics?id={album_id}&v={fresh.video_id}").json()
    assert (got["owner"], got["status"], got["text"]) == ("user", "synced", MINE)


def test_plain_text_is_recognised_as_plain(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    saved(c, album_id, track.video_id, "just words\nno timestamps")
    assert next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id).lyrics == "plain"


def test_editing_over_lrclibs_words_makes_them_yours_and_a_refetch_keeps_them(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    assert track.lyrics_sha and "lyrics" not in track.provenance  # lrclib's, recorded as ours

    saved(c, album_id, track.video_id, MINE)
    wait(c, c.post("/api/lyrics", json={"id": album_id, "refetch": True}, headers=HDR).json()["job"]["id"])
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert sidecar_of(app, album_id, fresh).read_text() == MINE + "\n"  # survived --refetch
    assert fresh.provenance["lyrics"] == "user"
    assert tagged_of(app, album_id, fresh) == MINE


def test_clearing_drops_the_words_the_mark_and_the_tag(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    saved(c, album_id, track.video_id, MINE)

    assert saved(c, album_id, track.video_id, "   ")["state"] == "done"  # empty is a clear
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert not sidecar_of(app, album_id, fresh).exists()
    assert fresh.lyrics == "none" and fresh.lyrics_sha is None
    assert "lyrics" not in fresh.provenance
    assert tagged_of(app, album_id, fresh) is None


def test_a_refetch_brings_lrclibs_words_back_after_a_clear(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    saved(c, album_id, track.video_id, MINE)
    saved(c, album_id, track.video_id, "")

    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])  # a plain pass
    assert not sidecar_of(app, album_id, track).exists()  # a clear is not a request for new words
    wait(c, c.post("/api/lyrics", json={"id": album_id, "refetch": True}, headers=HDR).json()["job"]["id"])
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert sidecar_of(app, album_id, fresh).read_text().strip() == FakeLyrics.LRC
    assert "lyrics" not in fresh.provenance


def test_a_track_that_is_not_downloaded_has_nowhere_to_put_lyrics(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    album_dir, plan = app.album(album_id)
    plan.tracks[1].state = "pending"
    save_plan(plan, album_dir)

    r = c.post("/api/save_lyrics", json={"id": album_id, "video_id": plan.tracks[1].video_id, "text": MINE}, headers=HDR)
    assert r.status_code == 400
    assert "no file yet" in r.text
    assert not sidecar_of(app, album_id, plan.tracks[1]).exists()


def test_a_track_from_another_album_is_refused(lyrics_server, opus_template):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    other = second_album(app.library, opus_template)

    r = c.post("/api/save_lyrics", json={"id": album_id, "video_id": other.tracks[0].video_id, "text": MINE}, headers=HDR)
    assert r.status_code == 400
    assert "no such track" in r.text


def test_the_editor_refuses_while_a_job_holds_the_album(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    held = app.jobs.submit("lyrics", "a long pass", lambda s: time.sleep(2), target=album_id)

    r = c.post("/api/save_lyrics", json={"id": album_id, "video_id": track.video_id, "text": MINE}, headers=HDR)
    assert r.status_code == 400
    assert "a long pass" in r.text and "wait for it" in r.text
    wait(c, held.id)
    assert saved(c, album_id, track.video_id, MINE)["state"] == "done"  # and it works afterwards


def test_a_sidecar_the_editor_wrote_is_recognised_on_disk_without_a_special_case(lyrics_server):
    """reconcile() must see the editor's file as the user's, like any other file it finds."""
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    saved(c, album_id, track.video_id, MINE)

    album_dir = app.album(album_id)[0]
    sidecar_of(app, album_id, track).write_text(MINE + "\nand a line added on disk\n")
    wait(c, c.post("/api/lyrics", json={"id": album_id, "refetch": True}, headers=HDR).json()["job"]["id"])
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert "added on disk" in sidecar_of(app, album_id, fresh).read_text()
    assert fresh.provenance["lyrics"] == "user"
    assert "added on disk" in (tagged_of(app, album_id, fresh) or "")
    assert album_dir == app.album(album_id)[0]  # nothing moved


# -- per-track lyrics actions (P9, DESIGN.md §9.27) ---------------------------------------


def track_action(c, album_id, video_id, reject=False):
    body = {"id": album_id, "video_id": video_id, "reject": reject}
    r = c.post("/api/lyrics_track", json=body, headers=HDR)
    if r.status_code >= 400:
        return r
    return wait(c, r.json()["job"]["id"])


def test_looking_one_track_up_again_asks_only_for_that_track(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    asked_before = len(api.asked)
    track = app.album(album_id)[1].tracks[0]

    assert track_action(c, album_id, track.video_id)["state"] == "done"
    assert len(api.asked) == asked_before + 1  # one track, one question
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert (fresh.lyrics, fresh.lyrics_id) == ("synced", 11)
    assert sidecar_of(app, album_id, fresh).read_text().strip() == FakeLyrics.LRC


def test_looking_up_a_track_that_had_none_finds_words(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[2]
    assert track.lyrics is None and not sidecar_of(app, album_id, track).exists()

    track_action(c, album_id, track.video_id)
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert fresh.lyrics == "synced"
    assert tagged_of(app, album_id, fresh) == FakeLyrics.LRC
    assert all(t.lyrics is None for t in app.album(album_id)[1].tracks if t.video_id != track.video_id)


def test_rejecting_an_entry_takes_the_next_candidate(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    api.candidates = [(11, FakeLyrics.LRC), (12, FakeLyrics.SECOND)]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    assert track.lyrics_id == 11

    assert track_action(c, album_id, track.video_id, reject=True)["state"] == "done"
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert fresh.lyrics_rejected == [11]
    assert fresh.lyrics_id == 12  # the next best, and never #11 again
    assert sidecar_of(app, album_id, fresh).read_text().strip() == FakeLyrics.SECOND
    assert tagged_of(app, album_id, fresh) == FakeLyrics.SECOND
    assert api.skipped[-1] == [11]


def test_rejecting_the_only_candidate_leaves_no_words(lyrics_server):
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]

    track_action(c, album_id, track.video_id, reject=True)
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert (fresh.lyrics, fresh.lyrics_id, fresh.lyrics_rejected) == ("none", None, [11])
    assert not sidecar_of(app, album_id, fresh).exists()
    assert tagged_of(app, album_id, fresh) is None


def test_a_rejected_entry_stays_rejected_through_a_refetch(lyrics_server):
    """The entry is the wrong recording; that does not become untrue on the next pass."""
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    track_action(c, album_id, track.video_id, reject=True)

    wait(c, c.post("/api/lyrics", json={"id": album_id, "refetch": True}, headers=HDR).json()["job"]["id"])
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert fresh.lyrics_rejected == [11]
    assert fresh.lyrics == "none" and not sidecar_of(app, album_id, fresh).exists()
    assert [11] in api.skipped  # the pass was told to leave it out

    api.candidates = [(11, FakeLyrics.LRC), (12, FakeLyrics.SECOND)]  # a better entry appears later
    wait(c, c.post("/api/lyrics", json={"id": album_id, "refetch": True}, headers=HDR).json()["job"]["id"])
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert (fresh.lyrics_id, fresh.lyrics) == (12, "synced")  # taken, while #11 stays out


def test_the_actions_are_refused_for_words_of_the_users(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    saved(c, album_id, track.video_id, MINE)

    for reject in (False, True):
        r = track_action(c, album_id, track.video_id, reject=reject)
        assert r.status_code == 400 and "yours" in r.text
    assert sidecar_of(app, album_id, track).read_text() == MINE + "\n"


def test_rejecting_needs_something_to_reject(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    r = track_action(c, album_id, track.video_id, reject=True)
    assert r.status_code == 400 and "no lrclib match" in r.text


def test_the_per_track_actions_wait_for_a_job_on_the_album(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]
    held = app.jobs.submit("lyrics", "a long pass", lambda s: time.sleep(2), target=album_id)

    r = track_action(c, album_id, track.video_id)
    assert r.status_code == 400 and "a long pass" in r.text
    wait(c, held.id)
    assert track_action(c, album_id, track.video_id)["state"] == "done"


def test_a_track_with_no_file_cannot_be_matched(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    album_dir, plan = app.album(album_id)
    plan.tracks[1].state = "pending"
    save_plan(plan, album_dir)

    r = track_action(c, album_id, plan.tracks[1].video_id)
    assert r.status_code == 400 and "no file yet" in r.text


def test_lrclib_being_unreachable_is_not_reported_as_no_words(lyrics_server):
    """A site that did not answer must not read as an answer."""
    app, c, api = lyrics_server
    album_id = app.albums()[0]["id"]
    track = app.album(album_id)[1].tracks[0]

    def down(*a, **kw):
        from ytalbum.lyrics import LyricsError

        raise LyricsError("lrclib: HTTP 503 after 4 tries")

    api.get = down
    job = wait(c, c.post("/api/lyrics_track", json={"id": album_id, "video_id": track.video_id},
                         headers=HDR).json()["job"]["id"])
    assert job["state"] == "done"
    log = "\n".join(c.get(f"/api/job?id={job['id']}").json()["log"])
    assert "could not be reached" in log and "nothing lrclib has" not in log
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert fresh.lyrics is None  # not looked up, so the next pass asks again


# -- repair from the web UI (P10) ----------------------------------------------------------


def test_the_repair_button_runs_repair_on_the_write_lane(library, opus_template):
    """The fetch log tells people `ytalbum repair` unifies spellings; now they can click it."""
    shouting = build_plan(Collection.from_dict(json.loads((FIXTURES / "vol1_collection.json").read_text())))
    # a second volume of the same curator, shouted: two spellings of one artist key, which is
    # what repair unifies. (Not an "album": repair would then name it after its track artists.)
    shouting.source_id, shouting.album = "PL-shout", "Vol. 2 - Shouted"
    shouting.albumartist, shouting.provenance["albumartist"] = "MY DARK LULLABIES", Provenance.YT_TITLE
    refresh_derived(shouting)
    run(shouting, library / shouting.folder, FakeYouTube(opus_template))
    save_plan(shouting, library / shouting.folder)

    app = App(Config(musicbrainz=False), library, port=0)
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            job = c.post("/api/repair", json={}, headers=HDR).json()["job"]
            assert job["lane"] == "write" and job["kind"] == "repair"
            done = wait(c, job["id"])
            assert done["state"] == "done"
            log = "\n".join(c.get(f"/api/job?id={job['id']}").json()["log"])
            assert "album(s) tidied up" in log  # repair's own summary reaches the job log
            assert "MY DARK LULLABIES" in log or "My Dark Lullabies" in log
    finally:
        srv.shutdown()
    assert not (library / "MY DARK LULLABIES").exists()  # the folder was renamed on disk
    assert {p.albumartist for _, p in iter_plans(library)} == {"My Dark Lullabies"}


def test_repair_is_refused_while_another_write_runs(lyrics_server):
    app, c, _ = lyrics_server
    held = app.jobs.submit("lyrics", "a long pass", lambda s: time.sleep(2))

    r = c.post("/api/repair", json={}, headers=HDR)
    assert r.status_code == 400
    assert "a long pass" in r.text and "wait for it" in r.text
    wait(c, held.id)
    assert wait(c, c.post("/api/repair", json={}, headers=HDR).json()["job"]["id"])["state"] == "done"


def test_the_spelling_hint_names_both_ways_to_run_repair(tmp_path, opus_template):
    """One message for both kinds of user: the command and the button."""
    from test_repair import LOTL, settled

    _, log = settled(tmp_path, opus_template, [("LORD OF THE LOST", Provenance.YT_TITLE)], (LOTL, Provenance.MB))
    hint = next(line for line in log if "repair" in line)
    assert "ytalbum repair" in hint and "Repair library" in hint


# -- the fetch preview (P11, DESIGN.md §9.28) ---------------------------------------------


def previewing_server(library, opus_template, collection):
    """A server whose YouTube answers with one fixed collection, so a preview can be driven."""
    yt = FakeYouTube(opus_template)
    yt.fetch = lambda url: collection
    app = App(Config(musicbrainz=False), library, port=0,
              service_factory=lambda job: Service(Config(musicbrainz=False), library, log=job.log.append, yt=yt))
    srv = app.make_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return app, srv, yt


def single_collection():
    """S1's shape: a label suffix in the title, which the plan is expected to drop."""
    c = Collection.from_dict(json.loads((FIXTURES / "vol1_collection.json").read_text()))
    c.source_url, c.source_id, c.is_playlist = "https://www.youtube.com/watch?v=" + "p" * 11, "p" * 11, False
    c.entries = c.entries[:1]
    c.entries[0].duration = 200
    c.entries[0].music.album = None  # a label-channel upload has no YT Music album of its own
    c.title = "LORD OF THE LOST - Viva Vendetta (Official Video) | Napalm Records"
    c.channel = c.entries[0].channel = "Napalm Records"
    c.entries[0].title = c.title  # for a single the video *is* the collection; both say the same
    return c


def test_a_preview_runs_on_the_read_lane_and_writes_nothing(library, opus_template):
    app, srv, yt = previewing_server(library, opus_template, single_collection())
    before = sorted(p.name for p in library.iterdir())
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            job = c.post("/api/open", json={"q": single_collection().source_url}, headers=HDR).json()["job"]
            assert job["lane"] == "read" and job["kind"] == "preview"
            done = wait(c, job["id"])
            assert done["state"] == "done"
            plan = c.get(f"/api/job?id={job['id']}").json()["result"]["plan"]
    finally:
        srv.shutdown()
    assert plan["album"] == "Viva Vendetta"  # the label suffix and the noise bracket are gone
    assert plan["albumartist"] == "LORD OF THE LOST"
    assert plan["folder"].startswith("LORD OF THE LOST/")
    assert yt.downloads == []  # nothing fetched
    assert sorted(p.name for p in library.iterdir()) == before  # and no folder made


def test_what_the_preview_shows_is_what_the_fetch_writes(library, opus_template):
    """The preview is the outcome: same code path, `dry` being the only difference."""
    collection = single_collection()
    app, srv, _ = previewing_server(library, opus_template, collection)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            job = c.post("/api/open", json={"q": collection.source_url}, headers=HDR).json()["job"]
            wait(c, job["id"])
            shown = c.get(f"/api/job?id={job['id']}").json()["result"]["plan"]

            fetched = c.post("/api/fetch", json={"urls": [collection.source_url]}, headers=HDR).json()["job"]
            assert wait(c, fetched["id"])["state"] == "done"
    finally:
        srv.shutdown()
    written = next(p for _, p in iter_plans(library) if p.source_id == collection.source_id)
    assert (written.albumartist, written.album, written.folder) == (shown["albumartist"], shown["album"], shown["folder"])
    assert [(t.number, t.artist, t.title) for t in written.tracks] == [
        (t["number"], t["artist"], t["title"]) for t in shown["tracks"]]


def test_a_preview_of_an_album_already_here_says_so_and_keeps_the_users_edits(library, opus_template):
    """Without the merge the preview would promise names the fetch would not write."""
    collection = single_collection()
    app, srv, _ = previewing_server(library, opus_template, collection)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            wait(c, c.post("/api/fetch", json={"urls": [collection.source_url]}, headers=HDR).json()["job"]["id"])
            album_dir, plan = next((d, p) for d, p in iter_plans(library) if p.source_id == collection.source_id)
            apply_user_edits(plan, {"album": "My Own Album Name"})
            save_plan(plan, album_dir)

            job = c.post("/api/open", json={"q": collection.source_url}, headers=HDR).json()["job"]
            wait(c, job["id"])
            result = c.get(f"/api/job?id={job['id']}").json()["result"]
            log = "\n".join(c.get(f"/api/job?id={job['id']}").json()["log"])
    finally:
        srv.shutdown()
    assert result["album_dir"]  # the preview says which folder it is already in
    assert result["plan"]["album"] == "My Own Album Name"  # and shows the name a fetch would keep
    assert result["plan"]["provenance"]["album"] == "user"
    assert "already in the library" in log


def test_the_direct_path_does_not_go_through_a_preview(library, opus_template):
    """Shift+Go posts a fetch itself, so the preview never becomes a mandatory click."""
    collection = single_collection()
    app, srv, yt = previewing_server(library, opus_template, collection)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10) as c:
            job = c.post("/api/fetch", json={"urls": [collection.source_url]}, headers=HDR).json()["job"]
            assert job["lane"] == "write"
            assert wait(c, job["id"])["state"] == "done"
    finally:
        srv.shutdown()
    assert yt.downloads  # it downloaded without any preview in between


# -- a way back from an edit (P12, DESIGN.md §9.29) ----------------------------------------


def test_resetting_an_album_field_restores_what_ytalbum_derived():
    plan = build_plan(vol1())
    derived = plan.album
    apply_user_edits(plan, {"album": "My Own Name"})
    assert (plan.album, plan.provenance["album"]) == ("My Own Name", Provenance.USER)

    apply_user_edits(plan, {"reset": ["album"]})
    assert plan.album == derived
    assert "album" not in plan.provenance  # ytalbum's again, and the next pass names its source
    assert plan.folder.endswith(derived)  # the folder follows


def test_resetting_a_track_field_restores_it_too():
    plan = build_plan(vol1())
    track = plan.tracks[0]
    derived_title, derived_artist = track.title, track.artist
    apply_user_edits(plan, {"tracks": [{"video_id": track.video_id, "title": "Mine", "artist": "Me"}]})
    assert track.provenance["title"] == Provenance.USER

    apply_user_edits(plan, {"tracks": [{"video_id": track.video_id, "reset": ["title", "artist"]}]})
    assert (track.title, track.artist) == (derived_title, derived_artist)
    assert "title" not in track.provenance and "artist" not in track.provenance
    assert derived_title in track.filename  # and so does the file name


def test_a_reset_and_an_edit_in_one_save_both_happen():
    plan = build_plan(vol1())
    derived = plan.album
    apply_user_edits(plan, {"album": "My Own Name", "albumartist": "My Own Artist"})
    apply_user_edits(plan, {"reset": ["album"], "albumartist": "Another Artist"})
    assert plan.album == derived
    assert (plan.albumartist, plan.provenance["albumartist"]) == ("Another Artist", Provenance.USER)


def test_nothing_is_reset_that_was_not_derived():
    plan = build_plan(vol1())
    plan.auto.pop("album", None)  # an album from before `auto` was recorded
    apply_user_edits(plan, {"album": "My Own Name"})
    apply_user_edits(plan, {"reset": ["album"]})
    assert plan.album == "My Own Name"  # kept: there is nothing to go back to
    assert plan.provenance["album"] == Provenance.USER


def test_resetting_the_order_lifts_the_flag_without_renumbering():
    plan = build_plan(vol1())
    order = [t.video_id for t in reversed(plan.tracks)]
    apply_user_edits(plan, {"tracks": [{"video_id": v, "number": n} for n, v in enumerate(order, 1)]})
    assert plan.provenance["order"] == Provenance.USER

    apply_user_edits(plan, {"reset": ["order"]})
    assert "order" not in plan.provenance
    assert [t.video_id for t in plan.tracks] == order  # nothing moved now…
    assert [t.number for t in plan.tracks] == list(range(1, len(order) + 1))

    merged = merge_plans(plan, build_plan(vol1()))  # …but the source may order it again
    assert [t.video_id for t in merged.tracks] == [t.video_id for t in build_plan(vol1()).tracks]


def test_an_edited_field_survives_an_update_but_a_reset_one_does_not():
    plan = build_plan(vol1())
    derived = plan.tracks[0].title
    apply_user_edits(plan, {"tracks": [{"video_id": plan.tracks[0].video_id, "title": "Mine"}]})
    kept = merge_plans(plan, build_plan(vol1()))
    assert kept.tracks[0].title == "Mine"  # the user's value wins a merge

    apply_user_edits(kept, {"tracks": [{"video_id": kept.tracks[0].video_id, "reset": ["title"]}]})
    again = merge_plans(kept, build_plan(vol1()))
    assert again.tracks[0].title == derived  # and after a reset the source decides again


def test_repair_unifies_an_album_artist_that_was_reset(tmp_path, opus_template):
    """The point of the reset: the field is ytalbum's again, so harmonisation may touch it."""
    from test_repair import service as repair_service

    first = build_plan(vol1())
    first.source_id, first.album = "PL-one", "Vol. 1"
    first.albumartist, first.provenance["albumartist"] = "My Dark Lullabies", Provenance.PLAYLIST
    refresh_derived(first)
    run(first, tmp_path / first.folder, FakeYouTube(opus_template))
    save_plan(first, tmp_path / first.folder)

    second = build_plan(vol1())
    second.source_id, second.album = "PL-two", "Vol. 2"
    refresh_derived(second)
    apply_user_edits(second, {"albumartist": "MY DARK LULLABIES"})  # shouted, and theirs
    refresh_derived(second)
    run(second, tmp_path / second.folder, FakeYouTube(opus_template))
    save_plan(second, tmp_path / second.folder)

    repair_service(tmp_path, opus_template).repair()
    assert load_plan(tmp_path / second.folder).albumartist == "MY DARK LULLABIES"  # theirs is kept

    plan = load_plan(tmp_path / second.folder)
    apply_user_edits(plan, {"reset": ["albumartist"]})
    save_plan(plan, tmp_path / second.folder)
    repair_service(tmp_path, opus_template).repair()
    assert {p.albumartist for _, p in iter_plans(tmp_path)} == {"My Dark Lullabies"}


# -- opening an album tells the truth about its lyrics (P13, DESIGN.md §9.30) --------------


def opened(app, c, album_id):
    """Open the album the way the view does, and wait for whatever it set going."""
    got = c.get(f"/api/album?id={album_id}").json()
    for job in app.jobs.recent(5):
        if job.kind == "lyrics" and job.state in ("queued", "running"):
            wait(c, job.id)
    return got


def test_a_sidecar_edited_on_disk_becomes_yours_when_the_album_is_opened(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    sidecar_of(app, album_id, track).write_text(MINE + "\n")
    jobs_before = len(app.jobs.recent(50))

    view = opened(app, c, album_id)
    shown = next(t for t in view["tracks"] if t["video_id"] == track.video_id)
    assert shown["provenance"]["lyrics"] == "user"  # the view says so at once
    assert len(app.jobs.recent(50)) == jobs_before + 1  # and one job makes it durable
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert fresh.provenance["lyrics"] == "user"
    assert tagged_of(app, album_id, fresh) == MINE  # the tag followed the file


def test_a_sidecar_deleted_on_disk_stops_being_claimed_when_the_album_is_opened(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    sidecar_of(app, album_id, track).unlink()

    view = opened(app, c, album_id)
    shown = next(t for t in view["tracks"] if t["video_id"] == track.video_id)
    assert shown["lyrics"] == "none"  # no ♪ for words that are gone
    fresh = next(t for t in app.album(album_id)[1].tracks if t.video_id == track.video_id)
    assert (fresh.lyrics, fresh.lyrics_sha) == ("none", None)
    assert tagged_of(app, album_id, fresh) is None
    assert app.albums()[0]["lyrics"] == len(app.album(album_id)[1].tracks) - 1  # the grid count follows


def test_opening_an_album_that_agrees_with_its_files_writes_nothing(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    album_dir = app.album(album_id)[0]
    before = {p.name: p.stat().st_mtime_ns for p in album_dir.iterdir() if p.is_file()}
    jobs_before = len(app.jobs.recent(50))

    for _ in range(3):
        c.get(f"/api/album?id={album_id}")
    assert len(app.jobs.recent(50)) == jobs_before  # no job, three times over
    assert {p.name: p.stat().st_mtime_ns for p in album_dir.iterdir() if p.is_file()} == before


def test_a_track_without_a_file_is_not_reconciled(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    album_dir, plan = app.album(album_id)
    plan.tracks[0].state, plan.tracks[0].lyrics = "pending", "synced"  # a claim with no file at all
    save_plan(plan, album_dir)
    jobs_before = len(app.jobs.recent(50))

    view = opened(app, c, album_id)
    assert view["tracks"][0]["lyrics"] == "synced"  # left alone: reconcile only judges done tracks
    assert len(app.jobs.recent(50)) == jobs_before


def test_opening_twice_in_a_row_does_not_queue_two_jobs(lyrics_server):
    app, c, _ = lyrics_server
    album_id = app.albums()[0]["id"]
    wait(c, c.post("/api/lyrics", json={"id": album_id}, headers=HDR).json()["job"]["id"])
    track = app.album(album_id)[1].tracks[0]
    sidecar_of(app, album_id, track).write_text(MINE + "\n")

    held = app.jobs.submit("lyrics", "a long pass", lambda s: time.sleep(1.5), target=album_id)
    jobs_before = len(app.jobs.recent(50))
    c.get(f"/api/album?id={album_id}")
    assert len(app.jobs.recent(50)) == jobs_before  # that album is already in hand
    wait(c, held.id)
