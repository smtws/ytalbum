"""Local PO-token server (bgutil HTTP mode): started on demand, stops itself when idle.

`ensure_server()` pings 127.0.0.1:<port>/ping; if nothing answers it starts a detached
watchdog (`python -m ytalbum.pot`), which runs `node build/main.js` and stops it once the
heartbeat file has not been touched for `idle` seconds. ytalbum touches the heartbeat on
every YouTube request and during downloads. If the server cannot be started, yt-dlp's
plugin falls back to script mode on its own.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

DEFAULT_PORT = 4416  # the plugin's default base_url
DEFAULT_IDLE = 300
START_TIMEOUT = 15.0


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ytalbum"


def heartbeat_path() -> Path:
    return cache_dir() / "pot-server.heartbeat"


def touch_heartbeat() -> None:
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def base_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def ping(port: int = DEFAULT_PORT, timeout: float = 1.0) -> dict | None:
    """The server's /ping answer if a bgutil server is listening, else None."""
    try:
        r = httpx.get(f"{base_url(port)}/ping", timeout=timeout)
        data = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) and "version" in data else None


def ensure_server(home: Path, node: str, port: int = DEFAULT_PORT, idle: int = DEFAULT_IDLE) -> bool:
    """Make sure a token server answers on `port`; start one if needed. True if it is up."""
    touch_heartbeat()
    if ping(port):
        return True
    log_file = cache_dir() / "pot-server.log"
    with open(log_file, "ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "ytalbum.pot", "--home", str(home), "--node", node, "--port", str(port), "--idle", str(idle)],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # survives ytalbum; stops itself when idle
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if ping(port, timeout=0.5):
            log.info("PO-token server started on port %d", port)
            return True
        time.sleep(0.3)
    log.warning("PO-token server did not start (see %s); falling back to script mode", log_file)
    return False


# -- the watchdog (runs detached) --------------------------------------------------------------


def watchdog(command: list[str], cwd: Path, idle: float, heartbeat: Path, poll: float = 10.0) -> int:
    """Run `command`; stop it when `heartbeat` is older than `idle` seconds or on SIGTERM."""
    child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL)
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping and child.poll() is None:
            try:
                age = time.time() - heartbeat.stat().st_mtime
            except FileNotFoundError:
                age = idle + 1
            if age > idle:
                print(f"{time.strftime('%F %T')} idle for {age:.0f}s, stopping", flush=True)
                break
            time.sleep(min(poll, max(0.1, idle - age)))
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return child.returncode or 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ytalbum.pot")
    p.add_argument("--home", type=Path, required=True, help="bgutil server dir (with build/main.js)")
    p.add_argument("--node", default="node")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--idle", type=float, default=DEFAULT_IDLE)
    args = p.parse_args(argv)
    print(f"{time.strftime('%F %T')} starting PO-token server on 127.0.0.1:{args.port}", flush=True)
    return watchdog(
        [args.node, "build/main.js", "--port", str(args.port), "--host", "127.0.0.1"],
        cwd=args.home,
        idle=args.idle,
        heartbeat=heartbeat_path(),
    )


if __name__ == "__main__":
    sys.exit(main())
