"""Slice 5: MusicBrainz — matching on recorded real responses, client behaviour on a mock transport."""

import json
import time
from pathlib import Path

import httpx
import pytest

from ytalbum.enrich import core, enrich, feat_text, kept_suffixes, pick_recording
from ytalbum.mb import MusicBrainz, MusicBrainzError, phrase
from ytalbum.models import Collection, Kind, Provenance
from ytalbum.plan import build_plan

FIXTURES = Path(__file__).parent.parent / "design-fixtures"
RESPONSES = json.loads((FIXTURES / "mb_responses.json").read_text())


class RecordedMB(MusicBrainz):
    """The real client, answering from responses recorded on 2026-09-22."""

    def __init__(self) -> None:
        super().__init__(cache_path=None, client=httpx.Client(transport=httpx.MockTransport(self._refuse)))
        self.asked: list[str] = []

    @staticmethod
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unrecorded request {request.url}")

    def _get(self, path, params):
        params = {**params, "fmt": "json"}
        k = path + "?" + "&".join(f"{a}={b}" for a, b in sorted(params.items()))
        self.asked.append(k)
        return RESPONSES.get(k) or None


def plan_for(name: str):
    return build_plan(Collection.from_dict(json.loads((FIXTURES / name).read_text())))


# -- matching on real data ---------------------------------------------------------------


def test_vol1_all_tracks_identified():
    plan = plan_for("vol1_collection.json")
    stats = enrich(plan, RecordedMB())
    assert stats == {"release": 0, "tracks": 13, "looked_up": 13}
    got = [(t.artist, t.title) for t in plan.tracks]
    assert got[1] == ("DOMINUM feat. Feuerschwanz", "The Dead Don’t Die")  # guest credit picked via the @handle
    assert got[2] == ("Mono Inc.", "Heile, heile Segen")  # MB spelling
    assert got[10] == ("Ashley Serena", "Lullaby of Woe")  # reversed title fixed by the swapped query
    assert all(t.mbid and t.provenance == {"artist": Provenance.MB, "title": Provenance.MB} for t in plan.tracks)
    assert plan.tracks[10].filename == "My Dark Lullabies - Vol. 1 - Heavy Sleeping - 11 - Ashley Serena - Lullaby of Woe.opus"


def test_variant_titles_confirm_the_artist_but_not_the_recording():
    plan = plan_for("vol20_collection.json")
    enrich(plan, RecordedMB())
    wardruna = plan.tracks[1]
    assert (wardruna.artist, wardruna.title) == ("Wardruna", "Helvegen (Live)")
    assert wardruna.provenance == {"artist": Provenance.MB, "title": Provenance.YT_TITLE}
    assert wardruna.mbid is None


def test_album_name_in_brackets_is_dropped_but_the_version_stays():
    plan = plan_for("vol20_collection.json")
    enrich(plan, RecordedMB())
    asp = next(t for t in plan.tracks if t.artist == "ASP")
    assert asp.title == "Schneefall in der Hölle (Plakat Mix)"  # "[MASKENHAFT-…]" named the release
    assert asp.mbid is None  # a mix: not that recording


def test_unknown_tracks_keep_youtube_data():
    plan = plan_for("vol20_collection.json")
    enrich(plan, RecordedMB())
    hurley = plan.tracks[4]
    assert (hurley.artist, hurley.title) == ("Mr. Hurley & die Pulveraffen", "Blau wie das Meer Version 2017")
    assert hurley.mbid is None and hurley.provenance["title"] == Provenance.YT_TITLE


def test_official_album_takes_the_release():
    plan = plan_for("legends_olak_collection.json")
    assert plan.kind == Kind.OFFICIAL_ALBUM
    stats = enrich(plan, RecordedMB())
    assert stats["release"] == 1 and stats["looked_up"] == 0  # no per-track lookups needed
    assert (plan.albumartist, plan.album, plan.year) == ("Sabaton", "Legends", 2025)
    assert plan.provenance == {"albumartist": Provenance.MB, "album": Provenance.MB, "year": Provenance.MB}
    assert plan.mbid and all(t.mbid for t in plan.tracks)
    assert [t.number for t in plan.tracks] == list(range(1, 12))
    assert plan.cover_url.startswith("https://coverartarchive.org/release-group/")
    assert "ytimg.com/s_p/OLAK5uy_" in plan.cover_fallback_url  # YouTube Music's own (square) album art


