"""Command line: `ytalbum fetch|plan|download|update|config`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config as config_mod
from .enrich import enrich
from .mb import MusicBrainz, default_cache_path
from .download import find_plan, iter_plans, load_plan, relocate, run, save_plan
from .models import AlbumPlan, PlanTrack, SourceRef
from .plan import build_plan, merge_plans
from .search import search_artist
from .youtube import BOT_CHECK, NotSupported, YouTube, channel_base_url

BLOCKED = 3  # exit code: YouTube is refusing requests right now; stop asking

PROV_MARK = {"mb": "MB", "yt_music": "YTM", "yt_title": "title", "playlist": "playlist", "user": "user"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ytalbum", description="Turn YouTube playlists into tagged albums.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="plan and download a playlist, video or channel URL")
    f.add_argument("url")
    f.add_argument("--library", type=Path, help="library root (overrides the config file)")
    f.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    f.add_argument("--all", action="store_true", help="channel: take every release and playlist")
    f.add_argument("--pick", metavar="SPEC", help="channel: which ones, e.g. 1,3-5 (default: ask)")
    f.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    f.add_argument("--dump-collection", type=Path, metavar="FILE", help="also save what YouTube returned (for test fixtures)")

    se = sub.add_parser("search", help="find an artist's albums on YouTube and pick which to fetch")
    se.add_argument("artist")
    se.add_argument("--library", type=Path)
    se.add_argument("--dry-run", action="store_true", help="print the plans, write nothing")
    se.add_argument("--all", action="store_true", help="take everything found")
    se.add_argument("--pick", metavar="SPEC", help="which ones, e.g. 1,3-5 (default: ask)")
    se.add_argument("--no-mb", action="store_true", help="skip MusicBrainz (lookups and the discography check)")

    pl = sub.add_parser("plan", help="write the plan into the album folder for editing, download nothing")
    pl.add_argument("url")
    pl.add_argument("--library", type=Path)
    pl.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")

    d = sub.add_parser("download", help="download from an (edited) plan in an album folder")
    d.add_argument("album_dir", type=Path)

    u = sub.add_parser("update", help="re-check every album in the library against its source")
    u.add_argument("--library", type=Path)
    u.add_argument("--dry-run", action="store_true", help="only report what changed")
    u.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")

    c = sub.add_parser("config", help="show or set configuration")
    c.add_argument("--library", type=Path, help="set the library root")
    c.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", help="use a browser's YouTube login (for age-restricted videos); 'none' to unset")
    c.add_argument("--cookies-file", type=Path, metavar="FILE", help="use an exported cookies.txt instead; 'none' to unset")

    args = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # keep progress in order with stderr when piped
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config_mod.load()
    if getattr(args, "no_mb", False):
        cfg.musicbrainz = False

    try:
        match args.cmd:
            case "config":
                return _config(args, cfg)
            case "fetch" | "plan":
                return _fetch(args, cfg)
            case "download":
                return _download(args.album_dir, cfg)
            case "update":
                return _update(args, cfg)
            case "search":
                return _search(args, cfg)
    except NotSupported as e:
        print(f"not supported: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — run the same command again to resume", file=sys.stderr)
        return 130
    return 0


# -- commands ----------------------------------------------------------------------------


def _config(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    changes = {}
    if args.library:
        changes["library_root"] = str(args.library.expanduser().resolve())
    if args.cookies_from_browser:
        changes["cookies_from_browser"] = None if args.cookies_from_browser == "none" else args.cookies_from_browser
    if args.cookies_file:
        changes["cookies_file"] = None if str(args.cookies_file) == "none" else str(args.cookies_file.expanduser().resolve())
    for name, value in changes.items():
        config_mod.save_setting(name, value)
    if changes:
        cfg = config_mod.load()
    runtime = cfg.resolved_js_runtime()
    print(f"config file:  {config_mod.config_path()}")
    print(f"library_root: {cfg.library_root or '(not set)'}")
    print(f"cookies:      {cfg.cookies_file or cfg.cookies_from_browser or '(none — age-restricted videos are skipped)'}")
    print(f"js runtime:   {' '.join(filter(None, runtime)) if runtime else 'NONE FOUND — install deno or node'}")
    return 0


def _fetch(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    dry = getattr(args, "dry_run", False)
    library = _library(args, cfg, required=not dry)
    if library is None and not dry:
        return 2
    _warn_js_runtime(cfg)
    yt = YouTube(cfg)

    if not channel_base_url(args.url):
        return _fetch_one(args.url, library, yt, args.cmd, dry, getattr(args, "dump_collection", None))

    if args.cmd == "plan":
        print("`plan` takes one playlist; for a channel use `fetch --pick N --dry-run` first", file=sys.stderr)
        return 2
    print(f"reading channel {args.url} …", file=sys.stderr)
    refs = yt.list_channel(args.url)
    if not refs:
        print("this channel has no releases or playlists", file=sys.stderr)
        return 1
    groups = [
        (label, [r for r in refs if r.tab == tab])
        for tab, label in (("releases", "Releases (official albums and singles)"), ("playlists", "Playlists"))
    ]
    return _pick_and_fetch([g for g in groups if g[1]], args, library, yt, dry)


def _search(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=not args.dry_run)
    if library is None and not args.dry_run:
        return 2
    _warn_js_runtime(cfg)
    yt = YouTube(cfg)
    print(f"searching YouTube Music for {args.artist!r} …", file=sys.stderr)
    result = search_artist(yt, args.artist, _musicbrainz() if cfg.musicbrainz else None)
    if not result.refs:
        print("nothing found", file=sys.stderr)
        return 1
    if result.channel_url:
        print(f"artist channel: {result.channel_url}", file=sys.stderr)
    code = _pick_and_fetch(result.groups, args, library, yt, args.dry_run, after_list=lambda: _print_missing(result.missing))
    return code


def _print_missing(missing: list[str]) -> None:
    if missing:
        print(f"\nMusicBrainz lists {len(missing)} more studio album(s) not found on YouTube: " + "; ".join(missing))


def _pick_and_fetch(groups, args, library, yt, dry, after_list=lambda: None) -> int:
    refs = [r for _, group in groups for r in group]
    _print_groups(groups, library)
    after_list()
    chosen = _choose(refs, args)
    worst = 0
    for i, ref in enumerate(chosen, 1):
        print(f"\n=== [{i}/{len(chosen)}] {ref.title}", file=sys.stderr)
        try:
            code = _fetch_one(ref.url, library, yt, "fetch", dry)
        except Exception as e:  # one broken source must not stop the others
            print(f"  failed: {e}", file=sys.stderr)
            code = 1
        worst = max(worst, code)
        if code == BLOCKED:
            print(f"stopping: YouTube is blocking requests; {len(chosen) - i} not fetched", file=sys.stderr)
            break
    return worst


def _download(album_dir: Path, cfg: config_mod.Config) -> int:
    plan = load_plan(album_dir)
    if not plan:
        print(f"no plan in {album_dir}", file=sys.stderr)
        return 2
    _warn_js_runtime(cfg)
    library = album_dir.resolve().parents[1]  # <library>/<artist>/<album>
    album_dir = relocate(album_dir, plan, library)
    _print_plan(plan)
    return _execute(plan, album_dir, YouTube(cfg))


def _update(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=True)
    if library is None:
        return 2
    _warn_js_runtime(cfg)
    albums = list(iter_plans(library))
    if not albums:
        print(f"no albums in {library}", file=sys.stderr)
        return 0
    yt = YouTube(cfg)
    worst = 0
    for i, (_, plan) in enumerate(albums, 1):
        print(f"\n=== [{i}/{len(albums)}] {plan.albumartist} — {plan.album}", file=sys.stderr)
        try:
            code = _fetch_one(plan.source_url, library, yt, "fetch", False, report_only=args.dry_run)
        except Exception as e:  # one broken source must not stop the others
            print(f"  could not update: {e}", file=sys.stderr)
            code = 1
        worst = max(worst, code)
        if code == BLOCKED:
            print(f"stopping: YouTube is blocking requests; {len(albums) - i} album(s) not checked", file=sys.stderr)
            break
    return worst


# -- the shared path -----------------------------------------------------------------------


_mb: MusicBrainz | None = None


def _musicbrainz() -> MusicBrainz:
    global _mb
    if _mb is None:
        _mb = MusicBrainz(default_cache_path())
    return _mb


def _fetch_one(
    url: str,
    library: Path | None,
    yt: YouTube,
    cmd: str,
    dry: bool,
    dump: Path | None = None,
    report_only: bool = False,
) -> int:
    print(f"reading {url} …", file=sys.stderr)
    collection = yt.fetch(url)
    if dump:
        dump.write_text(json.dumps(collection.to_dict(), indent=2, ensure_ascii=False) + "\n")
    if unreadable := collection.unreadable:
        # never classify, merge or rename from a partial view (DESIGN.md §3.8)
        reason = unreadable[0].skipped
        print(
            f"{len(unreadable)} of {len(collection.entries)} videos could not be read right now ({reason}).\n"
            "Nothing was changed — run the same command again later.",
            file=sys.stderr,
        )
        return BLOCKED if reason == BOT_CHECK else 1
    plan = build_plan(collection)
    if yt.cfg.musicbrainz:
        stats = enrich(plan, _musicbrainz(), progress=lambda msg: print(f"  {msg}", file=sys.stderr))
        found = "release matched" if stats["release"] else f"{stats['tracks']}/{stats['looked_up']} tracks matched"
        print(f"MusicBrainz: {found}", file=sys.stderr)

    if dry or library is None:
        _print_plan(plan)
        return 0

    if found := find_plan(library, plan.source_id):
        old_dir, existing = found
        known = {t.video_id for t in existing.tracks}
        new = sum(t.video_id not in known for t in plan.tracks)
        plan = merge_plans(existing, plan)
        gone = sum(not t.in_source for t in plan.tracks)
        print(f"existing album {old_dir.relative_to(library)}: {new} new, {gone} no longer in the source", file=sys.stderr)
        if report_only:
            return 0
        album_dir = relocate(old_dir, plan, library)
    else:
        if report_only:
            print(f"not in the library yet: {plan.folder}", file=sys.stderr)
            return 0
        album_dir = library / plan.folder
    _print_plan(plan)

    if cmd == "plan":
        path = save_plan(plan, album_dir)
        print(f"\nplan written to {path}\nedit it, then run: ytalbum download '{album_dir}'")
        return 0
    return _execute(plan, album_dir, yt)


def _execute(plan: AlbumPlan, album_dir: Path, yt: YouTube) -> int:
    todo = sum(t.state != "done" and t.in_source for t in plan.tracks)
    print(f"\ndownloading {todo} of {len(plan.tracks)} tracks into {album_dir}")

    def report(t: PlanTrack, what: str) -> None:
        status = {"downloaded": "ok  ", "failed": "FAIL"}.get(what, what)
        print(f"  {status} {t.number:02d} {t.artist} - {t.title}" + (f"  ({t.error})" if t.error and what == "failed" else ""))

    run(plan, album_dir, yt, on_track=report)
    failed = [t for t in plan.tracks if t.state != "done" and t.in_source]
    print(f"{len(plan.tracks) - len(failed)}/{len(plan.tracks)} tracks done" + (f", {len(failed)} not yet — run again to retry" if failed else ""))
    if any(t.error == BOT_CHECK for t in failed):
        return BLOCKED
    return 1 if failed else 0


# -- helpers -------------------------------------------------------------------------------


def _library(args: argparse.Namespace, cfg: config_mod.Config, required: bool) -> Path | None:
    library = args.library or cfg.library_root
    if library is None and required:
        print("no library root: run `ytalbum config --library PATH` or pass --library", file=sys.stderr)
    return library.expanduser() if library else None


def parse_pick(spec: str, count: int) -> list[int]:
    """'1,3-5' -> [0, 2, 3, 4] (0-based, in order, no duplicates). Raises ValueError."""
    if spec.strip().lower() == "all":
        return list(range(count))
    picked: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        for n in range(int(lo), int(hi or lo) + 1):
            if not 1 <= n <= count:
                raise ValueError(f"{n} is not between 1 and {count}")
            if n - 1 not in picked:
                picked.append(n - 1)
    return picked


def _choose(refs: list[SourceRef], args: argparse.Namespace) -> list[SourceRef]:
    spec = "all" if args.all else args.pick
    if spec is None:
        if not sys.stdin.isatty():
            print("\nchoose with --pick 1,3-5 or --all", file=sys.stderr)
            return []
        spec = input("\nwhich ones? (e.g. 1,3-5 / all / empty = none): ")
    try:
        return [refs[i] for i in parse_pick(spec, len(refs))]
    except ValueError as e:
        print(f"invalid choice: {e}", file=sys.stderr)
        return []


def _print_groups(groups: list[tuple[str, list[SourceRef]]], library: Path | None) -> None:
    have = {p.source_id for _, p in iter_plans(library)} if library and library.exists() else set()
    i = 0
    for label, refs in groups:
        print(f"\n{label}:")
        for r in refs:
            i += 1
            extra = []
            if r.tab == "search" and r.artist:
                extra.append(f"by {r.artist}")
            if r.count:
                extra.append(f"{r.count} tracks")
            print(f"  {i:3d}  {r.title}" + (f"   ({', '.join(extra)})" if extra else "") + ("   ✓ in library" if r.source_id in have else ""))


def _warn_js_runtime(cfg: config_mod.Config) -> None:
    if not cfg.resolved_js_runtime():
        print(
            "warning: no JavaScript runtime found (deno/node/bun/quickjs) — "
            "YouTube may hide formats or fail; see DESIGN.md §3.5",
            file=sys.stderr,
        )


def _print_plan(plan: AlbumPlan) -> None:
    prov = {k: PROV_MARK.get(v, v) for k, v in plan.provenance.items()}
    print(f"\n{plan.albumartist} — {plan.album}" + (f" ({plan.year})" if plan.year else ""))
    print(f"  kind: {plan.kind}   folder: {plan.folder}")
    print(f"  from: albumartist={prov.get('albumartist', '?')} album={prov.get('album', '?')}" + (f" year={prov['year']}" if "year" in prov else ""))
    for t in plan.tracks:
        marks = f"[{PROV_MARK.get(t.provenance.get('artist'), '?')}/{PROV_MARK.get(t.provenance.get('title'), '?')}]"
        state = "" if t.state == "pending" else f"  <{t.state}>"
        state += "" if t.in_source else "  <no longer in source>"
        print(f"  {t.number:02d}  {t.artist} - {t.title}  {marks}{state}")
    for s in plan.skipped:
        print(f"  --  skipped: {s['title']}  ({s['reason']})")
