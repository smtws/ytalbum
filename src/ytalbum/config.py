"""Configuration: one TOML file, overridable per run from the CLI."""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

# yt-dlp's order of preference (see `yt-dlp --help`, --js-runtimes)
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ytalbum" / "config.toml"


@dataclass
class Config:
    library_root: Path | None = None
    # yt-dlp needs a JS runtime for YouTube (DESIGN.md §3.5). None = autodetect.
    js_runtime: str | None = None
    js_runtime_path: str | None = None
    concurrency: int = 4

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
        concurrency=int(data.get("concurrency", 4)),
    )
    if root := data.get("library_root"):
        cfg.library_root = Path(root).expanduser()
    return cfg


def save_library_root(root: Path, path: Path | None = None) -> Path:
    """Set library_root in the config file, keeping any other lines."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [l for l in lines if not l.strip().startswith("library_root")]
    escaped = str(root).replace("\\", "\\\\").replace('"', '\\"')
    lines.insert(0, f'library_root = "{escaped}"')
    path.write_text("\n".join(lines) + "\n")
    return path