def test_fan_full_album_playlist_is_not_accepted_as_the_release():
    plan = plan_for_flat_legends()
    enrich(plan, RecordedMB())
    assert plan.mbid is None and plan.year is None
    docs = [t for t in plan.tracks if "Documentary" in t.title]
    assert len(docs) == 3 and all(t.mbid is None for t in docs)


def plan_for_flat_legends():
    from test_plan import flat_collection

    return build_plan(flat_collection("legends.json"))


def test_musicbrainz_outage_degrades_gracefully():
    class Down(RecordedMB):
        def _get(self, path, params):
            raise MusicBrainzError("HTTP 503 after 5 tries")

    plan = plan_for("vol1_collection.json")
    before = [(t.artist, t.title) for t in plan.tracks]
    assert enrich(plan, Down())["tracks"] == 0
    assert [(t.artist, t.title) for t in plan.tracks] == before


def test_user_edited_tracks_are_not_looked_up():
    plan = plan_for("vol1_collection.json")
    plan.tracks[0].provenance["title"] = Provenance.USER
    mb = RecordedMB()
    enrich(plan, mb)
    assert not any("Lullaby" in k and "ENEMY" in k.upper() for k in mb.asked)
    assert plan.tracks[0].mbid is None


# -- title helpers -------------------------------------------------------------------------


def test_title_helpers():
    assert core("The Dead Don't Die (feat. xxFEUERSCHWANZxx)") == "The Dead Don't Die"
    assert core("Crossing the Rubicon feat. Nothing More") == "Crossing the Rubicon"
    assert feat_text("The Dead Don't Die (feat. xxFEUERSCHWANZxx)") == "featxxfeuerschwanzxx"
    assert kept_suffixes("Helvegen (Live)", "Helvegen") == " (Live)"
    assert kept_suffixes("Song (feat. X)", "Song") == ""
    assert kept_suffixes("Song (Remix) [The Album]", "Song", ["The Album (Deluxe)"]) == " (Remix)"


def test_pick_recording_requires_artist_and_title():
    recs = [{"id": "1", "title": "Lullaby", "score": 100, "artist-credit": [{"name": "Tungsten"}]}]
    assert pick_recording("Tungsten", "Lullaby", recs)["id"] == "1"
    assert pick_recording("Enemy Inside", "Lullaby", recs) is None
    assert pick_recording("Tungsten", "Lullaby of Woe", recs) is None


def test_phrase_escaping():
    assert phrase('Say "Hi" \\o/') == '"Say \\"Hi\\" \\\\o/"'


# -- the client itself ------------------------------------------------------------------------


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://musicbrainz.org")


def test_client_retries_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request.url.params["query"])
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={"recordings": [{"id": "x"}]})

    mb = MusicBrainz(client=mock_client(handler), min_interval=0)
    assert mb.search_recordings("A", "B") == [{"id": "x"}]
    assert len(calls) == 3
    assert calls[0] == 'recording:"B" AND artist:"A"'


def test_client_gives_up_with_an_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    mb = MusicBrainz(client=mock_client(lambda r: httpx.Response(503)), min_interval=0, retries=2)
    with pytest.raises(MusicBrainzError):
        mb.search_releases("A", "B")


def test_client_caches_hits_but_not_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []
    answers = [httpx.Response(500), httpx.Response(200, json={"releases": [{"id": "r"}]})]

    def handler(request):
        calls.append(1)
        return answers.pop(0)

    mb = MusicBrainz(cache_path=tmp_path / "c.sqlite3", client=mock_client(handler), min_interval=0)
    with pytest.raises(MusicBrainzError):
        mb.search_releases("A", "B")
    assert mb.search_releases("A", "B") == [{"id": "r"}]
    assert mb.search_releases("A", "B") == [{"id": "r"}]  # from the cache
    assert len(calls) == 2


def test_client_spaces_requests(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    mb = MusicBrainz(client=mock_client(lambda r: httpx.Response(200, json={"recordings": []})), min_interval=1.0)
    mb.search_recordings("A", "B")
    mb.search_recordings("A", "C")
    assert slept and 0.9 < slept[-1] <= 1.0
