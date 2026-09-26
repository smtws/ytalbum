"""Orchestration shared by the CLI and the web UI (DESIGN.md §7: the library never knows the UI).

Everything user-visible goes through three callbacks: `log(text)`, `on_plan(plan)` before
anything is downloaded, and `on_track(track, what)` per track.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .download import PARTS_DIR, PLAN_FILE, find_plan, iter_plans, load_plan, relocate, run, save_plan
from .enrich import enrich
from .lyrics import Lrclib, LyricsAPI, remove_sidecar, sidecar_lost, status_of, update_track, user_owns, write_sidecar
from .lyrics import default_cache_path as lyrics_cache_path
from .mb import MusicBrainz, default_cache_path
from .models import AlbumPlan, Kind, PlanTrack, Provenance, SourceRef
from .plan import build_plan, drop_album_name, merge_plans, refresh_derived, renumber, set_single_album_name, wanted_folder
from .search import SearchResult, search_artist
from .titles import key as text_key
from .titles import move_feat, strip_self_feat
from .trim import ORIGINALS, kept_originals
from .youtube import BOT_CHECK, Cancelled, YouTube, channel_base_url

log = logging.getLogger(__name__)

EDITABLE_ALBUM = ("album", "albumartist", "year")
EDITABLE_TRACK = ("artist", "title")


def parse_time(value: object) -> float | None:
    """'8', '0:08', '1:02.5' -> seconds; empty -> None. Raises ValueError on nonsense."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.strip() for p in parts):
        raise ValueError(f"not a time: {text!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if seconds < 0:
        raise ValueError("times cannot be negative")
    return seconds


@dataclass
class Outcome:
    status: str  # ok | failed | blocked | incomplete | planned | reported | dry
    plan: AlbumPlan | None = None
    album_dir: Path | None = None
    message: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


class Service:
    def __init__(
        self,
        cfg: Config,
        library: Path | None,
        log: Callable[[str], None] = lambda s: None,
        on_plan: Callable[[AlbumPlan], None] = lambda p: None,
        on_track: Callable[[PlanTrack, str], None] = lambda t, what: None,
        yt: YouTube | None = None,
        mb: MusicBrainz | None = None,
        cancel: threading.Event | None = None,
        lrclib: LyricsAPI | None = None,
    ) -> None:
        self.cfg = cfg
        self.library = library.expanduser() if library else None
        self.log, self.on_plan, self.on_track = log, on_plan, on_track
        self.cancel = cancel
        self.yt = yt or YouTube(cfg, cancel)
        self._mb = mb
        self._lrclib = lrclib

    def check(self) -> None:
        """Stop here if the job was cancelled (only called where stopping is safe)."""
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    @property
    def mb(self) -> MusicBrainz | None:
        if not self.cfg.musicbrainz:
            return None
        if self._mb is None:
            self._mb = MusicBrainz(default_cache_path())
        return self._mb

    @property
    def lrclib(self) -> LyricsAPI | None:
        if not self.cfg.lyrics:
            return None
        if self._lrclib is None:
            self._lrclib = Lrclib(lyrics_cache_path())
        return self._lrclib

    # -- one source --------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        dry: bool = False,
        plan_only: bool = False,
        report_only: bool = False,
        dump: Path | None = None,
    ) -> Outcome:
        """Read a playlist/video, enrich it, merge with the library, then (unless told not to) download."""
        self.check()
        self.log(f"reading {url} …")
        collection = self.yt.fetch(url)
        self.check()
        if dump:
            dump.write_text(json.dumps(collection.to_dict(), indent=2, ensure_ascii=False) + "\n")
        if unreadable := collection.unreadable:
            # never classify, merge or rename from a partial view (DESIGN.md §3.8)
            reason = unreadable[0].skipped or "unknown"
            msg = (
                f"{len(unreadable)} of {len(collection.entries)} videos could not be read right now ({reason}). "
                "Nothing was changed — try again later."
            )
            self.log(msg)
            return Outcome("blocked" if reason == BOT_CHECK else "incomplete", message=msg)

        plan = build_plan(collection)
        if collection.entries and not plan.tracks:
            # every video unusable for a reason that will not pass (Music Premium only, private,
            # removed): there is no album here, and writing one leaves an empty folder behind
            reasons = Counter(s["reason"] for s in plan.skipped)
            listed = ", ".join(f"{n}× {reason}" for reason, n in reasons.most_common(2))
            msg = f"nothing to download in “{plan.album}”: all {len(collection.entries)} videos are unusable ({listed}). Nothing was written."
            self.log(msg)
            return Outcome("failed", plan, message=msg)

        if mb := self.mb:
            stats = enrich(plan, mb, progress=lambda m: (self.check(), self.log(f"  {m}")))
            self.log("MusicBrainz: " + ("release matched" if stats["release"] else f"{stats['tracks']}/{stats['looked_up']} tracks matched"))
            # enrichment keeps bracket groups MusicBrainz lacks, which puts a live album's own
            # name back into every track ("Louder Than Hell (Live in Hamburg)") - so the
            # album-wide judgement is made again, on the final titles
            if dropped := drop_album_name(plan.album, plan.tracks):
                self.log(f"  {dropped} track title(s) lost the repeated album name")
        if named := set_single_album_name(plan):  # a single is its song, under whatever name it ended up with
            self.log(f"  the single is named after its track: “{named}”")

        if dry or self.library is None:
            # A preview is only worth having if it is the outcome, so it goes through what a real
            # fetch goes through — the merge with what is already there and the harmonisation —
            # and writes nothing. Without the merge it would show a fresh plan for an album whose
            # stored one carries the user's own edits, and promise names the fetch would not write.
            known_dir = None
            if found := (find_plan(self.library, plan.source_id) if self.library and self.library.exists() else None):
                known_dir, existing = found
                fresh_ids = {t.video_id for t in plan.tracks}
                new = sum(t.video_id not in {x.video_id for x in existing.tracks} for t in plan.tracks)
                plan = merge_plans(existing, plan)
                gone = sum(not t.in_source for t in plan.tracks)
                self.log(f"already in the library as {known_dir.relative_to(self.library)}: "
                         f"{len(fresh_ids)} in the source now, {new} new, {gone} no longer there")
            self._settle_artist(plan)  # read-only: a dry run shows the artist a fetch would write
            self.on_plan(plan)
            return Outcome("dry", plan, known_dir)

        if found := find_plan(self.library, plan.source_id):
            old_dir, existing = found
            known = {t.video_id for t in existing.tracks}
            new = sum(t.video_id not in known for t in plan.tracks)
            plan = merge_plans(existing, plan)
            gone = sum(not t.in_source for t in plan.tracks)
            self.log(f"existing album {old_dir.relative_to(self.library)}: {new} new, {gone} no longer in the source")
            if report_only:
                return Outcome("reported", plan, old_dir)
            self._settle_artist(plan)  # before the folder is chosen, or the album stays put
            album_dir = relocate(old_dir, plan, self.library)
        else:
            if report_only:
                self.log(f"not in the library yet: {plan.folder}")
                return Outcome("reported", plan)
            self._settle_artist(plan)
            album_dir = self.library / plan.folder

        self.check()  # last point before anything on disk changes
        self.on_plan(plan)
        if plan_only:
            save_plan(plan, album_dir)
            return Outcome("planned", plan, album_dir)
        return self.execute(plan, album_dir)

    def _decide_spellings(self) -> dict[str, tuple[str, set[str | None]]]:
        """One spelling per artist key for the whole library, decided before anything is renamed.

        `repair` used to ask the question once per album, against the library *as stored*, so
        an album already visited could not benefit from evidence found later: with three albums
        in three spellings the first pass left two of them and a second pass was needed
        (§9.23). One scan settles every key instead — and one scan is also all it costs, rather
        than one per album. The candidates are what the library holds: every album-level
        spelling with its provenance, plus the spelling an album's own tracks carry where
        `track_spelling`'s guards hold, which is MusicBrainz evidence.
        """
        candidates: dict[str, dict[str, set[str | None]]] = {}
        for _, plan in iter_plans(self.library) if self.library and self.library.exists() else []:
            key = text_key(plan.albumartist)
            for name, source in ((plan.albumartist, plan.provenance.get("albumartist")), (track_spelling(plan), Provenance.MB)):
                if name:
                    candidates.setdefault(key, {}).setdefault(name, set()).add(source)
        decided: dict[str, tuple[str, set[str | None]]] = {}
        for key, names in candidates.items():
            best = min(names, key=lambda n: spelling_rank(n, names[n]))
            decided[key] = (best, names[best])
        return decided

    def _apply_spelling(self, plan: AlbumPlan, decided: dict[str, tuple[str, set[str | None]]]) -> None:
        """Give this album the spelling the library decided on for its artist."""
        if plan.provenance.get("albumartist") == Provenance.USER:
            return  # theirs, and it still counted as a candidate for everyone else
        chosen = decided.get(text_key(plan.albumartist))
        if not chosen or chosen[0] == plan.albumartist:
            return
        self.log(f"artist spelled '{chosen[0]}' elsewhere in the library — using that")
        self._adopt(plan, chosen[0], chosen[1])

    def _settle_artist(self, plan: AlbumPlan) -> None:
        """The artist this fetch writes: the album's own tracks first, then the library's spelling.

        A fetch renames only the album it is fetching. So when the library already holds a
        spelling for this artist key, the incoming album adopts it — even when it arrives with
        better evidence, because upgrading the other albums is `ytalbum repair`'s job, not a
        side effect of fetching something. When the newcomer *is* the better evidence, one line
        says so and names both spellings. The cost, accepted: an older spelling can stand until
        repair runs. What it buys is one folder per artist (DESIGN.md §9.23).
        """
        self._adopt_track_spelling(plan)
        if plan.provenance.get("albumartist") == Provenance.USER or not self.library or not self.library.exists():
            return  # a spelling chosen for *this* album wins for this album, second folder or not
        seen = self._spellings(plan)
        if not seen:
            return  # the library knows this artist under no other spelling
        theirs = min(seen, key=lambda n: spelling_rank(n, seen[n]))
        if theirs == plan.albumartist:
            return
        ours = {plan.provenance.get("albumartist")}
        if spelling_rank(plan.albumartist, ours) < spelling_rank(theirs, seen[theirs]):
            self.log(
                f"this album spells the artist '{plan.albumartist}', the library '{theirs}' — keeping "
                f"'{theirs}' so there is one folder; 'ytalbum repair' (or “Repair library” in the web UI) "
                f"unifies them on the better spelling"
            )
        else:
            self.log(f"artist spelled '{theirs}' elsewhere in the library — using that")
        self._adopt(plan, theirs, seen[theirs])

    def _adopt(self, plan: AlbumPlan, name: str, sources: set[str | None]) -> None:
        """Take a spelling from elsewhere in the library, with the evidence it really has.

        Not the evidence *this* album had: a spelling adopted while the album's own came from
        MusicBrainz used to keep the `mb` marker, so a shouted name inherited a confirmation
        MusicBrainz never gave — and `repair` then converged on the shouting (§9.23). And never
        `user`, which means "the user chose this for *this* album" and would freeze it.
        """
        plan.albumartist = plan.auto["albumartist"] = name
        for source in (Provenance.MB, Provenance.YT_MUSIC, Provenance.PLAYLIST):
            if source in sources:
                plan.provenance["albumartist"] = source
                break
        else:
            plan.provenance["albumartist"] = Provenance.YT_TITLE
        refresh_derived(plan)

    def _spellings(self, plan: AlbumPlan) -> dict[str, set[str | None]]:
        """Every spelling the *rest* of the library has for this artist key, and where each came from."""
        key = text_key(plan.albumartist)
        seen: dict[str, set[str | None]] = {}
        for _, other in iter_plans(self.library) if self.library else []:
            if other.source_id != plan.source_id and text_key(other.albumartist) == key:
                seen.setdefault(other.albumartist, set()).add(other.provenance.get("albumartist"))
        return seen

    def _adopt_track_spelling(self, plan: AlbumPlan) -> None:
        """An album spelled unlike its own tracks: MusicBrainz credited the tracks, believe them.

        The release credit and the track credits are separate fields in MusicBrainz and do
        disagree ("LORD OF THE LOST" on the release, "Lord of the Lost" on every track). The
        album is made consistent with itself before the library is consulted, so what the
        library then weighs — and what `repair` later sees — is the better spelling.
        """
        common = track_spelling(plan)
        if not common:
            return
        if common == plan.albumartist:
            # already spelled as the tracks are — but possibly without saying where that came
            # from. `repair`'s own "use the most common track artist" rule renames without a
            # marker, and an unmarked spelling loses a tie to any other mixed-case spelling in
            # the library, alphabetically, which is a coin flip.
            plan.provenance["albumartist"] = Provenance.MB
            return
        self.log(f"the tracks are credited '{common}', the album '{plan.albumartist}' — using the tracks' spelling")
        plan.albumartist = plan.auto["albumartist"] = common
        # and it carries the tracks' evidence: MusicBrainz credited them, which is the whole
        # reason to believe them. Left at the album's old marker, this spelling loses a tie to
        # any other mixed-case spelling in the library — alphabetically, which is a coin flip.
        plan.provenance["albumartist"] = Provenance.MB
        refresh_derived(plan)  # or a new album keeps the folder of the spelling just dropped

    def execute(self, plan: AlbumPlan, album_dir: Path) -> Outcome:
        todo = sum(t.state != "done" and t.in_source for t in plan.tracks)
        self.log(f"downloading {todo} of {len(plan.tracks)} tracks into {album_dir}")
        run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check, lyrics=self.lrclib)
        failed = [t for t in plan.tracks if t.state != "done" and t.in_source]
        self.log(f"{len(plan.tracks) - len(failed)}/{len(plan.tracks)} tracks done" + (f", {len(failed)} not yet — run again to retry" if failed else ""))
        if any(t.error == BOT_CHECK for t in failed):
            return Outcome("blocked", plan, album_dir, BOT_CHECK)
        return Outcome("failed" if failed else "ok", plan, album_dir)

    def download_existing(self, album_dir: Path) -> Outcome:
        """Run an (edited) plan in an album folder: rename, retag, fetch what is missing."""
        plan = load_plan(album_dir)
        if not plan:
            return Outcome("failed", message=f"no plan in {album_dir}")
        library = album_dir.resolve().parents[1]  # <library>/<artist>/<album>
        album_dir = relocate(album_dir, plan, library)
        self.on_plan(plan)
        return self.execute(plan, album_dir)

    # -- many sources ------------------------------------------------------------------

    def fetch_many(self, refs: list[SourceRef], dry: bool = False) -> list[Outcome]:
        outcomes: list[Outcome] = []
        for i, ref in enumerate(refs, 1):
            self.log(f"=== [{i}/{len(refs)}] {ref.title}")
            outcomes.append(self._guarded(lambda: self.fetch(ref.url, dry=dry)))
            if outcomes[-1].blocked:
                self.log(f"stopping: YouTube is blocking requests; {len(refs) - i} not fetched")
                break
        return outcomes

    def update_all(self, report_only: bool = False, deep: bool = False, artist: str | None = None) -> list[Outcome]:
        """Check every album (or one artist's). Unchanged, complete albums cost one request."""
        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if artist:
            albums = [(d, p) for d, p in albums if p.albumartist.casefold() == artist.casefold()]
        if not albums:
            self.log(f"no albums{f' by {artist}' if artist else ''} in {self.library}")
        outcomes: list[Outcome] = []
        skipped = 0
        for i, (album_dir, plan) in enumerate(albums, 1):
            self.log(f"=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}")
            if not deep and (unchanged := self._unchanged(plan)):
                skipped += 1
                self.log(f"  unchanged ({unchanged}) — nothing to do")
                outcomes.append(Outcome("ok", plan, album_dir, "unchanged"))
                continue
            outcomes.append(self._guarded(lambda: self.fetch(plan.source_url, report_only=report_only)))
            if outcomes[-1].blocked:
                self.log(f"stopping: YouTube is blocking requests; {len(albums) - i} album(s) not checked")
                break
        if skipped:
            self.log(f"{skipped} of {len(albums)} albums were unchanged")
        return outcomes

    def _unchanged(self, plan: AlbumPlan) -> str | None:
        """One cheap request: is this album still exactly what the source lists, and complete?

        Returns a short reason when it can be skipped, else None (then it is read in full).
        """
        known = plan.source_state or {}
        if not known.get("ids"):
            return None  # never recorded (older album): read it properly
        waiting = [t for t in plan.tracks if t.in_source and t.state != "done" and not t.error_kind]
        if waiting:
            return None  # something is still missing here
        try:
            now = self.yt.source_state(plan.source_url)
        except Cancelled:
            raise
        except Exception as e:
            self.log(f"  could not check quickly ({e}); reading it in full")
            return None
        if not now or now["ids"] != known["ids"]:
            return None
        if now.get("modified") != known.get("modified"):
            return None  # the playlist itself changed (or we never recorded a date): read it once
        return f"{len(now['ids'])} videos, unchanged since {now.get('modified') or 'last time'}"

    def _guarded(self, action: Callable[[], Outcome]) -> Outcome:
        try:
            return action()
        except Cancelled:
            raise  # the user's cancel ends the whole job, not just this source
        except Exception as e:  # one broken source must not stop the others
            log.debug("source failed", exc_info=True)
            self.log(f"  failed: {e}")
            return Outcome("failed", message=str(e))

    # -- discovery ---------------------------------------------------------------------

    def channel(self, url: str) -> list[tuple[str, list[SourceRef]]]:
        self.log(f"reading channel {url} …")
        refs = self.yt.list_channel(url)
        groups = [
            (label, [r for r in refs if r.tab == tab])
            for tab, label in (("releases", "Releases (official albums and singles)"), ("playlists", "Playlists"))
        ]
        return [g for g in groups if g[1]]

    def search(self, artist: str) -> SearchResult:
        self.log(f"searching YouTube Music for {artist!r} …")
        return search_artist(self.yt, artist, self.mb)

    def library_source_ids(self) -> set[str]:
        if not self.library or not self.library.exists():
            return set()
        return {p.source_id for _, p in iter_plans(self.library)}

    # -- removing tracks that left the source ------------------------------------------------

    def prune(self, album_dir: Path) -> Outcome:
        """Delete the tracks the last fetch found no longer in the source; retag the rest."""
        plan = load_plan(album_dir)
        if not plan:
            return Outcome("failed", message=f"no plan in {album_dir}")
        gone = [t for t in plan.tracks if not t.in_source]
        if not gone:
            self.log("nothing to remove: every track is still in the source")
            return Outcome("ok", plan, album_dir)
        for t in gone:
            path = _inside(album_dir, t.filename)
            if path and path.exists():
                path.unlink()
            remove_sidecar(album_dir, t.filename)
            for kept in kept_originals(album_dir, t):
                kept.unlink()  # the untouched download goes with the track, as in delete_track
            self.log(f"removed {t.number:02d} {t.artist} - {t.title}" + ("" if path else " (unsafe file name ignored)"))
        plan.tracks = [t for t in plan.tracks if t.in_source]
        # Close the gap the removed tracks leave — on every album, not only single-disc ones:
        # a player showing 1, 2, 4 is a defect in the tags, and what a user order protects is
        # the arrangement, which counting the discs off in their own order keeps exactly.
        arrange(plan)
        save_plan(plan, album_dir)
        return self.execute(plan, album_dir)  # renames/retags only (tracktotal changed)

    # -- lyrics ----------------------------------------------------------------------------

    def fetch_lyrics(self, refetch: bool = False, artist: str | None = None, source_id: str | None = None) -> list[Outcome]:
        """Look up what is missing, write the `.lrc` sidecars and the tags. Downloads nothing.

        Only tracks that were never looked at are asked for, so running this twice costs
        nothing; `refetch` asks again for all of them (but never for the user's own lyrics).
        """
        api = self.lrclib
        if api is None:
            self.log("lyrics are switched off — turn them on with: ytalbum config --lyrics on")
            return []
        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if artist:
            albums = [(d, p) for d, p in albums if p.albumartist.casefold() == artist.casefold()]
        if source_id:
            albums = [(d, p) for d, p in albums if p.source_id == source_id]
        outcomes: list[Outcome] = []
        for i, (album_dir, plan) in enumerate(albums, 1):
            self.check()
            if refetch:
                for t in plan.tracks:
                    if not user_owns(album_dir, t):  # a mark with no file behind it protects nothing
                        t.lyrics = None  # lyrics_id stays: it is how a sidecar is recognised as ours
            # a deleted sidecar is work too: the pass has to drop the tag and the status with it
            todo = [t for t in plan.tracks if t.state == "done" and (t.lyrics is None or sidecar_lost(album_dir, t))]
            if not todo:
                continue
            self.log(f"=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}: {len(todo)} track(s) to look at")
            outcomes.append(self._guarded(lambda: self._lyrics_pass(plan, album_dir, api)))
        counts = Counter(t.lyrics or "not looked up" for _, plan in albums for t in plan.tracks if t.state == "done")
        self.log("lyrics: " + (", ".join(f"{n} {what}" for what, n in counts.most_common()) or "no tracks"))
        return outcomes

    def save_lyrics(self, source_id: str, video_id: str, text: str) -> Outcome:
        """Write the words a user typed beside one track — or clear them — and retag it.

        This is the one door into the ownership contract from the UI side: it does by hand what
        `reconcile` does when it finds an edited file (DESIGN.md §9.21, §9.26). Nothing is looked
        up, so a user is never told their words were replaced by lrclib's.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        if track.state != "done":
            return Outcome("failed", message=f"{track.title}: there is no file yet to put lyrics beside")
        if body := text.strip():
            write_sidecar(album_dir, track, body)  # records lyrics_sha as the bytes it wrote
            track.lyrics = status_of(body)
            track.provenance["lyrics"] = Provenance.USER
            self.log(f"wrote your lyrics for {track.title} ({track.lyrics})")
        else:
            # empty is a clear, not an empty file — and it gives the mark up with the words, so
            # `--refetch` may bring lrclib's back, exactly as deleting the file by hand does
            remove_sidecar(album_dir, track.filename)
            track.lyrics, track.lyrics_sha = "none", None
            track.provenance.pop("lyrics", None)
            self.log(f"removed the lyrics of {track.title}")
        save_plan(plan, album_dir)
        # retag through the ordinary pass, with no lyrics client: it rewrites the LYRICS tag from
        # the sidecar as every pass does, and downloads nothing
        run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check, download=False)
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    def lookup_track(self, source_id: str, video_id: str, reject: bool = False) -> Outcome:
        """Ask lrclib about one track — or reject what it gave and ask again (DESIGN.md §9.27).

        `reject` remembers the entry on the track, so no later lookup can pick it again: not this
        one, not a `--refetch`, not a fresh pass. Rejecting is about *that entry* being the wrong
        recording, which stays true however often it is asked for.
        """
        api = self.lrclib
        if api is None:
            return Outcome("failed", message="lyrics are switched off — turn them on with: ytalbum config --lyrics on")
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track in this album")
        if track.state != "done":
            return Outcome("failed", message=f"{track.title}: there is no file yet to match lyrics against")
        if track.provenance.get("lyrics") == Provenance.USER:
            # their words are not lrclib's to replace; deleting them in the editor is the way back
            return Outcome("failed", message=f"{track.title}: these lyrics are yours — delete them first")
        if reject:
            if not track.lyrics_id:
                return Outcome("failed", message=f"{track.title}: there is no lrclib match to reject")
            if track.lyrics_id not in track.lyrics_rejected:
                track.lyrics_rejected.append(track.lyrics_id)
            self.log(f"lrclib #{track.lyrics_id} is not “{track.title}” — it will not be offered again")
            remove_sidecar(album_dir, track.filename)
            track.lyrics_sha = None
        track.lyrics = None  # not looked up: update_track does the asking
        text = update_track(api, plan, track, album_dir, album_dir / track.filename)
        if track.lyrics is None:
            # `update_track` swallows a LyricsError and leaves the status unset so the next pass
            # asks again. Saying "nothing fits" here would report a site that did not answer as
            # an answer — the one thing this log line must not do.
            self.log(f"{track.title}: lrclib could not be reached — asked again on the next pass")
        elif text:
            self.log(f"{track.title}: {track.lyrics} lyrics" + (f" (lrclib #{track.lyrics_id})" if track.lyrics_id else ""))
        else:
            self.log(f"{track.title}: nothing lrclib has fits this recording")
        save_plan(plan, album_dir)
        run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check, download=False)  # the tag follows the file
        save_plan(plan, album_dir)
        return Outcome("ok", plan, album_dir)

    def _lyrics_pass(self, plan: AlbumPlan, album_dir: Path, api: LyricsAPI) -> Outcome:
        run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check, download=False, lyrics=api)
        return Outcome("ok", plan, album_dir)

    # -- offline repair --------------------------------------------------------------------

    def repair(self) -> list[Outcome]:
        """Tidy the library without asking YouTube: performer-only artists, one spelling.

        Fixes albums downloaded before those rules existed — renames and retags only.
        """
        outcomes = []
        decided = self._decide_spellings()  # every artist key settled before the first rename
        for album_dir, plan in list(iter_plans(self.library)) if self.library and self.library.exists() else []:
            before = (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks))
            seen: set[str] = set()  # the same video listed twice in a playlist is one track
            unique = [t for t in plan.tracks if not (t.video_id in seen or seen.add(t.video_id))]
            if len(unique) != len(plan.tracks):
                self.log(f"{len(plan.tracks) - len(unique)} duplicate track(s) removed from the album")
                plan.tracks = unique
                renumber(plan)
            # a length belongs to the recording it was read from; tracks whose recording was
            # refused ("(Live)", a cover) kept one anyway and read as minutes off (fixed 2026-09-25)
            borrowed = sum(bool(t.mb_length and not t.mbid) for t in plan.tracks)
            if borrowed:
                for t in plan.tracks:
                    if t.mb_length and not t.mbid:
                        t.mb_length = None
                self.log(f"{borrowed} track(s) gave up a length taken from another recording")
            for t in plan.tracks:
                if t.provenance.get("artist") == Provenance.YT_MUSIC and ", " in t.artist:
                    t.artist = t.auto["artist"] = t.artist.split(", ")[0]  # writers and producers
                if Provenance.USER in (t.provenance.get("artist"), t.provenance.get("title")):
                    continue  # the user decided how this one reads
                artist, title = move_feat(t.artist, t.title)  # guests belong in the title
                title = strip_self_feat(artist, title)
                if (artist, title) != (t.artist, t.title):
                    t.artist, t.title = artist, title
                    t.auto.update(artist=artist, title=title)
            editable = [t for t in plan.tracks if t.provenance.get("title") != Provenance.USER]
            if dropped := drop_album_name(plan.album, editable):
                self.log(f"{dropped} track title(s) lost the repeated album name")
            if plan.kind != Kind.COMPILATION and plan.provenance.get("albumartist") in (Provenance.YT_MUSIC, Provenance.YT_TITLE):
                names = [t.artist for t in plan.tracks]
                if names:
                    plan.albumartist = plan.auto["albumartist"] = max(set(names), key=names.count)
            self._adopt_track_spelling(plan)  # an album that disagrees with its own tracks
            self._apply_spelling(plan, decided)
            if named := set_single_album_name(plan):
                self.log(f"the single is named after its track: “{named}”")
            # a plan can be right while the folder is not: the album artist was unified
            # earlier without moving anything (fixed 2026-09-24, but the folders remain)
            misplaced = album_dir != self.library / wanted_folder(plan)
            if not misplaced and not borrowed and before == (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks)):
                continue
            self.log(f"=== {plan.albumartist} — {plan.album}")
            save_plan(plan, album_dir)
            album_dir = relocate(album_dir, plan, self.library)
            run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check, download=False)
            outcomes.append(Outcome("ok", plan, album_dir))
        self.log(f"{len(outcomes)} album(s) tidied up")
        return outcomes

    # -- deleting (always asked for explicitly) -------------------------------------------

    def delete_track(self, source_id: str, video_id: str) -> Outcome:
        """Delete one track: its files go, and it leaves the album.

        Nothing is remembered — if the video is still in the source, the next fetch
        brings it back.
        """
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        track = next((t for t in plan.tracks if t.video_id == video_id), None)
        if not track:
            return Outcome("failed", message="no such track")
        for path in (_inside(album_dir, track.filename), *kept_originals(album_dir, track)):
            if path and path.exists():
                path.unlink()
        remove_sidecar(album_dir, track.filename)
        self.log(f"removed {track.number:02d} {track.artist} - {track.title}")
        plan.tracks.remove(track)
        renumber(plan)
        save_plan(plan, album_dir)
        return self.execute(plan, album_dir)  # renames and retags the rest

    def delete_album(self, source_id: str) -> Outcome:
        """Delete everything ytalbum put into this album folder, then the folder if it is empty."""
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        for track in plan.tracks:
            for path in (_inside(album_dir, track.filename), *kept_originals(album_dir, track)):
                if path and path.exists():
                    path.unlink()
            remove_sidecar(album_dir, track.filename)
        for path in [*album_dir.glob("cover.*"), album_dir / PLAN_FILE]:
            path.unlink(missing_ok=True)
        for folder in (album_dir / ORIGINALS, album_dir / PARTS_DIR):
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        left = sorted(p.name for p in album_dir.iterdir()) if album_dir.exists() else []
        if left:
            self.log(f"kept {album_dir}: it still holds {len(left)} file(s) that are not ours ({', '.join(left[:3])})")
        else:
            album_dir.rmdir()
            with contextlib.suppress(OSError):
                album_dir.parent.rmdir()  # the artist folder, only when empty
            self.log(f"deleted {album_dir}")
        return Outcome("ok", plan, album_dir)

    # -- edits (web UI) ----------------------------------------------------------------

    def trim_channel(self, channel: str, start: float | None, end: float | None) -> list[Outcome]:
        """Same trim for every track from one uploader, across the whole library."""
        outcomes = []
        for album_dir, plan in iter_plans(self.library) if self.library and self.library.exists() else []:
            hits = [t for t in plan.tracks if (t.channel or "") == channel]
            if not hits:
                continue
            for t in hits:
                t.trim_start, t.trim_end = start, end
            save_plan(plan, album_dir)
            self.log(f"{plan.album}: {len(hits)} track(s) from {channel}")
            outcomes.append(self._guarded(lambda: self.execute(plan, album_dir)))
        if not outcomes:
            self.log(f"no tracks from {channel} in the library")
        return outcomes

    def find_album(self, source_id: str) -> tuple[Path, AlbumPlan] | None:
        return find_plan(self.library, source_id) if self.library and self.library.exists() else None

    def apply_edits(self, source_id: str, edits: dict[str, Any]) -> Outcome:
        """User edits from the UI: set values, mark them as the user's, then rename/retag on disk."""
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        before = {t.video_id: t.filename for t in plan.tracks}
        apply_user_edits(plan, edits)
        for t in plan.tracks:  # a changed format leaves the old file behind
            old = before.get(t.video_id)
            if old and old != t.filename and Path(old).suffix != Path(t.filename).suffix:
                stale = _inside(album_dir, old)
                if stale and stale.exists():
                    stale.unlink()
        album_dir = relocate(album_dir, plan, self.library)
        return self.execute(plan, album_dir)


