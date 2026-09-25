"""Slice 5: MusicBrainz — matching on recorded real responses, client behaviour on a mock transport."""

import json
import time
from pathlib import Path

import httpx
import pytest

from ytalbum.enrich import (
    core,
    credit_names,
    credit_phrase,
    credited,
    enrich,
    enrich_release,
    enrich_track,
    feat_text,
    kept_suffixes,
    pick_recording,
    release_candidates,
    split_lookup,
    uploader_stood_in,
    version_markers,
)
from ytalbum.mb import MusicBrainz, MusicBrainzError, phrase
from ytalbum.models import Collection, Kind, PlanTrack, Provenance
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
    # guest credit picked via the @handle, and moved out of the artist field into the title
    assert got[1] == ("DOMINUM", "The Dead Don’t Die feat. Feuerschwanz")
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


def test_recording_length_is_kept_for_the_trim_suggestion():
    plan = plan_for("vol1_collection.json")
    enrich(plan, RecordedMB())
    known = [t for t in plan.tracks if t.mb_length]
    assert len(known) >= 10
    # MusicBrainz sometimes knows another, longer version: "Prinzessin" is 3:30 on YouTube,
    # the matched recording is 5:01 — the UI must not suggest an end past the file
    schandmaul = next(t for t in plan.tracks if t.artist == "Schandmaul")
    assert (schandmaul.mb_length, round(schandmaul.duration)) == (301.0, 210)


def test_release_tracks_keep_their_length_too():
    plan = plan_for("legends_olak_collection.json")
    enrich(plan, RecordedMB())
    assert all(t.mb_length and t.mb_length > 60 for t in plan.tracks)


# -- when the uploader stood in as the artist, the title carries the real one --------------


def test_split_lookup_finds_the_artist_glued_to_the_title():
    """'Assemblage 23 Lullaby': no punctuation separates them, so every split is tried."""
    mb = RecordedMB()
    found = split_lookup("Assemblage 23 Lullaby", mb)
    assert found is not None
    artist, title, rec = found
    assert (artist, title) == ("Assemblage 23", "Lullaby")
    assert credit_phrase(rec["artist-credit"]) == "Assemblage 23"
    assert mb.asked[0].startswith("recording?")  # the first split was asked first


def test_split_lookup_refuses_what_musicbrainz_cannot_confirm():
    # both halves must match a recording; a query for the right words is not enough
    assert split_lookup("Symphonic Gothic Metal Ballad", RecordedMB()) is None


def test_only_a_channel_name_triggers_the_split():
    t = PlanTrack(video_id="a" * 11, number=1, artist="Luewemi", title="Assemblage 23 Lullaby", filename="", provenance={})
    t.channel = "Luewemi"
    assert uploader_stood_in(t) is True
    t.channel = "Some Other Channel"  # a real artist name: the normal lookup applies
    assert uploader_stood_in(t) is False


# -- a credit's typography is not the artist's name -----------------------------------------

VOA = {"name": "Visions Of Atlantis", "artist": {"name": "Visions of Atlantis"}, "joinphrase": ""}


def test_a_credit_that_only_restyles_the_name_uses_the_artists_own_spelling():
    # MB credits carry per-release typography; three of seven VoA releases shout the "Of"
    assert credited(VOA) == "Visions of Atlantis"
    assert credit_phrase([VOA]) == "Visions of Atlantis"
    assert credit_names([VOA]) == ["Visions of Atlantis"]


def test_a_credit_that_names_something_else_is_kept():
    """The whole point of "credited as": an old release says Puff Daddy, and it stays."""
    entry = {"name": "Puff Daddy", "artist": {"name": "Diddy"}, "joinphrase": ""}
    assert credited(entry) == "Puff Daddy"


@pytest.mark.parametrize(
    ("credit", "entity", "expected"),
    [
        ("MONO INC.", "Mono Inc.", "Mono Inc."),  # punctuation and case only
        ("UNIVERSUM25", "Universum25", "Universum25"),
        ("DOMINUM", "DOMINUM", "DOMINUM"),  # MusicBrainz agrees with the cover: nothing to do
        ("Cat Stevens", "Yusuf", "Cat Stevens"),
        ("", "Arcana", "Arcana"),  # no credited name: the entity is all there is
        ("Arcana", "", "Arcana"),  # no entity in the response (a plain search hit)
    ],
)
def test_the_equivalence_class_decides(credit, entity, expected):
    assert credited({"name": credit, "artist": {"name": entity}}) == expected


