"""Offline repair: performer-only artists and one spelling per artist, without asking YouTube."""

import pytest
from test_incremental import FakeYouTube, opus_template, vol1

from ytalbum.config import Config
from ytalbum.download import iter_plans, load_plan, run, save_plan
from ytalbum.models import Provenance
from ytalbum.plan import build_plan, refresh_derived
from ytalbum.service import Service, track_spelling


class NoNetwork(FakeYouTube):
    """Any call to YouTube fails the test."""

    def download_audio(self, *a, **k):
        raise AssertionError("repair must not download")

    def fetch_bytes(self, *a, **k):
        raise AssertionError("repair must not fetch")


def library_with(tmp_path, opus_template, changes):
    plan = build_plan(vol1())
    changes(plan)
    refresh_derived(plan)
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, FakeYouTube(opus_template))
    save_plan(plan, album_dir)
    return tmp_path, plan


def service(tmp_path, opus_template):
    return Service(Config(musicbrainz=False), tmp_path, yt=NoNetwork(opus_template))


def test_writer_lists_are_reduced_to_the_performer(tmp_path, opus_template):
    def pollute(plan):
        plan.kind = "artist_playlist"
        for t in plan.tracks:
            t.artist = t.auto["artist"] = "Feuerschwanz, Benjamin Metzner, Peter Henrici"
            t.provenance["artist"] = Provenance.YT_MUSIC
        plan.albumartist = plan.auto["albumartist"] = "Feuerschwanz, Benjamin Metzner, Peter Henrici"
        plan.provenance["albumartist"] = Provenance.YT_MUSIC

    tmp_path, plan = library_with(tmp_path, opus_template, pollute)
    service(tmp_path, opus_template).repair()

    saved = load_plan(tmp_path / "Feuerschwanz" / plan.album)
    assert saved.albumartist == "Feuerschwanz"
    assert {t.artist for t in saved.tracks} == {"Feuerschwanz"}
    assert (tmp_path / "Feuerschwanz" / plan.album / saved.tracks[0].filename).exists()
    assert not (tmp_path / "Feuerschwanz, Benjamin Metzner, Peter Henrici").exists()


def test_one_spelling_per_artist(tmp_path, opus_template):
    def shout(plan):
        plan.kind = "artist_playlist"
        plan.albumartist = plan.auto["albumartist"] = "SCHANDMAUL"
        plan.provenance["albumartist"] = Provenance.YT_TITLE
        for t in plan.tracks:
            t.artist = t.auto["artist"] = "Schandmaul"

    tmp_path, plan = library_with(tmp_path, opus_template, shout)
    (tmp_path / "Schandmaul" / "Another Album").mkdir(parents=True)  # the spelling used elsewhere
    other = build_plan(vol1())
    other.source_id, other.albumartist, other.album = "PLother", "Schandmaul", "Another Album"
    save_plan(other, tmp_path / "Schandmaul" / "Another Album")

    service(tmp_path, opus_template).repair()
    assert (tmp_path / "Schandmaul" / plan.album / ".ytalbum.json").exists()
    assert not (tmp_path / "SCHANDMAUL").exists()


def test_your_own_artist_name_is_left_alone(tmp_path, opus_template):
    def mine(plan):
        plan.kind = "artist_playlist"
        plan.albumartist = "MY BAND"
        plan.provenance["albumartist"] = Provenance.USER

    tmp_path, plan = library_with(tmp_path, opus_template, mine)
    service(tmp_path, opus_template).repair()
    assert (tmp_path / "MY BAND" / plan.album / ".ytalbum.json").exists()


def test_compilations_keep_their_curator(tmp_path, opus_template):
    tmp_path, plan = library_with(tmp_path, opus_template, lambda p: None)
    service(tmp_path, opus_template).repair()
    saved = load_plan(tmp_path / plan.folder)
    assert saved.albumartist == "My Dark Lullabies"
    assert saved.tracks[1].artist.startswith("DOMINUM")  # track artists stay as they are