def _inside(album_dir: Path, filename: str) -> Path | None:
    """album_dir/filename, but only if that really is a file directly in the album folder."""
    path = (album_dir / filename).resolve()
    return path if path.parent == album_dir.resolve() and path.name == filename else None


def apply_user_edits(plan: AlbumPlan, edits: dict[str, Any]) -> AlbumPlan:
    """Pure: copy editable fields from `edits` into the plan, marking changed ones as USER."""
    for name in EDITABLE_ALBUM:
        if name in edits:
            value = edits[name]
            if name == "year":
                value = int(value) if str(value or "").strip().isdigit() else None
            elif not isinstance(value, str) or not value.strip():
                continue
            else:
                value = value.strip()
            if value != getattr(plan, name):
                setattr(plan, name, value)
                plan.provenance[name] = Provenance.USER
    by_id = {t.video_id: t for t in plan.tracks}
    was_on = {t.video_id: t.disc for t in plan.tracks}  # numbers count inside the disc they were on
    typed: dict[str, int] = {}  # tracks the user gave a new number to -> the position they typed
    discs_changed = order_changed = False
    for te in edits.get("tracks", []):
        t = by_id.get(te.get("video_id"))
        if not t:
            continue
        if (choice := te.get("audio_choice")) in ("best", "combined") and choice != t.audio_choice:
            # switching means fetching the track again, in the other form
            t.audio_choice, t.ext = choice, "m4a" if choice == "combined" else "opus"
            t.state, t.error, t.error_kind, t.tagged, t.trimmed = "pending", None, None, None, None
        if "trim_start" in te or "trim_end" in te:
            start, end = parse_time(te.get("trim_start")), parse_time(te.get("trim_end"))
            if start is not None and end is not None and end <= start:
                raise ValueError(f"{t.title}: the end must come after the start")
            t.trim_start, t.trim_end = start, end
        if str(te.get("number", "")).strip().isdigit():
            wanted = max(1, int(te["number"]))
            if wanted != t.number:
                typed[t.video_id] = wanted
                order_changed = True
            t.number = wanted
        if str(te.get("disc", "")).strip().isdigit():
            disc = max(1, int(te["disc"]))
            discs_changed |= disc != t.disc
            t.disc = disc
        for name in EDITABLE_TRACK:
            value = te.get(name)
            if isinstance(value, str) and value.strip() and value.strip() != getattr(t, name):
                setattr(t, name, value.strip())
                t.provenance[name] = Provenance.USER
                if name == "title":
                    # this is no longer the recording MusicBrainz matched, and neither is its
                    # length: the two belong together, and a length that outlives its recording
                    # is a false reference that `repair` would later drop on its own
                    t.mbid = t.mb_length = None
    if order_changed:
        # the numbers first, read where they were typed: inside the disc the track was on, where
        # they are unique. Doing this against the *new* discs is what interleaved a collapse.
        plan.tracks = placed(plan.tracks, was_on, typed)
    if discs_changed or order_changed:
        arrange(plan)  # then the discs, keeping that arrangement, counting each disc from 1
    if order_changed:
        plan.provenance["order"] = Provenance.USER  # the source may not renumber this album
    return refresh_derived(plan)


