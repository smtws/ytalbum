"""Slice 15: lyrics — matching that refuses the wrong recording, sidecars, the tag copy."""

import json
from pathlib import Path

import httpx
import pytest
from mutagen.oggopus import OggOpus
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import load_plan, run, save_plan
from ytalbum.lyrics import Lrclib, Lyrics, LyricsError, read_sidecar, sidecar_path, update_track
from ytalbum.models import Provenance
from ytalbum.plan import build_plan
from ytalbum.service import Service
from ytalbum.tag import audio_length, build_tags

SYNCED_LRC = "[00:01.00] one\n[00:05.50] two\n[00:12.00] three"

# one lrclib row, in its own field names
ROW = {"id": 42, "trackName": "Lullaby", "artistName": "TUNGSTEN", "albumName": "Tundra",
       "duration": 61.0, "syncedLyrics": SYNCED_LRC, "plainLyrics": "one\ntwo\nthree", "instrumental": False}


def client(handler, **kwargs) -> Lrclib:
    return Lrclib(cache_path=None, client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0, **kwargs)


def responses(*, get=None, search=None) -> tuple[Lrclib, list[httpx.URL]]:
    """A client answering /api/get with `get` (404 when None) and /api/search with `search`."""
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        if request.url.path.endswith("/get"):
            return httpx.Response(200, json=get) if get else httpx.Response(404, json={"name": "TrackNotFound"})
        return httpx.Response(200, json=search or [])

    return client(handler), asked


# -- matching ----------------------------------------------------------------------------


def test_the_exact_endpoint_is_asked_first_and_ends_it():
    api, asked = responses(get=ROW)
    found = api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0)
    assert found.synced == SYNCED_LRC
    assert found.lrclib_id == 42
    assert found.status == "synced"
    assert len(asked) == 1  # no search needed
    assert asked[0].params["duration"] == "61"


def test_an_album_lrclib_does_not_know_falls_back_to_the_search():
    # our compilation names ("My Dark Lullabies Vol. 7") never match lrclib's albums
    api, asked = responses(search=[ROW])
    found = api.get("TUNGSTEN", "Lullaby", "My Dark Lullabies Vol. 7", 60.0)
    assert found.lrclib_id == 42
    assert [u.path for u in asked] == ["/api/get", "/api/search"]


def test_a_recording_of_another_length_is_refused_but_its_length_is_kept():
    # the whole point: a cover or a live version is what a title-only match would attach.
    # How long lrclib thinks the song is stays, though — it is the second opinion on a file
    # that carries an intro, and the only one for tracks MusicBrainz does not know.
    api, _ = responses(search=[{**ROW, "duration": 210.0}])
    found = api.get("TUNGSTEN", "Lullaby", None, 61.0)
    assert found.text is None
    assert found.status == "none"
    assert found.length == 210.0


def test_the_kept_length_lands_in_the_plan(tmp_path, yt):
    class Refuses:
        def get(self, *a):
            return Lyrics(length=210.0)

    plan, album_dir = album(tmp_path, yt, Refuses())
    track = plan.tracks[0]
    assert (track.lyrics, track.lyrics_id, track.lyrics_length) == ("none", None, 210.0)
    assert read_sidecar(album_dir, track) is None


def test_without_a_length_nothing_is_accepted():
    api, asked = responses(search=[ROW])
    assert api.get("TUNGSTEN", "Lullaby", "Tundra", None) is None
    assert asked == []


def test_a_cover_by_someone_else_is_refused():
    api, _ = responses(search=[{**ROW, "artistName": "Some Tribute Band"}])
    assert api.get("TUNGSTEN", "Lullaby", None, 61.0) is None


def test_synced_wins_over_plain_and_then_the_closest_length():
    rows = [
        {**ROW, "id": 1, "duration": 61.0, "syncedLyrics": None},
        {**ROW, "id": 2, "duration": 62.0},
        {**ROW, "id": 3, "duration": 63.0},
    ]
    api, _ = responses(search=rows)
    assert api.get("TUNGSTEN", "Lullaby", None, 61.0).lrclib_id == 2


def test_an_instrumental_is_an_answer_not_a_gap():
    api, _ = responses(get={**ROW, "syncedLyrics": None, "plainLyrics": None, "instrumental": True})
    found = api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0)
    assert found.status == "instrumental"
    assert found.text is None


def test_a_track_over_an_hour_skips_the_endpoint_that_would_reject_it():
    # lrclib: "duration: must be between 1 and 3600" (HTTP 400, measured on a 63-minute track)
    api, asked = responses(get=ROW, search=[])
    assert api.get("Spooky Forest Music", "Ghost Glade", "Vol. 4", 3800.0) is None
    assert [u.path for u in asked] == ["/api/search"]


