"""`ytalbum app install|uninstall`: a desktop launcher that is its own window, not a browser tab.

A PWA installed from the browser keeps the browser's window class (Chrome reports
WM_CLASS "crx_<app-id>", "Google-chrome"), and desktops group by that class - so the app
lands in the taskbar as another browser window, with the browser's icon.

Nothing in the web manifest can change that: the class is set by the browser process.
A window with its own class needs its own browser process, which means `--class` plus a
profile directory of its own. That is what this launcher does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

APP_ID = "ytalbum"
ICON_SIZES = (16, 32, 48, 64, 128, 256, 512)
BROWSERS = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def profile_dir() -> Path:
    """The app's own browser profile - what makes `--class` stick (see the module docstring)."""
    return data_home() / f"{APP_ID}-browser"


def desktop_file() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def find_browser() -> str | None:
    return next((path for name in BROWSERS if (path := shutil.which(name))), None)


def render_icons(svg: Path) -> list[Path]:
    """Rasterise the app icon into the icon theme. Without a converter, the SVG is used as is."""
    icons = data_home() / "icons" / "hicolor"
    written = []
    if magick := shutil.which("magick") or shutil.which("convert"):
        for size in ICON_SIZES:
            out = icons / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd = [magick, "-background", "none", str(svg), "-resize", f"{size}x{size}", str(out)]
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                written.append(out)
    if not written:
        out = icons / "scalable" / "apps" / f"{APP_ID}.svg"
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(svg, out)
        written.append(out)
    return written


def render_entry(browser: str, url: str) -> str:
    # --class sets the window class the taskbar groups by; it is only honoured by a browser
    # process of its own, hence --user-data-dir
    exec_line = (
        f"{browser} --user-data-dir={profile_dir()} --class={APP_ID} "
        f"--app={url} --no-first-run --no-default-browser-check"
    )
    return f"""[Desktop Entry]
Type=Application
Version=1.0
Name=ytalbum
GenericName=Music downloader
Comment=YouTube playlists as properly tagged albums
Exec={exec_line}
Icon={APP_ID}
Terminal=false
Categories=AudioVideo;Audio;Network;
StartupWMClass={APP_ID}
"""


def install(url: str, browser: str | None = None) -> list[str]:
    """Write the launcher and its icons. Returns what was done, for the user."""
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError("no Chromium-based browser found (tried: " + ", ".join(BROWSERS) + ")")
    done = []
    for icon in render_icons(Path(__file__).parent / "webui" / "icon.svg"):
        done.append(f"wrote {icon}")
    desktop_file().parent.mkdir(parents=True, exist_ok=True)
    desktop_file().write_text(render_entry(browser, url))
    desktop_file().chmod(0o755)
    done.append(f"wrote {desktop_file()}")
    profile_dir().mkdir(parents=True, exist_ok=True)
    if update := shutil.which("update-desktop-database"):
        subprocess.run([update, str(desktop_file().parent)], capture_output=True)
        done.append("update-desktop-database")
    return done


def uninstall(keep_profile: bool = True) -> list[str]:
    done = []
    if desktop_file().exists():
        desktop_file().unlink()
        done.append(f"removed {desktop_file()}")
    icons = data_home() / "icons" / "hicolor"
    for path in sorted(icons.glob(f"*/apps/{APP_ID}.*")):
        path.unlink()
        done.append(f"removed {path}")
    if not keep_profile and profile_dir().exists():
        shutil.rmtree(profile_dir())
        done.append(f"removed {profile_dir()}")
    if update := shutil.which("update-desktop-database"):
        subprocess.run([update, str(desktop_file().parent)], capture_output=True)
    return done


def status(url: str) -> str:
    if not desktop_file().exists():
        return "launcher: not installed (ytalbum app install)"
    return f"launcher: {desktop_file()}   window class: {APP_ID}   opens {url}"
