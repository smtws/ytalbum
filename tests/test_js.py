"""The page's own logic, run by node. One command still runs everything (DESIGN.md §9.33).

`src/ytalbum/webui/logic.mjs` holds what the page computes rather than draws; `tests/js/` checks
it with node's built-in runner — no npm dependency, no build step. This file is the bridge: it
shells out, so `uv run pytest` covers both sides, and skips with a reason where node is missing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ytalbum.models import PlanTrack, Provenance
from ytalbum.plan import trimmed_gap

ROOT = Path(__file__).parent.parent
SHARED = Path(__file__).parent / "shared"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed: the page's own tests need it")
def test_the_pages_logic_passes_its_own_tests():
    run = subprocess.run(["node", "--test", "tests/js/*.test.mjs"], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout
    assert "# pass 0" not in run.stdout  # a glob that matched nothing would also "pass"


def test_the_shared_table_is_what_python_computes():
    """The same cases the JS side runs: `trimmed_gap` and `trimTarget` cannot drift apart."""
    cases = json.loads((SHARED / "trim_target.json").read_text())["cases"]
    assert len(cases) >= 10
    for case in cases:
        track = PlanTrack(video_id="x" * 11, number=1, artist="a", title="t", filename="f.opus",
                          provenance={"title": Provenance.MB})
        track.duration = case.get("duration")
        track.mb_length, track.lyrics_length = case.get("mb"), case.get("lrclib")
        track.file_length = case.get("file_length")
        kept, gap = trimmed_gap(track, case.get("start"), case.get("end"))
        assert kept == case["kept"], case["why"]
        assert gap == case["gap"], case["why"]
