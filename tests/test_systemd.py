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