def test_a_harmonised_artist_lands_in_the_right_folder_at_once(tmp_path, opus_template):
    """The spelling was unified but the album stayed in the old folder until the next run."""
    first = build_plan(vol1())
    first.albumartist, first.provenance["albumartist"] = "Saltatio Mortis", Provenance.MB
    refresh_derived(first)
    run(first, tmp_path / first.folder, FakeYouTube(opus_template))
    save_plan(first, tmp_path / first.folder)

    shouting = vol1()
    shouting.source_id = shouting.source_url = "PL-second"
    for e in shouting.entries:
        e.video_id = "x" + e.video_id[1:]
        e.music.artist = "SALTATIO MORTIS"
    service = Service(Config(library_root=tmp_path, musicbrainz=False), tmp_path, yt=FakeYouTube(opus_template))
    service.yt.fetch = lambda url: shouting

    outcome = service.fetch("https://www.youtube.com/playlist?list=PL-second")
    assert outcome.plan.albumartist == "Saltatio Mortis"  # the library's spelling wins
    assert outcome.album_dir.parent.name == "Saltatio Mortis"  # and the folder follows immediately
    assert not (tmp_path / "SALTATIO MORTIS").exists()


def shouting_second_playlist():
    shouting = vol1()
    shouting.source_id = shouting.source_url = "PL-second"
    for e in shouting.entries:
        e.video_id = "x" + e.video_id[1:]
        e.music.artist = "SALTATIO MORTIS"
    return shouting


def test_a_dry_run_shows_the_artist_the_fetch_would_write(tmp_path, opus_template):
    """D8: the preview used to print the spelling before harmonisation, so it could differ."""
    first = build_plan(vol1())
    first.albumartist, first.provenance["albumartist"] = "Saltatio Mortis", Provenance.MB
    refresh_derived(first)
    run(first, tmp_path / first.folder, FakeYouTube(opus_template))
    save_plan(first, tmp_path / first.folder)

    svc = Service(Config(library_root=tmp_path, musicbrainz=False), tmp_path, yt=NoNetwork(opus_template))
    svc.yt.fetch = lambda url: shouting_second_playlist()
    outcome = svc.fetch("https://www.youtube.com/playlist?list=PL-second", dry=True)
    assert outcome.status == "dry"
    assert outcome.plan.albumartist == "Saltatio Mortis"  # what a real fetch would write
    assert outcome.plan.folder.startswith("Saltatio Mortis/")
    assert not (tmp_path / "SALTATIO MORTIS").exists()  # and a dry run still writes nothing


def test_a_dry_run_without_a_library_still_works(tmp_path, opus_template):
    svc = Service(Config(musicbrainz=False), None, yt=NoNetwork(opus_template))
    svc.yt.fetch = lambda url: shouting_second_playlist()
    outcome = svc.fetch("https://www.youtube.com/playlist?list=PL-second", dry=True)
    assert outcome.status == "dry" and outcome.plan.albumartist == "SALTATIO MORTIS"


def test_repair_moves_an_album_whose_folder_no_longer_matches(tmp_path, opus_template):
    """The name was unified earlier without moving the album; repair has to finish the job."""
    plan = build_plan(vol1())
    plan.albumartist, plan.provenance["albumartist"] = "Saltatio Mortis", Provenance.MB
    refresh_derived(plan)
    stale = tmp_path / "SALTATIO MORTIS" / plan.album
    run(plan, stale, FakeYouTube(opus_template))
    save_plan(plan, stale)

    service(tmp_path, opus_template).repair()

    assert (tmp_path / "Saltatio Mortis" / plan.album / ".ytalbum.json").exists()
    assert not (tmp_path / "SALTATIO MORTIS").exists()


def harmonised(tmp_path, opus_template, library_names, own=("LORD OF THE LOST", Provenance.YT_TITLE)):
    """Put albums with the given (name, provenance) into a library, then harmonise `own`."""
    for i, (name, prov) in enumerate(library_names):
        p = build_plan(vol1())
        p.source_id, p.album = f"PL{i}", f"Album {i}"
        p.albumartist, p.provenance["albumartist"] = name, prov
        refresh_derived(p)
        run(p, tmp_path / p.folder, FakeYouTube(opus_template))
        save_plan(p, tmp_path / p.folder)
    plan = build_plan(vol1())
    plan.albumartist, plan.provenance["albumartist"] = own
    service(tmp_path, opus_template)._settle_artist(plan)  # the fetch side reads the same ranking
    return plan.albumartist


def settled(tmp_path, opus_template, library_names, own, tracks=None):
    """The settled spelling and the log; `settle` gives the whole plan."""
    plan, log = settle(tmp_path, opus_template, library_names, own, tracks)
    return plan.albumartist, log


