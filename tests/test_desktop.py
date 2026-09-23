"""The desktop launcher: its own window class, so the taskbar does not call it a browser."""

import pytest

from ytalbum import desktop


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_entry_asks_for_a_window_of_its_own():
    entry = desktop.render_entry("/usr/bin/google-chrome", "http://127.0.0.1:8765/")
    # the taskbar groups by the window class, so the launcher must claim it and set it
    assert "StartupWMClass=ytalbum" in entry
    assert "--class=ytalbum" in entry
    # ...which a browser only honours in a process of its own
    assert "--user-data-dir=" in entry
    assert "--app=http://127.0.0.1:8765/" in entry
    assert "Icon=ytalbum" in entry


def test_install_writes_launcher_and_icons(data_home):
    done = desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    entry = data_home / "applications" / "ytalbum.desktop"
    assert entry.exists() and "Exec=/usr/bin/google-chrome" in entry.read_text()
    assert any((data_home / "icons" / "hicolor").glob("*/apps/ytalbum.*"))
    assert desktop.profile_dir().is_dir()
    assert any("ytalbum.desktop" in line for line in done)


def test_install_without_a_browser_says_so(data_home, monkeypatch):
    monkeypatch.setattr(desktop, "find_browser", lambda: None)
    with pytest.raises(RuntimeError, match="no Chromium-based browser"):
        desktop.install("http://127.0.0.1:8765/")


def test_uninstall_keeps_the_profile_unless_asked(data_home):
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    desktop.uninstall()
    assert not desktop.desktop_file().exists()
    assert not any((data_home / "icons" / "hicolor").glob("*/apps/ytalbum.*"))
    assert desktop.profile_dir().is_dir()  # logins and window size live here

    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    desktop.uninstall(keep_profile=False)
    assert not desktop.profile_dir().exists()


def test_status_reports_both_states(data_home):
    assert "not installed" in desktop.status("http://127.0.0.1:8765/")
    desktop.install("http://127.0.0.1:8765/", browser="/usr/bin/google-chrome")
    assert "window class: ytalbum" in desktop.status("http://127.0.0.1:8765/")
