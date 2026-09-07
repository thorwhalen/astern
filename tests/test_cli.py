"""Tests for the ``python -m astern`` CLI, exercised as a real subprocess.

Runs against *this* checkout: ``PYTHONPATH`` is pointed at the repo root so the
subprocess imports the local ``astern`` package rather than whatever is on the
machine's editable-install path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fixtures import write_home

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(args, *, home, data_dir):
    env = dict(os.environ)
    env["ASTERN_HOME"] = str(home)
    env["ASTERN_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "astern", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_lenses(tmp_path):
    home = tmp_path / ".claude"
    home.mkdir()
    proc = _run(["lenses"], home=home, data_dir=tmp_path / "data")
    assert proc.returncode == 0, proc.stderr
    assert "stats" in proc.stdout


def test_cli_sync_max_sessions(tmp_path):
    w = write_home(
        tmp_path,
        sessions=[
            {
                "sid": "cli-sess-1",
                "cwd": "/tmp/proj-cli",
                "turns": [{"prompt": "hi", "final_text": "hey"}],
            }
        ],
    )
    proc = _run(["sync", "--max-sessions", "1"], home=w.home, data_dir=tmp_path / "data")
    assert proc.returncode == 0, proc.stderr
    assert "stats" in proc.stdout
