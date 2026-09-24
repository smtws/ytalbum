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
from .mb import MusicBrainz, default_cache_path
from .models import AlbumPlan, Kind, PlanTrack, Provenance, SourceRef
from .plan import build_plan, drop_album_name, merge_plans, refresh_derived, renumber, wanted_folder
from .search import SearchResult, search_artist
from .titles import key as text_key
from .titles import move_feat, strip_self_feat
from .trim import ORIGINALS, original_path
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
    ) -> None:
        self.cfg = cfg
        self.library = library.expanduser() if library else None
        self.log, self.on_plan, self.on_track = log, on_plan, on_track
        self.cancel = cancel
        self.yt = yt or YouTube(cfg, cancel)
        self._mb = mb

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

        if dry or self.library is None:
            self.on_plan(plan)
            return Outcome("dry", plan)

        if found := find_plan(self.library, plan.source_id):
            old_dir, existing = found
            known = {t.video_id for t in existing.tracks}
            new = sum(t.video_id not in known for t in plan.tracks)
            plan = merge_plans(existing, plan)
            gone = sum(not t.in_source for t in plan.tracks)
            self.log(f"existing album {old_dir.relative_to(self.library)}: {new} new, {gone} no longer in the source")
            if report_only:
                return Outcome("reported", plan, old_dir)
            self._harmonize_artist(plan)  # before the folder is chosen, or the album stays put
            album_dir = relocate(old_dir, plan, self.library)
        else:
            if report_only:
                self.log(f"not in the library yet: {plan.folder}")
                return Outcome("reported", plan)
            self._harmonize_artist(plan)
            album_dir = self.library / plan.folder

        self.check()  # last point before anything on disk changes
        self.on_plan(plan)
        if plan_only:
            save_plan(plan, album_dir)
            return Outcome("planned", plan, album_dir)
        return self.execute(plan, album_dir)

    def _harmonize_artist(self, plan: AlbumPlan) -> None:
        """One spelling per artist in the library: 'SCHANDMAUL' and 'Schandmaul' are one folder."""
        if plan.provenance.get("albumartist") == Provenance.USER or not self.library or not self.library.exists():
            return
        seen: dict[str, set[str | None]] = {}  # spelling -> where each one came from
        for _, other in iter_plans(self.library):
            seen.setdefault(other.albumartist, set()).add(other.provenance.get("albumartist"))
        seen.setdefault(plan.albumartist, set()).add(plan.provenance.get("albumartist"))
        same = [name for name in seen if text_key(name) == text_key(plan.albumartist)]
        best = min(
            same,
            key=lambda n: (
                Provenance.USER not in seen[n],  # a spelling someone chose themselves
                Provenance.MB not in seen[n],  # then one MusicBrainz confirmed
                n.isupper(),  # then mixed case over a shouting channel name
                n.islower(),
                len(n),
                n,
            ),
        )
        if best != plan.albumartist:
            self.log(f"artist spelled '{best}' elsewhere in the library — using that")
            plan.albumartist = plan.auto["albumartist"] = best
            refresh_derived(plan)  # or a new album keeps the folder of the spelling just dropped

    def execute(self, plan: AlbumPlan, album_dir: Path) -> Outcome:
        todo = sum(t.state != "done" and t.in_source for t in plan.tracks)
        self.log(f"downloading {todo} of {len(plan.tracks)} tracks into {album_dir}")
        run(plan, album_dir, self.yt, on_track=self.on_track, check=self.check)
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
            self.log(f"removed {t.number:02d} {t.artist} - {t.title}" + ("" if path else " (unsafe file name ignored)"))
        plan.tracks = [t for t in plan.tracks if t.in_source]
        if all(t.disc == 1 for t in plan.tracks):  # gone tracks were numbered last; close any gap
            for number, t in enumerate(plan.tracks, 1):
                t.number = number
        save_plan(plan, album_dir)
        return self.execute(plan, album_dir)  # renames/retags only (tracktotal changed)

    # -- offline repair --------------------------------------------------------------------

    def repair(self) -> list[Outcome]:
        """Tidy the library without asking YouTube: performer-only artists, one spelling.

        Fixes albums downloaded before those rules existed — renames and retags only.
        """
        outcomes = []
        for album_dir, plan in list(iter_plans(self.library)) if self.library and self.library.exists() else []:
            before = (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks))
            seen: set[str] = set()  # the same video listed twice in a playlist is one track
            unique = [t for t in plan.tracks if not (t.video_id in seen or seen.add(t.video_id))]
            if len(unique) != len(plan.tracks):
                self.log(f"{len(plan.tracks) - len(unique)} duplicate track(s) removed from the album")
                plan.tracks = unique
                renumber(plan)
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
            self._harmonize_artist(plan)
            # a plan can be right while the folder is not: the album artist was unified
            # earlier without moving anything (fixed 2026-09-24, but the folders remain)
            misplaced = album_dir != self.library / wanted_folder(plan)
            if not misplaced and before == (plan.albumartist, [(t.artist, t.title) for t in plan.tracks], len(plan.tracks)):
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
        for path in (_inside(album_dir, track.filename), original_path(album_dir, track)):
            if path and path.exists():
                path.unlink()
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
            for path in (_inside(album_dir, track.filename), original_path(album_dir, track)):
                if path and path.exists():
                    path.unlink()
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
            order_changed |= wanted != t.number
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
                t.mbid = None if name == "title" else t.mbid
    if discs_changed or order_changed:
        renumber_discs(plan)  # sorts by what was asked for, then counts each disc from 1
    if order_changed:
        plan.provenance["order"] = Provenance.USER  # the source may not renumber this album
    return refresh_derived(plan)


def renumber_discs(plan: AlbumPlan) -> AlbumPlan:
    """Each disc counts from 1, in the order the tracks stand — what a split needs."""
    plan.tracks.sort(key=lambda t: (t.disc, t.number))
    counters: dict[int, int] = {}
    for t in plan.tracks:
        counters[t.disc] = counters.get(t.disc, 0) + 1
        t.number = counters[t.disc]
    return plan


def exit_code(outcomes: list[Outcome] | Outcome) -> int:
    """CLI exit status: 3 = YouTube is blocking, 1 = something failed, 0 = fine."""
    items = outcomes if isinstance(outcomes, list) else [outcomes]
    if any(o.blocked for o in items):
        return 3
    return 1 if any(o.status in ("failed", "incomplete") for o in items) else 0


__all__ = ["Outcome", "Service", "apply_user_edits", "channel_base_url", "exit_code"]