def test_join_phrases_survive_and_every_name_is_restyled():
    ac = [
        {"name": "MONO INC.", "artist": {"name": "Mono Inc."}, "joinphrase": " feat. "},
        {"name": "tilo wolff", "artist": {"name": "Tilo Wolff"}, "joinphrase": ""},
    ]
    assert credit_phrase(ac) == "Mono Inc. feat. Tilo Wolff"
    assert credit_names(ac) == ["Mono Inc.", "Tilo Wolff"]


class OneRelease:
    """A single release, credited with a restyled artist name at both levels."""

    def __init__(self, titles: list[str]) -> None:
        self.body = {
            "id": "rel-1",
            "title": "Delta",
            "artist-credit": [VOA],
            "release-group": {"id": "rg-1", "first-release-date": "2011-03-25"},
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": i, "title": t, "artist-credit": [VOA], "recording": {"id": f"rec-{i}"}}
                        for i, t in enumerate(titles, 1)
                    ],
                }
            ],
        }

    def search_releases(self, artist: str, album: str) -> list[dict]:
        return [{**self.body, "track-count": len(self.body["media"][0]["tracks"]), "status": "Official", "score": 100}]

    def release(self, mbid: str) -> dict:
        return self.body


def test_the_release_and_its_tracks_get_the_artists_own_spelling():
    plan = plan_for("vol1_collection.json")
    plan.kind = Kind.OFFICIAL_ALBUM
    plan.album = "Delta"
    plan.albumartist = "Visions Of Atlantis"  # as the video titles shouted it
    plan.tracks = plan.tracks[:2]
    for t in plan.tracks:
        t.artist = "Visions Of Atlantis"

    assert enrich_release(plan, OneRelease([t.title for t in plan.tracks])) is True
    assert plan.albumartist == "Visions of Atlantis"
    assert {t.artist for t in plan.tracks} == {"Visions of Atlantis"}
    assert plan.provenance["albumartist"] == Provenance.MB


# -- a bracket group can say the recordings are different ones ------------------------------


@pytest.mark.parametrize(
    ("title", "markers"),
    [
        ("OPVS NOIR Vol. 1 (Instrumental)", {"instrumental"}),
        ("Heroes (Track Commentary Version)", {"commentary"}),
        ("Louder Than Hell (Live in Hamburg)", {"live"}),
        ("Swan Songs (Deluxe Edition)", set()),  # an edition holds the same recordings
        ("Nimmermehr (Tour Edition)", set()),
        ("Temple of the Torn (Collector's Cut)", set()),
        ("Alive", set()),  # not a marker: it must be a word of its own, in brackets
        ("Wildlive", set()),
    ],
)
def test_version_markers_are_told_from_edition_markers(title, markers):
    assert version_markers(title) == markers


def release_named(title, n=13):
    return {"id": "rel", "title": title, "track-count": n, "status": "Official",
            "artist-credit": [{"name": "Sabaton", "artist": {"name": "Sabaton"}}], "score": 100}


def test_a_release_of_other_recordings_is_not_our_album():
    """The Sabaton case: 11 commentary clips took the name of the album they talk about."""
    plan = plan_for("vol1_collection.json")
    plan.albumartist, plan.album = "Sabaton", "Heroes (Track Commentary Version)"
    assert release_candidates(plan, [release_named("Heroes")]) == []
    assert release_candidates(plan, [release_named("Heroes (Track Commentary Version)")])


def test_an_edition_still_matches_the_plain_release():
    plan = plan_for("vol1_collection.json")
    plan.albumartist, plan.album = "Sabaton", "Heroes (Deluxe Edition)"
    assert release_candidates(plan, [release_named("Heroes")])


def test_a_studio_release_is_not_offered_for_a_live_upload():
    plan = plan_for("vol1_collection.json")
    plan.albumartist, plan.album = "Sabaton", "Heroes (Live in Prague)"
    assert release_candidates(plan, [release_named("Heroes")]) == []


def test_a_rejected_recording_leaves_no_length_behind():
    """`mb_length` is the length of the recording we accepted, or nothing at all."""
    t = PlanTrack(video_id="a" * 11, number=1, artist="Feuerschwanz", title="Ketzerei (Summer Breeze 2016)",
                  filename="", provenance={})

    class OneRecording:
        def search_recordings(self, artist, title):
            return [{"id": "rec-1", "title": "Ketzerei", "length": 215500, "score": 100,
                     "artist-credit": [{"name": "Feuerschwanz", "artist": {"name": "Feuerschwanz"}}]}]

    assert enrich_track(t, OneRecording()) is True
    assert t.artist == "Feuerschwanz"  # the artist is confirmed
    assert t.title == "Ketzerei (Summer Breeze 2016)"  # our title stands
    assert t.mbid is None
    assert t.mb_length is None
