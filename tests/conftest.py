"""Every test runs isolated: own cache/config dirs, no PO-token server, no lrclib.net."""

import pytest

import ytalbum.pot
import ytalbum.service


class NoLyrics:
    """What a Service gets instead of a real lrclib client: nothing is found, nothing is asked."""

    def get(self, artist, title, album=None, length=None):
        return None


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    started = []
    monkeypatch.setattr(ytalbum.pot, "ensure_server", lambda *a, **k: started.append(a) or False)
    # a Service builds its lyrics client itself; tests that want one pass a fake explicitly
    monkeypatch.setattr(ytalbum.service, "Lrclib", lambda *a, **k: NoLyrics())
    return started
