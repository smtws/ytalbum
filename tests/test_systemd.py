"""Socket activation: unit files, and serving on a socket handed over like systemd does."""

import socket
import threading

import httpx
import pytest

from ytalbum.config import Config
from ytalbum.systemd import install, render_units
from ytalbum.web import App


def test_units_start_the_service_on_demand_with_node_on_the_path():
    units = render_units(Config(js_runtime="node", js_runtime_path="/opt/node/bin/node"), port=9000, idle_exit=600)
    assert "ListenStream=127.0.0.1:9000" in units["ytalbum.socket"]
    service = units["ytalbum.service"]
    assert "serve --idle-exit 600" in service
    assert "Environment=PATH=/opt/node/bin:" in service


def test_install_refuses_without_a_library():
    with pytest.raises(ValueError, match="library"):
        install(Config())


def test_server_on_an_inherited_socket_and_idle_accounting(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    app = App(Config(musicbrainz=False), tmp_path)
    srv = app.make_server(sock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = sock.getsockname()[1]
    assert httpx.get(f"http://127.0.0.1:{port}/api/state").json()["albums"] == []
    assert app.idle_for() < 1
    srv.shutdown()
    srv.server_close()


def test_restart_refuses_while_a_job_runs(monkeypatch):
    import subprocess

    import ytalbum.systemd as sd

    monkeypatch.setattr(sd, "busy", lambda port=None: True)
    calls = []
    monkeypatch.setattr(sd, "systemctl", lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    with pytest.raises(RuntimeError, match="job is running"):
        sd.restart()
    assert calls == []
    sd.restart(force=True)  # only on purpose
    assert calls == [("restart", "ytalbum.service")]


def test_installed_port_is_read_from_the_unit(tmp_path, monkeypatch):
    import ytalbum.systemd as sd

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "systemd" / "user").mkdir(parents=True)
    (tmp_path / "systemd" / "user" / "ytalbum.socket").write_text("[Socket]\nListenStream=127.0.0.1:9123\n")
    assert sd.installed_port() == 9123


# -- flags that only describe the units ---------------------------------------------------


@pytest.mark.parametrize("action", ["restart", "status", "uninstall"])
@pytest.mark.parametrize("flag", [["--port", "9000"], ["--idle-exit", "60"]])
def test_a_flag_that_cannot_work_is_refused_not_ignored(action, flag, capsys, monkeypatch, tmp_path):
    """`service restart --port 9000` did nothing with the port — and said nothing either."""
    import ytalbum.cli as cli
    import ytalbum.systemd as sd

    monkeypatch.setattr(sd, "restart", lambda force=False: pytest.fail("must not act"))
    monkeypatch.setattr(sd, "uninstall", lambda: pytest.fail("must not act"))
    monkeypatch.setattr(sd, "status", lambda: pytest.fail("must not act"))
    assert cli.main(["service", action, *flag]) == 2
    message = capsys.readouterr().err
    assert flag[0] in message and "install" in message


def test_install_still_takes_both_flags(monkeypatch, capsys, tmp_path):
    import ytalbum.cli as cli
    import ytalbum.systemd as sd

    seen = {}
    monkeypatch.setattr(sd, "install", lambda cfg, port, idle: seen.update(port=port, idle=idle) or [])
    monkeypatch.setattr(sd, "status", lambda: "")
    assert cli.main(["service", "install", "--port", "9000", "--idle-exit", "60"]) == 0
    assert seen == {"port": 9000, "idle": 60}

    seen.clear()
    assert cli.main(["service", "install"]) == 0
    assert seen == {"port": 8765, "idle": 900}  # the documented defaults, unchanged