def settle(tmp_path, opus_template, library_names, own, tracks=None):
    """Put albums into a library, then settle `own` the way a fetch does. Returns (plan, log)."""
    for i, (name, prov) in enumerate(library_names):
        p = build_plan(vol1())
        p.source_id, p.album = f"PL{i}", f"Album {i}"
        p.albumartist, p.provenance["albumartist"] = name, prov
        refresh_derived(p)
        run(p, tmp_path / p.folder, FakeYouTube(opus_template))
        save_plan(p, tmp_path / p.folder)
    plan = build_plan(vol1())
    plan.source_id = "PL-own"
    plan.albumartist, plan.provenance["albumartist"] = own
    for t in plan.tracks:
        t.artist, t.provenance["artist"] = tracks if tracks else (t.artist, t.provenance.get("artist"))
    log: list[str] = []
    svc = Service(Config(musicbrainz=False), tmp_path, yt=NoNetwork(opus_template), log=log.append)
    svc._settle_artist(plan)
    return plan, log


LOTL = "Lord of the Lost"


def test_a_fetch_adopts_the_librarys_spelling_rather_than_imposing_its_own(tmp_path, opus_template):
    name, log = settled(tmp_path, opus_template, [(LOTL, Provenance.MB)], ("LORD OF THE LOST", Provenance.YT_TITLE))
    assert name == LOTL
    assert log == [f"artist spelled '{LOTL}' elsewhere in the library — using that"]


def test_a_fetch_keeps_a_spelling_the_user_chose_for_another_album(tmp_path, opus_template):
    name, _ = settled(tmp_path, opus_template, [("LORD of the LOST", Provenance.USER)], (LOTL, Provenance.MB))
    assert name == "LORD of the LOST"


def test_better_evidence_is_named_once_and_left_to_repair(tmp_path, opus_template):
    """A fetch may rename only the album it is fetching, so the library's spelling still wins."""
    name, log = settled(tmp_path, opus_template, [("LORD OF THE LOST", Provenance.YT_TITLE)], (LOTL, Provenance.MB))
    assert name == "LORD OF THE LOST"  # unchanged here; the other album is not this fetch's business
    hints = [line for line in log if "repair" in line]
    assert len(hints) == 1
    assert LOTL in hints[0] and "LORD OF THE LOST" in hints[0]


def test_a_user_spelling_for_this_album_wins_and_keeps_its_own_folder(tmp_path, opus_template):
    name, log = settled(tmp_path, opus_template, [(LOTL, Provenance.MB)], ("LORD of the LOST", Provenance.USER))
    assert name == "LORD of the LOST"  # the only case that leaves two folders for one artist
    assert log == []


def test_an_album_takes_the_spelling_its_own_tracks_carry(tmp_path, opus_template):
    """MusicBrainz credits the release and the tracks separately, and they disagree."""
    plan, log = settle(tmp_path, opus_template, [], ("LORD OF THE LOST", Provenance.MB),
                       tracks=(LOTL, Provenance.MB))
    name = plan.albumartist
    assert name == LOTL
    assert plan.provenance["albumartist"] == Provenance.MB  # the tracks' evidence comes with it
    assert log == [f"the tracks are credited '{LOTL}', the album 'LORD OF THE LOST' — using the tracks' spelling"]


def test_a_track_spelling_without_musicbrainz_behind_it_is_not_adopted(tmp_path, opus_template):
    name, log = settled(tmp_path, opus_template, [], ("LORD OF THE LOST", Provenance.MB),
                        tracks=(LOTL, Provenance.YT_TITLE))
    assert (name, log) == ("LORD OF THE LOST", [])


def test_the_tracks_spelling_is_what_the_library_then_weighs(tmp_path, opus_template):
    """Outcome 3 runs first so the fetch arrives with the better evidence, hint and all."""
    name, log = settled(tmp_path, opus_template, [("LORD OF THE LOST", Provenance.YT_TITLE)],
                        ("LORD OF THE LOST", Provenance.MB), tracks=(LOTL, Provenance.MB))
    assert name == "LORD OF THE LOST"
    assert len([line for line in log if "repair" in line]) == 1