def test_an_entry_without_words_never_ends_the_search():
    """lrclib marks an entry instrumental when nobody submitted words, not only when a
    recording has none: 16 of the 18 entries for one Feuerschwanz song are such stubs, and
    the exact endpoint answered with one of them before the search saw the synced entry of
    exactly the same length."""
    stub = {**ROW, "id": 2608331, "syncedLyrics": None, "plainLyrics": None, "instrumental": True, "duration": 213.0}
    real = {**ROW, "id": 1948185, "duration": 213.0}
    api, asked = responses(get=stub, search=[stub, real])
    found = api.get("TUNGSTEN", "Lullaby", "Best Of", 211.7)
    assert found.lrclib_id == 1948185
    assert found.synced == SYNCED_LRC
    assert [u.path for u in asked] == ["/api/get", "/api/search"]


def test_an_instrumental_stands_when_nobody_has_words_for_the_song():
    stub = {**ROW, "syncedLyrics": None, "plainLyrics": None, "instrumental": True}
    api, _ = responses(get=stub, search=[stub])
    assert api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0).status == "instrumental"


def test_words_win_over_a_closer_length_without_them():
    api, _ = responses(search=[
        {**ROW, "id": 1, "duration": 61.0, "syncedLyrics": None, "plainLyrics": None, "instrumental": True},
        {**ROW, "id": 2, "duration": 63.0},
    ])
    assert api.get("TUNGSTEN", "Lullaby", None, 61.0).lrclib_id == 2


# -- transport ---------------------------------------------------------------------------


def test_server_busy_is_retried(monkeypatch):
    monkeypatch.setattr("ytalbum.lyrics.time.sleep", lambda s: None)
    tries = []

    def handler(request):
        tries.append(request.url)
        return httpx.Response(200, json=ROW) if len(tries) > 2 else httpx.Response(503, json={"name": "ServerOverloaded"})

    assert client(handler).get("TUNGSTEN", "Lullaby", "Tundra", 61.0).lrclib_id == 42
    assert len(tries) == 3


def test_giving_up_raises_and_never_pretends_there_are_no_lyrics(monkeypatch):
    monkeypatch.setattr("ytalbum.lyrics.time.sleep", lambda s: None)
    api = client(lambda request: httpx.Response(503, json={}), retries=1)
    with pytest.raises(LyricsError):
        api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0)


def test_a_request_lrclib_refuses_is_an_answer_not_an_error(tmp_path):
    """A 4xx will not become a 2xx on the next run; raising would retry it for ever."""
    asked = []

    def handler(request):
        asked.append(request.url)
        return httpx.Response(400, json={"message": "duration: must be between 1 and 3600"})

    cache = tmp_path / "lyrics.sqlite3"
    for _ in range(2):
        api = Lrclib(cache, httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
        assert api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0) is None
    assert len(asked) == 2  # get + search in the first run, nothing in the second


def test_answers_are_cached_on_disk(tmp_path, monkeypatch):
    asked = []

    def handler(request):
        asked.append(request.url)
        return httpx.Response(200, json=ROW)

    cache = tmp_path / "lyrics.sqlite3"
    for _ in range(2):
        api = Lrclib(cache, httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
        assert api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0).lrclib_id == 42
    assert len(asked) == 1  # the second client never asked


def test_a_miss_is_remembered_too(tmp_path):
    asked = []

    def handler(request):
        asked.append(request.url)
        return httpx.Response(404, json={"name": "TrackNotFound"}) if request.url.path.endswith("/get") else httpx.Response(200, json=[])

    cache = tmp_path / "lyrics.sqlite3"
    for _ in range(2):
        api = Lrclib(cache, httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
        assert api.get("TUNGSTEN", "Lullaby", "Tundra", 61.0) is None
    assert len(asked) == 2  # get + search, once each


# -- the album on disk -------------------------------------------------------------------


class FakeLyrics:
    """Answers for every track, or nothing for the ones named in `without`."""

    def __init__(self, without: set[str] = frozenset(), synced: bool = True) -> None:
        self.without, self.synced = without, synced
        self.asked: list[tuple[str, str, float | None]] = []

    def get(self, artist, title, album, length):
        self.asked.append((artist, title, length))
        if title in self.without:
            return None
        return Lyrics(synced=SYNCED_LRC if self.synced else None, plain="one\ntwo", lrclib_id=7)


def album(tmp_path, yt, api=None):
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:2]
    album_dir = tmp_path / "album"
    run(plan, album_dir, yt, lyrics=api)
    return plan, album_dir


@pytest.fixture
def yt(opus_template) -> FakeYouTube:
    return FakeYouTube(opus_template)


