"""Every test runs isolated: its own cache/config dirs, and no real PO-token server is started."""

import pytest

import ytalbum.pot


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    started = []
    monkeypatch.setattr(ytalbum.pot, "ensure_server", lambda *a, **k: started.append(a) or False)
    return started
