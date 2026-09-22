"""`ytalbum service install|uninstall|status`: the web UI as a socket-activated systemd user service.

systemd listens on the port; the first request starts `ytalbum serve --idle-exit …`, which
stops itself when idle and is started again by the next request. User level only (no root).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def status() -> str:
    socket = systemctl("is-active", f"{UNIT}.socket").stdout.strip() or "not installed"
    service = systemctl("is-active", f"{UNIT}.service").stdout.strip()
    return f"socket: {socket}   web UI process: {'running' if service == 'active' else 'stopped (starts on the next request)'}"