def test_a_download_writes_the_sidecar_and_the_tag(tmp_path, yt):
    api = FakeLyrics()
    plan, album_dir = album(tmp_path, yt, api)
    track = plan.tracks[0]

    sidecar = sidecar_path(album_dir, track.filename)
    assert sidecar.exists()
    assert sidecar.read_text() == SYNCED_LRC + "\n"
    assert OggOpus(album_dir / track.filename)["lyrics"] == [SYNCED_LRC]
    assert track.lyrics == "synced"
    assert track.lyrics_id == 7
    # the length handed to lrclib is the file's own, not what YouTube said
    assert api.asked[0][2] == pytest.approx(audio_length(album_dir / track.filename), abs=0.05)


def test_nothing_found_is_remembered_so_it_is_asked_once(tmp_path, yt):
    api = FakeLyrics(without={t.title for t in build_plan(vol1()).tracks[:2]})
    plan, album_dir = album(tmp_path, yt, api)
    assert [t.lyrics for t in plan.tracks] == ["none", "none"]
    assert not list(album_dir.glob("*.lrc"))
    assert "lyrics" not in OggOpus(album_dir / plan.tracks[0].filename)

    run(plan, album_dir, yt, download=False, lyrics=api)
    assert len(api.asked) == 2  # not asked again


def test_a_second_pass_neither_asks_again_nor_retags(tmp_path, yt):
    api = FakeLyrics()
    plan, album_dir = album(tmp_path, yt, api)
    before = {t.video_id: t.tagged for t in plan.tracks}
    run(plan, album_dir, yt, download=False, lyrics=api)
    assert len(api.asked) == 2
    assert {t.video_id: t.tagged for t in plan.tracks} == before


def test_an_existing_album_gets_its_lyrics_without_downloading(tmp_path, yt):
    plan, album_dir = album(tmp_path, yt, None)  # downloaded before lyrics existed
    assert all(t.lyrics is None for t in plan.tracks)
    assert "lyrics" not in OggOpus(album_dir / plan.tracks[0].filename)

    api = FakeLyrics()
    yt.downloads.clear()
    run(plan, album_dir, yt, download=False, lyrics=api)
    assert yt.downloads == []
    assert all(t.lyrics == "synced" for t in plan.tracks)
    assert OggOpus(album_dir / plan.tracks[0].filename)["lyrics"] == [SYNCED_LRC]


def test_the_sidecar_follows_a_renamed_track(tmp_path, yt):
    plan, album_dir = album(tmp_path, yt, FakeLyrics())
    track = plan.tracks[0]
    old = sidecar_path(album_dir, track.filename)

    track.title = "Another Name"
    run(plan, album_dir, yt, download=False)
    assert not old.exists()
    assert sidecar_path(album_dir, track.filename).read_text() == SYNCED_LRC + "\n"
    assert OggOpus(album_dir / track.filename)["lyrics"] == [SYNCED_LRC]  # still tagged


def test_deleting_the_sidecar_takes_the_tag_with_it(tmp_path, yt):
    """The .lrc is the truth: the tag is rewritten from it, never kept."""
    plan, album_dir = album(tmp_path, yt, FakeLyrics())
    track = plan.tracks[0]
    sidecar_path(album_dir, track.filename).unlink()
    run(plan, album_dir, yt, download=False)
    assert "lyrics" not in OggOpus(album_dir / track.filename)


def test_lyrics_the_user_wrote_are_never_overwritten(tmp_path, yt):
    plan, album_dir = album(tmp_path, yt, None)
    track = plan.tracks[0]
    sidecar_path(album_dir, track.filename).write_text("mine\n")
    track.provenance["lyrics"] = Provenance.USER

    api = FakeLyrics()
    run(plan, album_dir, yt, download=False, lyrics=api)
    assert api.asked == [(t.artist, t.title, pytest.approx(1.0, abs=0.1)) for t in plan.tracks[1:]]
    assert read_sidecar(album_dir, track) == "mine"
    assert OggOpus(album_dir / track.filename)["lyrics"] == ["mine"]


def test_a_trim_makes_the_track_be_matched_again(tmp_path, yt):
    """The match is gated on the file's length, so a cut file has to be looked up afresh."""
    api = FakeLyrics()
    plan, album_dir = album(tmp_path, yt, api)
    track = plan.tracks[0]
    assert track.lyrics == "synced"

    track.trim_start = 0.2
    run(plan, album_dir, yt, download=False, lyrics=api)
    assert len(api.asked) == 3  # this one was looked up a second time
    assert api.asked[-1][2] < api.asked[0][2]  # against the length it has now, not the old one
    assert read_sidecar(album_dir, track) == SYNCED_LRC  # and its timestamps are left alone


