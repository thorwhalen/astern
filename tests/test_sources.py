"""Tests for astern.sources: homes(), iter_session_files(), load_records()."""

from __future__ import annotations

import time
from pathlib import Path

from fixtures import write_home

from astern.sources import DFLT_HOME, Home, homes, iter_session_files, load_records

# --- homes() -----------------------------------------------------------------


def test_homes_none_uses_default(monkeypatch):
    monkeypatch.delenv("ASTERN_HOME", raising=False)
    hs = homes(None)
    assert len(hs) == 1
    assert hs[0].path == DFLT_HOME


def test_homes_none_uses_env_var(monkeypatch, tmp_path):
    env_home = tmp_path / ".claude-env"
    monkeypatch.setenv("ASTERN_HOME", str(env_home))
    hs = homes(None)
    assert len(hs) == 1
    assert hs[0].path == env_home
    assert hs[0].name == "claude-env"


def test_homes_single_string(tmp_path):
    p = tmp_path / ".claude-x"
    hs = homes(str(p))
    assert len(hs) == 1
    assert hs[0].path == p
    assert hs[0].name == "claude-x"


def test_homes_single_path(tmp_path):
    p = tmp_path / ".claude-y"
    hs = homes(p)
    assert hs[0].path == p


def test_homes_iterable(tmp_path):
    p1, p2 = tmp_path / ".claude-1", tmp_path / ".claude-2"
    hs = homes([p1, p2])
    assert [h.path for h in hs] == [p1, p2]
    assert [h.name for h in hs] == ["claude-1", "claude-2"]


def test_homes_passthrough_home_instance(tmp_path):
    h = Home(name="custom", path=tmp_path)
    hs = homes(h)
    assert hs == [h]


def test_homes_expands_user(monkeypatch):
    hs = homes("~/.claude-expand-test")
    assert hs[0].path == Path.home() / ".claude-expand-test"


# --- iter_session_files() -----------------------------------------------------


def _session_spec(sid, cwd, *, mtime=None, **extra):
    spec = {"sid": sid, "cwd": cwd, "turns": [{"prompt": "hi", "final_text": "hey"}]}
    if mtime is not None:
        spec["mtime"] = mtime
    spec.update(extra)
    return spec


def test_iter_session_files_newest_first(tmp_path):
    now = time.time()
    w = write_home(
        tmp_path,
        sessions=[
            _session_spec("s-old", "/tmp/proj-a", mtime=now - 1000),
            _session_spec("s-mid", "/tmp/proj-b", mtime=now - 500),
            _session_spec("s-new", "/tmp/proj-c", mtime=now - 10),
        ],
    )
    sids = [sf.session_id for sf in iter_session_files(w.home)]
    assert sids == ["s-new", "s-mid", "s-old"]


def test_iter_session_files_since_days(tmp_path):
    now = time.time()
    w = write_home(
        tmp_path,
        sessions=[
            _session_spec("s-recent", "/tmp/proj-a", mtime=now - 3600),  # 1h ago
            _session_spec("s-ancient", "/tmp/proj-b", mtime=now - 30 * 86400),  # 30d ago
        ],
    )
    sids = {sf.session_id for sf in iter_session_files(w.home, since_days=1)}
    assert sids == {"s-recent"}


def test_iter_session_files_projects_filter(tmp_path):
    w = write_home(
        tmp_path,
        sessions=[
            _session_spec("s-a", "/tmp/proj-alpha"),
            _session_spec("s-b", "/tmp/proj-beta"),
        ],
    )
    sids = {sf.session_id for sf in iter_session_files(w.home, projects="alpha")}
    assert sids == {"s-a"}


def test_iter_session_files_max_sessions(tmp_path):
    now = time.time()
    w = write_home(
        tmp_path,
        sessions=[
            _session_spec("s-1", "/tmp/p1", mtime=now - 30),
            _session_spec("s-2", "/tmp/p2", mtime=now - 20),
            _session_spec("s-3", "/tmp/p3", mtime=now - 10),
        ],
    )
    sfs = list(iter_session_files(w.home, max_sessions=2))
    assert len(sfs) == 2
    assert [sf.session_id for sf in sfs] == ["s-3", "s-2"]


def test_iter_session_files_kinds_default_session_only(tmp_path):
    w = write_home(
        tmp_path,
        sessions=[_session_spec("s-parent", "/tmp/proj-with-sub", with_subagent=True)],
    )
    sfs = list(iter_session_files(w.home))
    assert [sf.session_id for sf in sfs] == ["s-parent"]
    assert sfs[0].kind == "session"
    assert sfs[0].parent_id is None


def test_iter_session_files_kinds_includes_subagent(tmp_path):
    w = write_home(
        tmp_path,
        sessions=[_session_spec("s-parent", "/tmp/proj-with-sub", with_subagent=True)],
    )
    sfs = list(iter_session_files(w.home, kinds=("session", "subagent")))
    kinds = {sf.kind for sf in sfs}
    assert kinds == {"session", "subagent"}
    sub = next(sf for sf in sfs if sf.kind == "subagent")
    assert sub.parent_id == "s-parent"
    assert sub.session_id == "agent-x"


def test_iter_session_files_kinds_subagent_only(tmp_path):
    w = write_home(
        tmp_path,
        sessions=[_session_spec("s-parent", "/tmp/proj-with-sub", with_subagent=True)],
    )
    sfs = list(iter_session_files(w.home, kinds=("subagent",)))
    assert [sf.kind for sf in sfs] == ["subagent"]


def test_iter_session_files_fingerprint_and_home_name(tmp_path):
    w = write_home(tmp_path, sessions=[_session_spec("s-1", "/tmp/proj")])
    sf = next(iter_session_files(w.home))
    assert sf.home == w.home.name.lstrip(".")
    assert sf.fingerprint == {"size": sf.size, "mtime": sf.mtime}
    assert sf.size > 0


def test_iter_session_files_missing_home_is_empty(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list(iter_session_files(missing)) == []


# --- load_records() -----------------------------------------------------------


def test_load_records_tolerates_blank_and_malformed_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "user", "message": {"content": "hi"}}\n'
        "\n"
        "   \n"
        "not json at all {{{\n"
        "[1, 2, 3]\n"
        '{"type": "assistant", "message": {"content": []}}\n',
        encoding="utf-8",
    )
    recs = load_records(path)
    assert [r["type"] for r in recs] == ["user", "assistant"]


def test_load_records_missing_file_returns_empty(tmp_path):
    assert load_records(tmp_path / "nope.jsonl") == []


def test_load_records_preserves_unknown_types(tmp_path):
    path = tmp_path / "t2.jsonl"
    path.write_text(
        '{"type": "some-future-record-type", "payload": 42}\n', encoding="utf-8"
    )
    recs = load_records(path)
    assert recs == [{"type": "some-future-record-type", "payload": 42}]