def track_spelling(plan: AlbumPlan) -> str | None:
    """The spelling this album's own tracks carry, when it may speak for the album.

    The guards are §9.23's: not an album artist the user chose, the same artist key (so case and
    punctuation only, never a genuinely different credit), the most common track credit, and
    MusicBrainz behind that credit. A tie between two equally common spellings is settled by the
    evidence and then by `spelling_rank`, because `max(set(names), key=names.count)` would settle
    it by set iteration order — which hash randomisation makes differ between runs.
    """
    if plan.provenance.get("albumartist") == Provenance.USER or not plan.tracks:
        return None
    counts = Counter(t.artist for t in plan.tracks)
    most = max(counts.values())
    confirmed = {t.artist for t in plan.tracks if t.provenance.get("artist") == Provenance.MB}
    common = min(
        (name for name, n in counts.items() if n == most),
        key=lambda n: (n not in confirmed, spelling_rank(n, {Provenance.MB} if n in confirmed else set())),
    )
    if common not in confirmed or text_key(common) != text_key(plan.albumartist):
        return None
    return common


def spelling_rank(name: str, sources: set[str | None]) -> tuple[bool, bool, bool, bool, int, str]:
    """How good a spelling is: what someone chose, then MusicBrainz, then case, then length."""
    return (
        Provenance.USER not in sources,  # a spelling someone chose themselves
        Provenance.MB not in sources,  # then one MusicBrainz confirmed
        name.isupper(),  # then mixed case over a shouting channel name
        name.islower(),
        len(name),
        name,
    )