def test_plain_lyrics_are_written_as_they_are(tmp_path, yt):
    plan, album_dir = album(tmp_path, yt, FakeLyrics(synced=False))
    assert plan.tracks[0].lyrics == "plain"
    assert read_sidecar(album_dir, plan.tracks[0]) == "one\ntwo"


def test_the_signature_notices_lyrics_arriving(tmp_path, yt):
    plan, _ = album(tmp_path, yt, None)
    track = plan.tracks[0]
    assert "lyrics" not in build_tags(plan, track)
    assert build_tags(plan, track, SYNCED_LRC)["lyrics"] == SYNCED_LRC


def test_the_plan_round_trips_with_the_new_fields(tmp_path, yt):
    plan, album_dir = album(tmp_path, yt, FakeLyrics())
    written = json.loads((album_dir / ".ytalbum.json").read_text())
    assert written["tracks"][0]["lyrics"] == "synced"
    assert written["tracks"][0]["lyrics_id"] == 7


# -- the whole library (ytalbum lyrics) --------------------------------------------------


def library(tmp_path, opus_template):
    plan = build_plan(vol1())
    plan.tracks = plan.tracks[:2]
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, FakeYouTube(opus_template))
    return album_dir


def service(tmp_path, api):
    return Service(Config(musicbrainz=False), tmp_path, yt=NoNetwork(), lrclib=api)


class NoNetwork:
    def download_audio(self, *a, **k):
        raise AssertionError("fetching lyrics must not download audio")

    def fetch_bytes(self, *a, **k):
        raise AssertionError("fetching lyrics must not ask YouTube")


def test_the_library_pass_fills_in_what_is_missing_and_stops_there(tmp_path, opus_template):
    album_dir = library(tmp_path, opus_template)
    api = FakeLyrics()

    service(tmp_path, api).fetch_lyrics()
    saved = load_plan(album_dir)
    assert [t.lyrics for t in saved.tracks] == ["synced", "synced"]
    assert len(list(album_dir.glob("*.lrc"))) == 2

    service(tmp_path, api).fetch_lyrics()  # nothing left to do
    assert len(api.asked) == 2


def test_refetch_asks_again_but_leaves_the_users_own_lyrics_alone(tmp_path, opus_template):
    album_dir = library(tmp_path, opus_template)
    api = FakeLyrics()
    service(tmp_path, api).fetch_lyrics()

    plan = load_plan(album_dir)
    mine = plan.tracks[0]
    sidecar_path(album_dir, mine.filename).write_text("mine\n")
    mine.provenance["lyrics"] = Provenance.USER
    save_plan(plan, album_dir)

    service(tmp_path, api).fetch_lyrics(refetch=True)
    saved = load_plan(album_dir)
    assert len(api.asked) == 3  # only the other track was asked about again
    assert read_sidecar(album_dir, saved.tracks[0]) == "mine"
    assert saved.tracks[0].provenance["lyrics"] == Provenance.USER


def test_lyrics_switched_off_does_nothing(tmp_path, opus_template):
    album_dir = library(tmp_path, opus_template)
    api = FakeLyrics()
    Service(Config(musicbrainz=False, lyrics=False), tmp_path, yt=NoNetwork(), lrclib=api).fetch_lyrics()
    assert api.asked == []
    assert load_plan(album_dir).tracks[0].lyrics is None


def test_update_track_survives_lrclib_being_down(tmp_path, yt):
    class Broken:
        def get(self, *a):
            raise LyricsError("HTTP 503 after 4 tries")

    plan, album_dir = album(tmp_path, yt, None)
    track = plan.tracks[0]
    assert update_track(Broken(), plan, track, album_dir, album_dir / track.filename) is None
    assert track.lyrics is None  # not remembered as "none": ask again next time


def test_an_instrumental_cut_does_not_borrow_the_singers_words():
    """It is exactly as long as the sung version, so only the title can tell them apart."""
    sung = {**ROW, "id": 1, "duration": 61.0}
    quiet = {**ROW, "id": 2, "duration": 61.0, "syncedLyrics": None, "plainLyrics": None, "instrumental": True}
    api, _ = responses(search=[sung, quiet])
    assert api.get("TUNGSTEN", "Lullaby (instrumental)", None, 61.0).lrclib_id == 2
    assert api.get("TUNGSTEN", "Lullaby (Karaoke)", None, 61.0).lrclib_id == 2
    assert api.get("TUNGSTEN", "Lullaby", None, 61.0).lrclib_id == 1  # the sung one still gets them


def test_an_instrumental_with_only_sung_entries_gets_none_of_them():
    api, _ = responses(get=ROW, search=[ROW])
    found = api.get("TUNGSTEN", "Lullaby (instrumental)", "Tundra", 61.0)
    assert found.text is None
    assert found.status == "none"
    assert found.length == 61.0  # the length is still worth keeping