def test_repair_prefers_a_track_spelling_over_another_albums_guess(tmp_path, opus_template):
    """Two mixed-case spellings would otherwise be separated alphabetically — a coin flip."""
    def guess(plan):
        plan.kind = "album"
        plan.source_id, plan.album = "PL-guess", "Album guess"
        plan.albumartist, plan.provenance["albumartist"] = "Lord Of The Lost", Provenance.YT_TITLE
        for t in plan.tracks:
            t.artist, t.provenance["artist"] = "Lord Of The Lost", Provenance.YT_TITLE

    tmp_path, _ = library_with(tmp_path, opus_template, guess)
    second = build_plan(vol1())
    second.kind, second.source_id, second.album = "album", "PL-mb", "Album mb"
    second.albumartist, second.provenance["albumartist"] = "LORD OF THE LOST", Provenance.MB
    for t in second.tracks:
        t.artist, t.provenance["artist"] = LOTL, Provenance.MB
    refresh_derived(second)
    run(second, tmp_path / second.folder, FakeYouTube(opus_template))
    save_plan(second, tmp_path / second.folder)

    service(tmp_path, opus_template).repair()
    assert {p.albumartist for _, p in iter_plans(tmp_path)} == {LOTL}


def test_repair_converges_on_the_spelling_the_tracks_carry(tmp_path, opus_template):
    """What the fetch-time hint promises: repair finds the better spelling in the tracks."""
    def shout(plan):
        plan.kind = "album"
        plan.albumartist, plan.provenance["albumartist"] = "LORD OF THE LOST", Provenance.MB
        for t in plan.tracks:
            t.artist, t.provenance["artist"] = LOTL, Provenance.MB

    tmp_path, plan = library_with(tmp_path, opus_template, shout)
    assert (tmp_path / "LORD OF THE LOST").exists()

    service(tmp_path, opus_template).repair()
    saved = load_plan(tmp_path / LOTL / plan.album)
    assert saved.albumartist == LOTL
    assert not (tmp_path / "LORD OF THE LOST").exists()  # folder and file names follow


def test_repair_leaves_a_genuinely_different_credit_alone(tmp_path, opus_template):
    """Only case and punctuation: a compilation's tracks are other artists entirely."""
    tmp_path, plan = library_with(tmp_path, opus_template, lambda p: None)
    service(tmp_path, opus_template).repair()
    assert load_plan(tmp_path / plan.folder).albumartist == "My Dark Lullabies"


def test_an_adopted_spelling_does_not_inherit_this_albums_evidence(tmp_path, opus_template):
    """A shouted name marked `mb` is a confirmation MusicBrainz never gave — and repair believes it."""
    plan, _ = settle(tmp_path, opus_template, [("LORD OF THE LOST", Provenance.YT_TITLE)], (LOTL, Provenance.MB))
    assert plan.albumartist == "LORD OF THE LOST"
    assert plan.provenance["albumartist"] == Provenance.YT_TITLE  # the evidence that spelling has


def test_an_adopted_spelling_is_never_marked_as_the_users(tmp_path, opus_template):
    """`user` means the user chose it for *this* album; inheriting it would freeze the album."""
    plan, _ = settle(tmp_path, opus_template, [("LORD of the LOST", Provenance.USER)], (LOTL, Provenance.MB))
    assert plan.albumartist == "LORD of the LOST"
    assert plan.provenance["albumartist"] != Provenance.USER


def test_repair_reaches_the_track_spelling_through_an_adopted_one(tmp_path, opus_template):
    """The whole chain: a fetch adopts the shouting, repair then finds the tracks' spelling."""
    def shout(plan):
        plan.kind = "album"
        plan.source_id, plan.album = "PL-shout", "Album shout"
        plan.albumartist, plan.provenance["albumartist"] = "LORD OF THE LOST", Provenance.YT_TITLE
        for t in plan.tracks:
            t.artist, t.provenance["artist"] = LOTL, Provenance.MB

    tmp_path, _ = library_with(tmp_path, opus_template, shout)
    plan, _ = settle(tmp_path, opus_template, [], (LOTL, Provenance.MB), tracks=(LOTL, Provenance.MB))
    assert (plan.albumartist, plan.provenance["albumartist"]) == ("LORD OF THE LOST", Provenance.YT_TITLE)
    save_plan(plan, tmp_path / plan.folder)
    run(plan, tmp_path / plan.folder, FakeYouTube(opus_template))

    service(tmp_path, opus_template).repair()
    assert all(p.albumartist == LOTL for _, p in iter_plans(tmp_path))
    assert not (tmp_path / "LORD OF THE LOST").exists()


