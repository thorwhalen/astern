"""Tests for astern.store: Store round-trip, key suffix hiding, MemoryStore, mk_store."""

from __future__ import annotations

from pathlib import Path

from astern.store import MemoryStore, Store, data_dir, mk_store

# --- Store round trip -----------------------------------------------------------


def test_store_round_trips_json(tmp_path):
    s = Store(root=tmp_path / "root")
    s.sessions["abc"] = {"title": "hello", "n": 3}
    assert s.sessions["abc"] == {"title": "hello", "n": 3}


def test_store_round_trips_nested_key(tmp_path):
    s = Store(root=tmp_path / "root")
    s.findings["stats/abc123"] = [{"kind": "session_stats", "evidence": {"n_turns": 2}}]
    assert s.findings["stats/abc123"] == [
        {"kind": "session_stats", "evidence": {"n_turns": 2}}
    ]
    # physically nested on disk
    assert (tmp_path / "root" / "findings" / "stats" / "abc123.json").exists()


def test_store_json_suffix_hidden_on_disk_and_in_keys(tmp_path):
    s = Store(root=tmp_path / "root")
    s.sessions["abc"] = {"a": 1}
    on_disk = list((tmp_path / "root" / "sessions").iterdir())
    assert [p.name for p in on_disk] == ["abc.json"]
    assert list(s.sessions) == ["abc"]


def test_store_iteration_and_contains(tmp_path):
    s = Store(root=tmp_path / "root")
    s.sessions["a"] = {"x": 1}
    s.sessions["b"] = {"x": 2}
    assert set(s.sessions) == {"a", "b"}
    assert "a" in s.sessions
    assert "missing" not in s.sessions
    assert len(s.sessions) == 2


def test_store_delete(tmp_path):
    s = Store(root=tmp_path / "root")
    s.sessions["a"] = {"x": 1}
    del s.sessions["a"]
    assert "a" not in s.sessions


def test_store_five_kinds_are_independent(tmp_path):
    s = Store(root=tmp_path / "root")
    s.sessions["sid"] = {"kind": "session"}
    s.turns["sid"] = [{"index": 0}]
    s.findings["stats/sid"] = [{"kind": "stats"}]
    s.ledger["sid"] = {"n_turns": 1}
    s.judgments["stats/sid/0"] = {"text": "x"}
    assert set((tmp_path / "root").iterdir()) == {
        tmp_path / "root" / k
        for k in ("sessions", "turns", "findings", "ledger", "judgments")
    }


# --- MemoryStore -----------------------------------------------------------------


def test_memory_store_round_trips_in_process():
    s = MemoryStore()
    s.sessions["abc"] = {"title": "x"}
    assert s.sessions["abc"]["title"] == "x"


def test_memory_store_same_dict_across_accesses():
    s = MemoryStore()
    s.findings["lens/sid"] = []
    s.findings["lens/sid"].append({"a": 1})
    assert s.findings["lens/sid"] == [{"a": 1}]


def test_memory_store_kinds_are_independent_dicts():
    s = MemoryStore()
    s.sessions["k"] = "session-value"
    assert "k" not in s.turns


# --- mk_store() ------------------------------------------------------------------


def test_mk_store_with_store_instance_passthrough(tmp_path):
    s = Store(root=tmp_path / "root")
    assert mk_store(s) is s


def test_mk_store_with_str(tmp_path):
    s = mk_store(str(tmp_path / "as-str"))
    assert isinstance(s, Store)
    assert s.root == tmp_path / "as-str"


def test_mk_store_with_path(tmp_path):
    s = mk_store(tmp_path / "as-path")
    assert s.root == tmp_path / "as-path"


def test_mk_store_none_uses_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTERN_DATA_DIR", str(tmp_path / "env-root"))
    s = mk_store(None)
    assert s.root == tmp_path / "env-root"


def test_mk_store_none_without_env_uses_default(monkeypatch):
    monkeypatch.delenv("ASTERN_DATA_DIR", raising=False)
    s = mk_store(None)
    assert s.root == Path.home() / ".local" / "share" / "astern"


def test_data_dir_precedence_arg_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTERN_DATA_DIR", str(tmp_path / "env-root"))
    assert data_dir(tmp_path / "explicit-root") == tmp_path / "explicit-root"
