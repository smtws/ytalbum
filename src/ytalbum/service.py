"""Orchestration shared by the CLI and the web UI (DESIGN.md §7: the library never knows the UI).

Everything user-visible goes through three callbacks: `log(text)`, `on_plan(plan)` before
anything is downloaded, and `on_track(track, what)` per track.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .download import find_plan, iter_plans, load_plan, relocate, run, save_plan
from .enrich import enrich
from .mb import MusicBrainz, default_cache_path
from .models import AlbumPlan, PlanTrack, Provenance, SourceRef
from .plan import build_plan, merge_plans, refresh_derived
from .search import SearchResult, search_artist
from .youtube import BOT_CHECK, Cancelled, YouTube, channel_base_url

log = logging.getLogger(__name__)

EDITABLE_ALBUM = ("album", "albumartist", "year")
EDITABLE_TRACK = ("artist", "title")


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
        if mb := self.mb:
            stats = enrich(plan, mb, progress=lambda m: (self.check(), self.log(f"  {m}")))
            self.log("MusicBrainz: " + ("release matched" if stats["release"] else f"{stats['tracks']}/{stats['looked_up']} tracks matched"))

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
            album_dir = relocate(old_dir, plan, self.library)
        else:
            if report_only:
                self.log(f"not in the library yet: {plan.folder}")
                return Outcome("reported", plan)
            album_dir = self.library / plan.folder

        self.check()  # last point before anything on disk changes
        self.on_plan(plan)
        if plan_only:
            save_plan(plan, album_dir)
            return Outcome("planned", plan, album_dir)
        return self.execute(plan, album_dir)

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

    def update_all(self, report_only: bool = False) -> list[Outcome]:
        albums = list(iter_plans(self.library)) if self.library and self.library.exists() else []
        if not albums:
            self.log(f"no albums in {self.library}")
        outcomes: list[Outcome] = []
        for i, (_, plan) in enumerate(albums, 1):
            self.log(f"=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}")
            outcomes.append(self._guarded(lambda: self.fetch(plan.source_url, report_only=report_only)))
            if outcomes[-1].blocked:
                self.log(f"stopping: YouTube is blocking requests; {len(albums) - i} album(s) not checked")
                break
        return outcomes

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

    # -- edits (web UI) ----------------------------------------------------------------

    def find_album(self, source_id: str) -> tuple[Path, AlbumPlan] | None:
        return find_plan(self.library, source_id) if self.library and self.library.exists() else None

    def apply_edits(self, source_id: str, edits: dict[str, Any]) -> Outcome:
        """User edits from the UI: set values, mark them as the user's, then rename/retag on disk."""
        found = self.find_album(source_id)
        if not found:
            return Outcome("failed", message=f"unknown album {source_id}")
        album_dir, plan = found
        apply_user_edits(plan, edits)
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
    for te in edits.get("tracks", []):
        t = by_id.get(te.get("video_id"))
        if not t:
            continue
        for name in EDITABLE_TRACK:
            value = te.get(name)
            if isinstance(value, str) and value.strip() and value.strip() != getattr(t, name):
                setattr(t, name, value.strip())
                t.provenance[name] = Provenance.USER
                t.mbid = None if name == "title" else t.mbid
    return refresh_derived(plan)


def exit_code(outcomes: list[Outcome] | Outcome) -> int:
    """CLI exit status: 3 = YouTube is blocking, 1 = something failed, 0 = fine."""
    items = outcomes if isinstance(outcomes, list) else [outcomes]
    if any(o.blocked for o in items):
        return 3
    return 1 if any(o.status in ("failed", "incomplete") for o in items) else 0


__all__ = ["Outcome", "Service", "apply_user_edits", "exit_code", "channel_base_url"]
