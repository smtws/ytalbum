"""The on-demand PO-token server: watchdog idle stop, on-demand start (no real server started)."""

import os
import sys
import time

from ytalbum.config import Config
from ytalbum.pot import watchdog
from ytalbum.youtube import YouTube


def test_watchdog_stops_the_child_when_idle(tmp_path):
    beat = tmp_path / "beat"
    beat.touch()
    old = time.time() - 100
    os.utime(beat, (old, old))
    start = time.monotonic()
    watchdog([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, idle=1, heartbeat=beat, poll=0.1)
    assert time.monotonic() - start < 10  # did not wait for the 60 s child


def test_watchdog_keeps_running_while_the_heartbeat_is_fresh(tmp_path):
    beat = tmp_path / "beat"
    beat.touch()
    start = time.monotonic()
    watchdog([sys.executable, "-c", "import time; time.sleep(1.5)"], cwd=tmp_path, idle=30, heartbeat=beat, poll=0.1)
    assert time.monotonic() - start >= 1.4  # the child ended on its own, it was not stopped


def test_server_is_ensured_once_per_instance_and_only_in_server_mode(tmp_path, isolated):
    home = tmp_path / "server"
    (home / "build").mkdir(parents=True)
    (home / "build" / "generate_once.js").write_text("")
    yt = YouTube(Config(pot_provider_home=home, js_runtime="node", js_runtime_path="/usr/bin/node"))
    yt.ensure_pot_server()
    yt.ensure_pot_server()
    assert len(isolated) == 1 and isolated[0][0] == home

    YouTube(Config(pot_provider_home=home, pot_mode="script")).ensure_pot_server()
    assert len(isolated) == 1
