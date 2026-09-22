"""Command line: `ytalbum fetch|plan|download|config`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config as config_mod
from .download import find_plan, load_plan, relocate, run, save_plan
from .models import AlbumPlan, PlanTrack
from .plan import build_plan, merge_plans
from .youtube import NotSupported, YouTube

PROV_MARK = {"mb": "MB", "yt_music": "YTM", "yt_title": "title", "playlist": "playlist", "user": "user"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ytalbum", description="Turn a YouTube playlist into a tagged album.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="plan and download a playlist or video URL")
    f.add_argument("url")
    f.add_argument("--library", type=Path, help="library root (overrides the config file)")
    f.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    f.add_argument("--dump-collection", type=Path, metavar="FILE", help="also save what YouTube returned (for test fixtures)")

    pl = sub.add_parser("plan", help="write the plan into the album folder for editing, download nothing")
    pl.add_argument("url")
    pl.add_argument("--library", type=Path)

    d = sub.add_parser("download", help="download from an (edited) plan in an album folder")
    d.add_argument("album_dir", type=Path)

    c = sub.add_parser("config", help="show or set configuration")
    c.add_argument("--library", type=Path, help="set the library root")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = config_mod.load()

    try:
        match args.cmd:
            case "config":
                return _config(args, cfg)
            case "fetch" | "plan":
                return _fetch(args, cfg)
            case "download":
                return _download(args.album_dir, cfg)
    except NotSupported as e:
        print(f"not supported yet: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — run the same command again to resume", file=sys.stderr)
        return 130
    return 0


def _config(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    if args.library:
        path = config_mod.save_library_root(args.library.expanduser().resolve())
        print(f"library_root set in {path}")
        cfg = config_mod.load()
    runtime = cfg.resolved_js_runtime()
    print(f"config file:  {config_mod.config_path()}")
    print(f"library_root: {cfg.library_root or '(not set)'}")
    print(f"js runtime:   {' '.join(filter(None, runtime)) if runtime else 'NONE FOUND — install deno or node'}")
    return 0


def _fetch(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = args.library.expanduser() if args.library else cfg.library_root
    dry = getattr(args, "dry_run", False)
    if not library and not dry:
        print("no library root: run `ytalbum config --library PATH` or pass --library", file=sys.stderr)
        return 2
    _warn_js_runtime(cfg)

    yt = YouTube(cfg)
    print(f"reading {args.url} …", file=sys.stderr)
    collection = yt.fetch(args.url)
    if getattr(args, "dump_collection", None):
        args.dump_collection.write_text(json.dumps(collection.to_dict(), indent=2, ensure_ascii=False) + "\n")
    plan = build_plan(collection)

    if dry:
        _print_plan(plan)
        return 0

    library = library.expanduser()
    if found := find_plan(library, plan.source_id):
        old_dir, existing = found
        new = [t.video_id for t in plan.tracks if t.video_id not in {x.video_id for x in existing.tracks}]
        plan = merge_plans(existing, plan)
        album_dir = relocate(old_dir, plan, library)
        gone = sum(not t.in_source for t in plan.tracks)
        print(f"updating {album_dir}: {len(new)} new, {gone} no longer in the source (your edits are kept)", file=sys.stderr)
    else:
        album_dir = library / plan.folder
    _print_plan(plan)

    if args.cmd == "plan":
        path = save_plan(plan, album_dir)
        print(f"\nplan written to {path}\nedit it, then run: ytalbum download {album_dir}")
        return 0
    return _execute(plan, album_dir, yt)


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


def _execute(plan: AlbumPlan, album_dir: Path, yt: YouTube) -> int:
    todo = sum(t.state != "done" and t.in_source for t in plan.tracks)
    print(f"\ndownloading {todo} of {len(plan.tracks)} tracks into {album_dir}")

    def report(t: PlanTrack, what: str) -> None:
        status = {"downloaded": "ok  ", "failed": "FAIL"}.get(what, what)
        print(f"  {status} {t.number:02d} {t.artist} - {t.title}" + (f"  ({t.error})" if t.error and what == "failed" else ""))

    run(plan, album_dir, yt, on_track=report)
    failed = [t for t in plan.tracks if t.state != "done" and t.in_source]
    print(f"\n{len(plan.tracks) - len(failed)}/{len(plan.tracks)} tracks done" + (f", {len(failed)} failed — run again to retry" if failed else ""))
    return 1 if failed else 0


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
