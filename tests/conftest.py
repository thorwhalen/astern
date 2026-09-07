"""Shared pytest fixtures for the astern test suite.

Nothing here reaches the real ``~/.claude`` or ``~/.local/share/astern`` — every
test that needs a home or a store builds one under ``tmp_path`` (see
:mod:`fixtures`) or uses :class:`astern.store.MemoryStore`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# This worktree's astern package must win over any other checkout's editable
# install on sys.path (e.g. when several worktrees of this repo share one
# machine's site-packages entry) -- otherwise regular `import astern...` in test
# modules and pytest's `--doctest-modules` collection of astern/*.py disagree on
# which file backs the same module name and collection fails outright.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_wrong_astern = sys.modules.get("astern")
if _wrong_astern is not None and getattr(_wrong_astern, "__file__", None) != str(
    _ROOT / "astern" / "__init__.py"
):
    for _name in [n for n in sys.modules if n == "astern" or n.startswith("astern.")]:
        del sys.modules[_name]

import pytest

from astern.lenses import LENSES
from astern.store import Store


@pytest.fixture
def store(tmp_path):
    """A real, on-disk :class:`astern.store.Store` rooted under ``tmp_path``."""
    return Store(root=tmp_path / "astern_store")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Never let a test accidentally fall back to the real home or store."""
    monkeypatch.delenv("ASTERN_HOME", raising=False)
    monkeypatch.delenv("ASTERN_DATA_DIR", raising=False)


@pytest.fixture
def clean_lenses():
    """Snapshot the lens registry and restore it, for tests that register a fake lens."""
    before = dict(LENSES)
    yield LENSES
    LENSES.clear()
    LENSES.update(before)
