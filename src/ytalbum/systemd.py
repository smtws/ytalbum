"""`ytalbum service install|uninstall|status`: the web UI as a socket-activated systemd user service.

systemd listens on the port; the first request starts `ytalbum serve --idle-exit …`, which
stops itself when idle and is started again by the next request. User level only (no root).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from .config import Config

UNIT = "ytalbum"
DEFAULT_IDLE_EXIT = 900


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "systemd" / "user"


def render_units(cfg: Config, port: int = 8765, idle_exit: int = DEFAULT_IDLE_EXIT) -> dict[str, str]:
    exe = Path(sys.prefix) / "bin" / "ytalbum"
    path = ["/usr/local/bin", "/usr/bin", "/bin"]
    if node := cfg.resolved_node():  # systemd does not see nvm's PATH; yt-dlp and the token server need node
        path.insert(0, str(Path(node).parent))
    return {
        f"{UNIT}.socket": f"""[Unit]
Description=ytalbum web UI (listens, starts the service on demand)

[Socket]
ListenStream=127.0.0.1:{port}

[Install]
WantedBy=sockets.target
""",
        f"{UNIT}.service": f"""[Unit]
Description=ytalbum web UI (started by ytalbum.socket, stops itself when idle)
Requires={UNIT}.socket
After={UNIT}.socket

[Service]
ExecStart={exe} serve --idle-exit {idle_exit}
Environment=PATH={":".join(path)}
Environment=PYTHONUNBUFFERED=1
""",
    }


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(cfg: Config, port: int = 8765, idle_exit: int = DEFAULT_IDLE_EXIT) -> list[str]:
    """Write the units, enable and start the socket. Returns what was done, for the user."""
    if not cfg.library_root:
        raise ValueError("set the library first: ytalbum config --library PATH")
    done = []
    unit_dir().mkdir(parents=True, exist_ok=True)
    for name, text in render_units(cfg, port, idle_exit).items():
        (unit_dir() / name).write_text(text)
        done.append(f"wrote {unit_dir() / name}")
    for args in (("daemon-reload",), ("enable", "--now", f"{UNIT}.socket")):
        r = systemctl(*args)
        if r.returncode:
            raise RuntimeError(f"systemctl --user {' '.join(args)}: {r.stderr.strip()}")
        done.append(f"systemctl --user {' '.join(args)}")
    return done


def uninstall() -> list[str]:
    done = []
    for args in (("disable", "--now", f"{UNIT}.socket"), ("stop", f"{UNIT}.service")):
        systemctl(*args)
        done.append(f"systemctl --user {' '.join(args)}")
    for name in (f"{UNIT}.socket", f"{UNIT}.service"):
        path = unit_dir() / name
        if path.exists():
            path.unlink()
            done.append(f"removed {path}")
    systemctl("daemon-reload")
    return done


def installed_port(default: int = 8765) -> int:
    path = unit_dir() / f"{UNIT}.socket"
    match = re.search(r"ListenStream=.*?:(\d+)", path.read_text()) if path.exists() else None
    return int(match[1]) if match else default


def busy(port: int | None = None) -> bool:
    """True if the running web UI has a queued or running job (nothing to interrupt if not)."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port or installed_port()}/api/state", timeout=2)
        state = r.json()
        return bool(state.get("busy_write", state.get("busy")))  # a running search is cheap to lose
    except (httpx.HTTPError, ValueError):
        return False


def restart(force: bool = False) -> list[str]:
    """Restart the service, but never while it is working (that would kill the job)."""
    if not force and busy():
        raise RuntimeError("a job is running — wait for it, cancel it in the web UI, or use --force")
    r = systemctl("restart", f"{UNIT}.service")
    if r.returncode:
        raise RuntimeError(f"systemctl --user restart: {r.stderr.strip()}")
    return [f"systemctl --user restart {UNIT}.service"]


def status() -> str:
    socket = systemctl("is-active", f"{UNIT}.socket").stdout.strip() or "not installed"
    service = systemctl("is-active", f"{UNIT}.service").stdout.strip()
    return f"socket: {socket}   web UI process: {'running' if service == 'active' else 'stopped (starts on the next request)'}"
