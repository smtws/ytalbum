"""Configuration: one TOML file, overridable per run from the CLI."""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

# yt-dlp's order of preference (see `yt-dlp --help`, --js-runtimes)
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


# where yt-dlp looks for each browser's profile (Linux), so we only offer what exists
BROWSER_DIRS = {
    "firefox": ("~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox", "~/.var/app/org.mozilla.firefox/.mozilla/firefox"),
    "chrome": ("~/.config/google-chrome",),
    "chromium": ("~/.config/chromium", "~/snap/chromium/common/chromium"),
    "brave": ("~/.config/BraveSoftware/Brave-Browser",),
    "edge": ("~/.config/microsoft-edge",),
    "vivaldi": ("~/.config/vivaldi",),
    "opera": ("~/.config/opera",),
}


def detect_browsers() -> list[str]:
    """Browsers with a profile on this machine, in yt-dlp's naming."""
    return [name for name, dirs in BROWSER_DIRS.items() if any(Path(d).expanduser().is_dir() for d in dirs)]


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ytalbum" / "config.toml"


@dataclass
class Config:
    library_root: Path | None = None
    # yt-dlp needs a JS runtime for YouTube (DESIGN.md §3.5). None = autodetect.
    js_runtime: str | None = None
    js_runtime_path: str | None = None
    concurrency: int = 2  # parallel YouTube requests; more trips YouTube's bot check sooner
    musicbrainz: bool = True
    # opt-in, only needed for age-restricted videos (DESIGN.md §7). yt-dlp writes
    # refreshed cookies back into cookies_file.
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None  # "firefox", "chrome", "chrome:Profile 1", …

    def resolved_js_runtime(self) -> tuple[str, str | None] | None:
        """(name, path) of the runtime to hand to yt-dlp, or None if none is available."""
        if self.js_runtime:
            return self.js_runtime, self.js_runtime_path
        for name in JS_RUNTIMES:
            if path := shutil.which(name):
                return name, path
        return None


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    cfg = Config(
        js_runtime=data.get("js_runtime"),
        js_runtime_path=data.get("js_runtime_path"),
        concurrency=int(data.get("concurrency", 2)),
        musicbrainz=bool(data.get("musicbrainz", True)),
    )
    if root := data.get("library_root"):
        cfg.library_root = Path(root).expanduser()
    if cookies := data.get("cookies_file"):
        cfg.cookies_file = Path(cookies).expanduser()
    cfg.cookies_from_browser = data.get("cookies_from_browser") or None
    return cfg


def save_setting(name: str, value: str | None, path: Path | None = None) -> Path:
    """Set (or with None: remove) one top-level string setting, keeping all other lines."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [l for l in lines if l.split("=")[0].strip() != name]
    if value is not None:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.insert(0, f'{name} = "{escaped}"')
    path.write_text("\n".join(lines) + "\n")
    return path


def save_library_root(root: Path, path: Path | None = None) -> Path:
    return save_setting("library_root", str(root), path)
