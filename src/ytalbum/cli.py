"""Command line: `ytalbum fetch|plan|download|update|search|serve|config`. A thin layer over service.py."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import NOTICE
from . import config as config_mod
from .config import Config
from .download import PLAN_FILE
from .models import AlbumPlan, PlanTrack, SourceRef, kept
from .service import Service, collection_address, exit_code
from .sources import NotSupported

PROV_MARK = {"mb": "MB", "yt_music": "YTM", "yt_title": "title", "playlist": "playlist", "user": "user"}
BLOCKED = 3  # exit code: YouTube is refusing requests right now; stop asking


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
    f.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")
    f.add_argument("--dump-collection", type=Path, metavar="FILE", help="also save what YouTube returned (for test fixtures)")

    se = sub.add_parser("search", help="find an artist's albums on YouTube and pick which to fetch")
    se.add_argument("artist")
    se.add_argument("--library", type=Path)
    se.add_argument("--dry-run", action="store_true", help="print the plans, write nothing")
    se.add_argument("--all", action="store_true", help="take everything found")
    se.add_argument("--pick", metavar="SPEC", help="which ones, e.g. 1,3-5 (default: ask)")
    se.add_argument("--no-mb", action="store_true", help="skip MusicBrainz (lookups and the discography check)")
    se.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")

    pl = sub.add_parser("plan", help="write the plan into the album folder for editing, download nothing")
    pl.add_argument("url", nargs="?")
    pl.add_argument("--library", type=Path)
    pl.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    pl.add_argument("--verify", action="store_true",
                    help="read every plan in the library and report anything a rewrite would lose; writes nothing")

    d = sub.add_parser("download", help="download from an (edited) plan in an album folder")
    d.add_argument("album_dir", type=Path)
    d.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")

    pr = sub.add_parser("prune", help="delete the tracks that are no longer in the source playlist")
    pr.add_argument("album_dir", type=Path)
    pr.add_argument("--yes", action="store_true", help="do not ask")

    rp = sub.add_parser("repair", help="tidy artist names in the library, offline (one-off)")
    rp.add_argument("--library", type=Path)

    ly = sub.add_parser("lyrics", help="fetch lyrics for tracks that have none yet (.lrc beside the file + tag)")
    ly.add_argument("--library", type=Path)
    ly.add_argument("--artist", help="only this album artist")
    ly.add_argument("--refetch", action="store_true", help="look every track up again (keeps lyrics you wrote yourself)")
    ly.add_argument("--near", action="store_true",
                    help="afterwards, align lrclib's near misses to decide whether they are this recording's words")
    ly.add_argument("--dry-run", action="store_true", help="with --near: look up only, align nothing, write nothing")

    dl = sub.add_parser("delete", help="delete an album (or one track) — files are removed")
    dl.add_argument("album_dir", type=Path)
    dl.add_argument("--track", metavar="VIDEO_ID", help="delete only this track")
    dl.add_argument("--yes", action="store_true", help="do not ask")

    u = sub.add_parser("update", help="re-check every album in the library against its source")
    u.add_argument("--library", type=Path)
    u.add_argument("--dry-run", action="store_true", help="only report what changed")
    u.add_argument("--no-mb", action="store_true", help="skip the MusicBrainz lookup")
    u.add_argument("--no-lyrics", action="store_true", help="do not look lyrics up at lrclib.net")
    u.add_argument("--deep", action="store_true", help="read every album fully, even unchanged ones")

    sv = sub.add_parser("serve", help="web UI for the library (also installable as an app)")
    sv.add_argument("--library", type=Path)
    sv.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from other devices (no login!)")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--idle-exit", type=float, default=0, metavar="SECONDS", help="stop after this long without requests or jobs (for socket activation)")

    tm = sub.add_parser("timing-serve", help="run the local aligner as a small HTTP service for another machine")
    tm.add_argument("--host", default="0.0.0.0", help="0.0.0.0 by default: the point is to be reached from the LAN")
    tm.add_argument("--port", type=int, default=8770)
    tm.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")

    sd = sub.add_parser("service", help="run the web UI on demand via systemd (user level)")
    sd.add_argument("action", choices=("install", "uninstall", "status", "restart"))
    sd.add_argument("--force", action="store_true", help="restart even while a job is running")
    sd.add_argument("--port", type=int, default=None, help="install: the port to listen on (default 8765)")
    sd.add_argument("--idle-exit", type=int, default=None, metavar="SECONDS",
                    help="install: stop the service after this long without requests or jobs (default 900)")

    ap = sub.add_parser("app", help="desktop launcher with its own window, not another browser window")
    ap.add_argument("action", choices=("install", "uninstall", "status"))
    ap.add_argument("--port", type=int, default=None, help="port of the web UI (default: the installed service's)")
    ap.add_argument("--browser", help="which Chromium-based browser to use")
    ap.add_argument("--remove-profile", action="store_true", help="uninstall: also delete the app's browser profile")

    rc = sub.add_parser("recycle", help="what ytalbum moved aside instead of deleting")
    rc.add_argument("action", choices=["list", "restore", "empty"])
    rc.add_argument("entry", nargs="?", help="restore: the entry id, or enough of it to be unique")
    rc.add_argument("--library", type=Path)
    rc.add_argument("--older-than", type=float, metavar="DAYS",
                    help="empty: only entries older than this many days")

    c = sub.add_parser("config", help="show or set configuration")
    c.add_argument("--library", type=Path, help="set the library root")
    c.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", help="use a browser's YouTube login: gets past the bot check and unlocks age-restricted videos; 'none' to unset")
    c.add_argument("--cookies-file", type=Path, metavar="FILE", help="use an exported cookies.txt instead; 'none' to unset")
    c.add_argument("--lyrics", choices=("on", "off"), help="look lyrics up at lrclib.net when downloading")

    args = p.parse_args(argv)
    # stderr, so a piped stdout stays byte-identical to 0.9.0 — and suppressed, because the
    # least important line in the program must not be able to change what a command returns
    # (`recycle list | head` closes a pipe under us, and this runs before anything guards it).
    with contextlib.suppress(BrokenPipeError, OSError, ValueError):
        print(NOTICE, file=sys.stderr)
    sys.stdout.reconfigure(line_buffering=True)  # keep progress in order with stderr when piped
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config_mod.load()
    if getattr(args, "no_mb", False):
        cfg.musicbrainz = False
    if getattr(args, "no_lyrics", False):
        cfg.lyrics = False

    try:
        match args.cmd:
            case "config":
                return _config(args, cfg)
            case "recycle":
                return _recycle(args, cfg)
            case "plan" if args.verify:
                return _verify_plans(cfg, getattr(args, "library", None))
            case "fetch" | "plan":
                if not args.url:
                    print("give a URL, or --verify to check the plans already here", file=sys.stderr)
                    return 2
                return _fetch(args, cfg)
            case "search":
                return _search(args, cfg)
            case "download":
                return exit_code(_service(cfg, _library(args, cfg, required=False)).download_existing(args.album_dir))
            case "update":
                library = _library(args, cfg, required=True)
                return 2 if library is None else exit_code(_service(cfg, library).update_all(report_only=args.dry_run, deep=args.deep))
            case "serve":
                return _serve(args, cfg)
            case "timing-serve":
                from .timing_serve import serve as timing_serve

                return timing_serve(host=args.host, port=args.port, device=args.device)
            case "prune":
                return _prune(args, cfg)
            case "service":
                return _systemd(args, cfg)
            case "app":
                return _app(args)
            case "delete":
                return _delete(args, cfg)
            case "repair":
                library = _library(args, cfg, required=True)
                return 2 if library is None else exit_code(_service(cfg, library).repair())
            case "lyrics":
                library = _library(args, cfg, required=True)
                if library is None:
                    return 2
                service = _service(cfg, library)
                outcomes = service.fetch_lyrics(refetch=args.refetch, artist=args.artist)
                if args.near:
                    outcomes += service.check_near_lyrics_all(refetch=args.refetch, artist=args.artist,
                                                              dry_run=args.dry_run)
                return exit_code(outcomes)
    except NotSupported as e:
        print(f"not supported: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted — run the same command again to resume", file=sys.stderr)
        return 130
    return 0


# -- commands ----------------------------------------------------------------------------


def _service(cfg: config_mod.Config, library: Path | None) -> Service:
    if not cfg.resolved_js_runtime():
        print("warning: no JavaScript runtime found (deno/node/bun/quickjs) — YouTube may hide formats or fail; see DESIGN.md §3.5", file=sys.stderr)
    return Service(cfg, library, log=lambda s: print(s, file=sys.stderr), on_plan=_print_plan, on_track=_print_track)


def _config(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    changes = {}
    if args.library:
        changes["library_root"] = str(args.library.expanduser().resolve())
    if args.cookies_from_browser:
        changes["cookies_from_browser"] = None if args.cookies_from_browser == "none" else args.cookies_from_browser
    if args.cookies_file:
        changes["cookies_file"] = None if str(args.cookies_file) == "none" else str(args.cookies_file.expanduser().resolve())
    if args.lyrics:
        changes["lyrics"] = args.lyrics == "on"
    for name, value in changes.items():
        config_mod.save_setting(name, value)
    if changes:
        cfg = config_mod.load()
    runtime = cfg.resolved_js_runtime()
    print(f"config file:  {config_mod.config_path()}")
    print(f"library_root: {cfg.library_root or '(not set)'}")
    print(f"cookies:      {cfg.cookies_file or cfg.cookies_from_browser or '(none — age-restricted videos are skipped)'}")
    print(f"musicbrainz:  {'on' if cfg.musicbrainz else 'off'}")
    print(f"lyrics:       {'on (lrclib.net)' if cfg.lyrics else 'off'}")
    pot = cfg.resolved_pot_provider()
    if not pot or cfg.pot_mode == "off":
        print(f"po tokens:    {'off' if cfg.pot_mode == 'off' else '(no generator — some streams may be withheld; see README)'}")
    else:
        from .pot import ping

        running = ping(cfg.pot_port)
        mode = f"server on 127.0.0.1:{cfg.pot_port}, stops after {cfg.pot_idle}s idle" if cfg.pot_mode == "server" else "script"
        state = f" — running (v{running['version']})" if running else (" — started on demand" if cfg.pot_mode == "server" else "")
        print(f"po tokens:    {mode}{state}\n              {pot}")
    print(f"js runtime:   {' '.join(filter(None, runtime)) if runtime else 'NONE FOUND — install deno or node'}")
    # reported, not enforced: a library can be browsed, tagged, searched and have its lyrics fetched
    # with no ffmpeg at all. It is downloading and trimming that stop, and they stop at the moment
    # of use, which is a bad moment to find out (docs/qa-catalog.md, AP).
    if cfg.library_root and cfg.library_root.is_dir():
        from .recycle import total as bin_total

        count, size = bin_total(cfg.library_root)
        print(f"recycle bin:  {count} entr{'y' if count == 1 else 'ies'}, {size / 1e6:.1f} MB"
              + ("" if not count else " — `ytalbum recycle list`"))
    ffmpeg = cfg.resolved_ffmpeg()
    print(f"ffmpeg:       {ffmpeg or 'NOT FOUND — downloading and trimming will fail; apt install ffmpeg'}")
    return 0


def _recycle(args: argparse.Namespace, cfg: Config) -> int:
    """The bin: what is in it, putting one back, and emptying it — never automatically.

    `recycle list | head` closes the pipe under us, which Python reports as a BrokenPipeError with a
    traceback on the way out. A listing command has to survive being piped into `head`.
    """
    from . import recycle as bin_

    root = _library(args, cfg, required=True)
    if not root or not root.is_dir():
        return 2
    if args.action == "list":
        return _recycle_list(bin_, root)
    if args.action == "restore":
        if not args.entry:
            print("which one? `ytalbum recycle list` shows the ids", file=sys.stderr)
            return 2
        outcome = _service(cfg, root).restore(args.entry)
        if outcome.message:
            print(outcome.message, file=sys.stderr if outcome.status == "failed" else sys.stdout)
        return exit_code(outcome)
    gone, freed = bin_.empty(root, args.older_than)
    print(f"removed {gone} entr{'y' if gone == 1 else 'ies'} for good, {freed / 1e6:.1f} MB")
    return 0


def _recycle_list(bin_: Any, root: Path) -> int:
    try:
        found = bin_.entries(root)
        if not found:
            print("the recycle bin is empty")
            return 0
        for entry in found:
            print(f"{entry.id}  {entry.bytes / 1e6:6.1f} MB  {entry.describe()}")
        count, size = bin_.total(root)
        print(f"\n{count} entr{'y' if count == 1 else 'ies'}, {size / 1e6:.1f} MB — "
              f"nothing here is ever removed on its own")
    except BrokenPipeError:
        # `| head` closed the pipe. Point stdout at nothing so the interpreter's exit-time flush has
        # somewhere to go; closing it instead would be correct here and break anything capturing it.
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _verify_plans(cfg: Config, library: Path | None) -> int:
    """Read every plan and re-serialise it in memory: what would a rewrite lose? (DESIGN.md §6)

    Nothing is written. Additions are expected and not faults — a plan saved before a field existed
    gains it with its default — so only two things count: a key that disappears, or a value that
    changes. Unknown keys are listed because they say a newer ytalbum has been here.
    """
    root = library or cfg.library_root
    if not root or not root.is_dir():
        print("set the library first: ytalbum config --library PATH", file=sys.stderr)
        return 2
    paths = sorted(root.glob(f"*/*/{PLAN_FILE}"))
    identical = filled = 0
    faults: list[str] = []
    unknown: dict[str, int] = {}
    for path in paths:
        where = f"{path.parent.parent.name}/{path.parent.name}"
        try:
            before = json.loads(path.read_text(encoding="utf-8"))
            plan = AlbumPlan.from_dict(before)
        except (OSError, ValueError, TypeError) as e:
            faults.append(f"{where}: cannot be read — {e}")
            continue
        after = plan.to_dict()
        gone, changed = _plan_differences(before, after)
        for key in _plan_unknown(plan):
            unknown[key] = unknown.get(key, 0) + 1
        if gone or changed:
            faults.append(f"{where}: would lose {gone}" if gone else f"{where}: would change {changed[:3]}")
        elif json.dumps(after, indent=2, ensure_ascii=False) + "\n" == path.read_text(encoding="utf-8"):
            identical += 1
        else:
            filled += 1
    print(f"{len(paths)} plan(s): {identical} byte-identical, {filled} would gain default fields, "
          f"{len(faults)} would lose or change something")
    for key, n in sorted(unknown.items()):
        print(f"  unknown field {key!r} on {n} plan(s) — written by a newer ytalbum, carried through")
    for fault in faults:
        print(f"  {fault}")
    return 1 if faults else 0


def _plan_differences(before: Any, after: Any, path: str = "") -> tuple[list[str], list[str]]:
    gone: list[str] = []
    changed: list[str] = []
    if isinstance(before, dict) and isinstance(after, dict):
        gone += [f"{path}{k}" for k in before.keys() - after.keys()]
        for k in before.keys() & after.keys():
            g, c = _plan_differences(before[k], after[k], f"{path}{k}.")
            gone += g
            changed += c
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            changed.append(f"{path}length {len(before)} -> {len(after)}")
        for i, (b, a) in enumerate(zip(before, after, strict=False)):
            g, c = _plan_differences(b, a, f"{path}{i}.")
            gone += g
            changed += c
    elif before != after:
        changed.append(f"{path}{before!r} -> {after!r}")
    return gone, changed


def _plan_unknown(plan: AlbumPlan) -> list[str]:
    keys = list(kept(plan))
    for track in plan.tracks:
        keys += [f"tracks.{k}" for k in kept(track)]
    return sorted(set(keys))


def _fetch(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    dry = getattr(args, "dry_run", False)
    library = _library(args, cfg, required=not dry)
    if library is None and not dry:
        return 2
    service = _service(cfg, library)

    if not collection_address(args.url, cfg):
        outcome = service.fetch(args.url, dry=dry, plan_only=args.cmd == "plan", dump=getattr(args, "dump_collection", None))
        if outcome.status == "planned":
            print(f"\nplan written to {outcome.album_dir}/.ytalbum.json\nedit it, then run: ytalbum download '{outcome.album_dir}'")
        return exit_code(outcome)

    if args.cmd == "plan":
        print("`plan` takes one playlist; for a channel use `fetch --pick N --dry-run` first", file=sys.stderr)
        return 2
    groups = service.channel(args.url)
    if not groups:
        print("this channel has no releases or playlists", file=sys.stderr)
        return 1
    return _pick_and_fetch(service, groups, args, dry)


def _search(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=not args.dry_run)
    if library is None and not args.dry_run:
        return 2
    service = _service(cfg, library)
    result = service.search(args.artist)
    if not result.refs:
        print("nothing found", file=sys.stderr)
        return 1
    if result.channel_url:
        print(f"artist channel: {result.channel_url}", file=sys.stderr)
    return _pick_and_fetch(service, result.groups, args, args.dry_run, missing=result.missing)


def _pick_and_fetch(service: Service, groups, args, dry: bool, missing: list[str] = ()) -> int:
    refs = [r for _, group in groups for r in group]
    _print_groups(groups, service.library_source_ids())
    if missing:
        print(f"\nMusicBrainz lists {len(missing)} more studio album(s) not found on YouTube: " + "; ".join(missing))
    chosen = _choose(refs, args)
    return exit_code(service.fetch_many(chosen, dry=dry)) if chosen else 0


def _prune(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from .download import load_plan

    plan = load_plan(args.album_dir)
    if not plan:
        print(f"no plan in {args.album_dir}", file=sys.stderr)
        return 2
    gone = [t for t in plan.tracks if not t.in_source]
    if not gone:
        print("nothing to remove: every track is still in the source")
        return 0
    print("no longer in the source playlist — these files will be deleted:")
    for t in gone:
        print(f"  {t.number:02d} {t.artist} - {t.title}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("add --yes to confirm", file=sys.stderr)
            return 2
        if input("delete them? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            return 0
    # the library root is what tells prune where the recycle bin is; without it, it would fall
    # back to unlinking, which is exactly the behaviour slice 49 removed
    return exit_code(_service(cfg, _library(args, cfg, required=False)).prune(args.album_dir))


def _systemd(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from . import systemd

    given = [flag for flag, value in (("--port", args.port), ("--idle-exit", args.idle_exit)) if value is not None]
    if args.action != "install" and given:
        # they describe the units, which only `install` writes — silently doing nothing with them
        # is how you come to believe the service moved to another port
        print(f"{' and '.join(given)} only mean something for 'install': they are written into the units. "
              f"The running service's port is the installed one — 'ytalbum service status' shows it.", file=sys.stderr)
        return 2
    port, idle_exit = args.port or 8765, args.idle_exit or 900
    try:
        if args.action == "install":
            for line in systemd.install(cfg, port, idle_exit):
                print(line)
            print(f"ready: open http://localhost:{port}/ — the web UI starts on demand and stops after {idle_exit}s idle")
        elif args.action == "uninstall":
            for line in systemd.uninstall():
                print(line)
        elif args.action == "restart":
            for line in systemd.restart(args.force):
                print(line)
        print(systemd.status())
    except (ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    return 0


def _app(args: argparse.Namespace) -> int:
    from . import desktop, systemd

    url = f"http://127.0.0.1:{args.port or systemd.installed_port()}/"
    try:
        if args.action == "install":
            for line in desktop.install(url, args.browser):
                print(line)
            print("ready: 'ytalbum' is in the menu — its window is its own, not the browser's")
        elif args.action == "uninstall":
            for line in desktop.uninstall(keep_profile=not args.remove_profile):
                print(line)
        print(desktop.status(url))
    except (OSError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 2
    return 0


def _delete(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    from .download import load_plan

    plan = load_plan(args.album_dir)
    if not plan:
        print(f"no plan in {args.album_dir}", file=sys.stderr)
        return 2
    if args.track:
        track = next((t for t in plan.tracks if t.video_id == args.track), None)
        if not track:
            print(f"no track {args.track} in this album", file=sys.stderr)
            return 2
        what = f"the track “{track.artist} - {track.title}”"
    else:
        what = f"the album “{plan.albumartist} — {plan.album}” with {len(plan.tracks)} track(s)"
    print(f"about to delete {what} in {args.album_dir}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("add --yes to confirm", file=sys.stderr)
            return 2
        if input("delete? [y/N] ").strip().lower() not in ("y", "yes", "j", "ja"):
            return 0
    library = args.album_dir.resolve().parents[1]
    service = _service(cfg, library)
    outcome = service.delete_track(plan.source_id, args.track) if args.track else service.delete_album(plan.source_id)
    if outcome.message:
        print(outcome.message, file=sys.stderr)
    return exit_code(outcome)


def _serve(args: argparse.Namespace, cfg: config_mod.Config) -> int:
    library = _library(args, cfg, required=True)
    if library is None:
        return 2
    from .web import serve

    serve(cfg, library, host=args.host, port=args.port, idle_exit=args.idle_exit)
    return 0


# -- helpers -------------------------------------------------------------------------------


def _library(args: argparse.Namespace, cfg: config_mod.Config, required: bool) -> Path | None:
    library = getattr(args, "library", None) or cfg.library_root
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


def _print_groups(groups: list[tuple[str, list[SourceRef]]], have: set[str]) -> None:
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


def _print_track(t: PlanTrack, what: str) -> None:
    status = {"downloaded": "ok  ", "failed": "FAIL"}.get(what, what)
    print(f"  {status} {t.number:02d} {t.artist} - {t.title}" + (f"  ({t.error})" if t.error and what == "failed" else ""))


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