def placed(tracks: list[PlanTrack], was_on: dict[str, int], typed: dict[str, int]) -> list[PlanTrack]:
    """Put every track the user typed a number for on that position, inside the disc it was on.

    A typed number is a position, in both directions and including the last one. Sorting by the
    numbers cannot do that: a track moved *down* still sorts ahead of the track that holds the
    position below its target, so typing 5 on the first of five tracks moved it to 4 and no typed
    number could ever move a track to the end (DESIGN.md §9.22). So the typed tracks are taken
    out of the arrangement and put back at the index they asked for, lowest number first, while
    the untouched ones keep their relative order.
    """
    groups: dict[int, list[PlanTrack]] = {}
    for t in tracks:
        groups.setdefault(was_on.get(t.video_id, t.disc), []).append(t)
    for disc, group in groups.items():
        rest = [t for t in group if t.video_id not in typed]
        for t in sorted((x for x in group if x.video_id in typed), key=lambda x: typed[x.video_id]):
            rest.insert(min(typed[t.video_id] - 1, len(rest)), t)
        groups[disc] = rest
    return [t for disc in sorted(groups) for t in groups[disc]]


def arrange(plan: AlbumPlan) -> AlbumPlan:
    """Group the tracks by disc and count each disc from 1, in the order its tracks stand.

    The arrangement is the order the tracks are in — the order the album view shows — and this
    only groups and counts it. Sorting by `(disc, number)` as well, which is what this used to
    do, reshuffles an album whenever the numbers are not unique across the discs: collapsing
    1-01…1-03 / 2-01…2-03 back to one disc interleaved them (DESIGN.md §9.22).
    """
    by_disc: dict[int, list[PlanTrack]] = {}
    for t in plan.tracks:
        by_disc.setdefault(t.disc, []).append(t)
    plan.tracks = [t for disc in sorted(by_disc) for t in by_disc[disc]]
    for group in by_disc.values():
        for number, t in enumerate(group, 1):
            t.number = number
    return plan


def exit_code(outcomes: list[Outcome] | Outcome) -> int:
    """CLI exit status: 3 = YouTube is blocking, 1 = something failed, 0 = fine."""
    items = outcomes if isinstance(outcomes, list) else [outcomes]
    if any(o.blocked for o in items):
        return 3
    return 1 if any(o.status in ("failed", "incomplete") for o in items) else 0


__all__ = ["Outcome", "Service", "apply_user_edits", "channel_base_url", "exit_code"]