def test_three_spellings_converge_in_one_pass(tmp_path, opus_template):
    """Repair used to settle each album against the library as stored, so it needed two passes."""
    def shout(plan):
        plan.kind, plan.source_id, plan.album = "album", "PL-shout", "Album shout"
        plan.albumartist, plan.provenance["albumartist"] = "LORD OF THE LOST", Provenance.YT_TITLE
        for t in plan.tracks:
            t.artist, t.provenance["artist"] = "LORD OF THE LOST", Provenance.YT_TITLE

    tmp_path, _ = library_with(tmp_path, opus_template, shout)
    for source_id, album, name, prov, track, track_prov in (
        ("PL-title", "Album title", "Lord Of The Lost", Provenance.YT_TITLE, "Lord Of The Lost", Provenance.YT_TITLE),
        ("PL-mb", "Album mb", "LORD OF THE LOST", Provenance.YT_TITLE, LOTL, Provenance.MB),
    ):
        p = build_plan(vol1())
        p.kind, p.source_id, p.album = "album", source_id, album
        p.albumartist, p.provenance["albumartist"] = name, prov
        for t in p.tracks:
            t.artist, t.provenance["artist"] = track, track_prov
        refresh_derived(p)
        run(p, tmp_path / p.folder, FakeYouTube(opus_template))
        save_plan(p, tmp_path / p.folder)

    service(tmp_path, opus_template).repair()
    assert {p.albumartist for _, p in iter_plans(tmp_path)} == {LOTL}  # the MB track evidence wins
    assert sorted(d.name for d in tmp_path.iterdir() if d.is_dir()) == [LOTL]

    second = service(tmp_path, opus_template).repair()  # and the pass after it has nothing to do
    assert second == []


def test_a_user_spelling_is_left_alone_and_does_not_pull_the_others(tmp_path, opus_template):
    def mine(plan):
        plan.kind, plan.source_id, plan.album = "album", "PL-mine", "Album mine"
        plan.albumartist, plan.provenance["albumartist"] = "LORD of the LOST", Provenance.USER
        for t in plan.tracks:
            t.artist, t.provenance["artist"] = LOTL, Provenance.MB

    tmp_path, _ = library_with(tmp_path, opus_template, mine)
    other = build_plan(vol1())
    other.kind, other.source_id, other.album = "album", "PL-other", "Album other"
    other.albumartist, other.provenance["albumartist"] = "LORD OF THE LOST", Provenance.YT_TITLE
    for t in other.tracks:
        t.artist, t.provenance["artist"] = LOTL, Provenance.MB
    refresh_derived(other)
    run(other, tmp_path / other.folder, FakeYouTube(opus_template))
    save_plan(other, tmp_path / other.folder)

    service(tmp_path, opus_template).repair()
    saved = {p.source_id: p.albumartist for _, p in iter_plans(tmp_path)}
    assert saved["PL-mine"] == "LORD of the LOST"  # untouched
    assert saved["PL-other"] == "LORD of the LOST"  # and their spelling still ranks first


def test_two_equally_common_track_spellings_are_settled_by_evidence(tmp_path, opus_template):
    """Not by set iteration order, which hash randomisation makes differ between runs."""
    plan = build_plan(vol1())
    plan.albumartist, plan.provenance["albumartist"] = "LORD OF THE LOST", Provenance.MB
    for i, t in enumerate(plan.tracks):
        half = i < len(plan.tracks) // 2
        t.artist = LOTL if half else "Lord Of The Lost"
        t.provenance["artist"] = Provenance.MB if half else Provenance.YT_TITLE
    plan.tracks = plan.tracks[: 2 * (len(plan.tracks) // 2)]  # an even split, no majority
    assert track_spelling(plan) == LOTL


def test_a_spelling_musicbrainz_confirmed_beats_one_from_a_video_title(tmp_path, opus_template):
    names = [("Lord Of The Lost", Provenance.YT_TITLE), ("Lord of the Lost", Provenance.MB)]
    assert harmonised(tmp_path, opus_template, names) == "Lord of the Lost"


def test_a_spelling_the_user_chose_beats_musicbrainz(tmp_path, opus_template):
    names = [("Lord of the Lost", Provenance.MB), ("LORD of the LOST", Provenance.USER)]
    assert harmonised(tmp_path, opus_template, names) == "LORD of the LOST"


def test_without_either_the_case_rule_still_decides(tmp_path, opus_template):
    names = [("SCHANDMAUL", Provenance.YT_TITLE), ("Schandmaul", Provenance.YT_MUSIC)]
    assert harmonised(tmp_path, opus_template, names, own=("SCHANDMAUL", Provenance.YT_TITLE)) == "Schandmaul"


def test_a_length_read_from_another_recording_is_given_up(tmp_path, opus_template):
    """A live cut kept the studio recording's length, which reads as minutes off."""

    def borrow(plan):
        plan.tracks[0].mb_length, plan.tracks[0].mbid = 215.5, None  # "(Summer Breeze 2016)"
        plan.tracks[1].mb_length, plan.tracks[1].mbid = 208.2, "rec-1"  # this one really matched

    tmp_path, plan = library_with(tmp_path, opus_template, borrow)
    service(tmp_path, opus_template).repair()

    saved = load_plan(tmp_path / plan.folder)
    assert saved.tracks[0].mb_length is None
    assert saved.tracks[1].mb_length == 208.2  # the accepted one keeps it


# -- a single is named after its song (DESIGN.md §9.25) -----------------------------------


def a_single(tmp_path, opus_template, album, title, album_prov=Provenance.PLAYLIST):
    """A one-track album on disk whose album name and track title disagree."""
    plan = build_plan(vol1())
    plan.kind, plan.source_id = "single", "PL-single"
    plan.tracks = plan.tracks[:1]
    plan.album, plan.provenance["album"] = album, album_prov
    plan.tracks[0].title, plan.tracks[0].provenance["title"] = title, Provenance.MB
    refresh_derived(plan)
    album_dir = tmp_path / plan.folder
    run(plan, album_dir, FakeYouTube(opus_template))
    save_plan(plan, album_dir)
    return tmp_path, plan


def test_repair_names_an_existing_single_after_its_song(tmp_path, opus_template):
    tmp_path, plan = a_single(tmp_path, opus_template, "The Dead Don't Die (feat. @xxHANDLExx)",
                             "The Dead Don't Die feat. Feuerschwanz")
    old_dir = tmp_path / plan.folder

    service(tmp_path, opus_template).repair()
    saved = next(p for _, p in iter_plans(tmp_path))
    assert saved.album == "The Dead Don't Die feat. Feuerschwanz"
    assert saved.album == saved.tracks[0].title
    assert not old_dir.exists()  # the folder followed
    moved = tmp_path / saved.folder
    assert (moved / saved.tracks[0].filename).exists()  # and so did the file
    assert "@" not in saved.tracks[0].filename


def test_repair_leaves_a_single_whose_name_the_user_chose(tmp_path, opus_template):
    tmp_path, plan = a_single(tmp_path, opus_template, "My Own Name", "Some Other Title",
                             album_prov=Provenance.USER)
    service(tmp_path, opus_template).repair()
    saved = next(p for _, p in iter_plans(tmp_path))
    assert saved.album == "My Own Name"


def test_a_fetched_single_is_named_after_its_enriched_track(tmp_path, opus_template):
    """The fetch path: enrichment renames the track, and the album follows it."""
    collection = vol1()
    collection.source_id = collection.source_url = "v" * 11
    collection.is_playlist = False
    collection.entries = collection.entries[:1]
    collection.entries[0].duration = 200  # a two-second fixture entry counts as unusable
    collection.title = "DOMINUM - The Dead Don't Die (feat. @xxHANDLExx)"
    collection.channel = "DOMINUM"

    svc = Service(Config(library_root=tmp_path, musicbrainz=False), tmp_path, yt=FakeYouTube(opus_template))
    svc.yt.fetch = lambda url: collection
    outcome = svc.fetch(collection.source_url)
    saved = next(p for _, p in iter_plans(tmp_path))
    assert saved.kind == "single"
    assert saved.album == saved.tracks[0].title  # one song, one name, whatever enrichment left
    assert "@" not in saved.album and "@" not in outcome.album_dir.name
