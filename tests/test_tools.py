"""Tests for astern.tools: sync's idempotency, sessions(), show(), lenses(), _run_lens()."""

from __future__ import annotations

import time

import pytest
from fixtures import mk_records, write_home

from astern import tools
from astern.lenses import finding, lens
from astern.store import MemoryStore
from astern.turns import iter_turns


def _proj_spec(sid, cwd, mtime=None):
    spec = {"sid": sid, "cwd": cwd, "turns": [{"prompt": "hi", "final_text": "hey"}]}
    if mtime is not None:
        spec["mtime"] = mtime
    return spec


# --- sync(): first run reads all, second run reads none -----------------------


def test_sync_first_run_reads_all_and_runs_stats(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("s1", "/tmp/proj-a"), _proj_spec("s2", "/tmp/proj-b")])
    result = tools.sync(str(w.home), store=store)
    assert result["seen"] == 2
    assert result["read"] == 2
    assert result["unchanged"] == 0
    assert result["lenses"]["stats"]["ran"] == 2
    assert result["lenses"]["stats"]["skipped"] == 0
    assert result["lenses"]["stats"]["findings"] == 2
    assert "s1" in store.sessions and "s2" in store.sessions
    assert store.findings["stats/s1"][0]["kind"] == "session_stats"


def test_sync_second_run_reads_none_and_skips_every_lens(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("s1", "/tmp/proj-a"), _proj_spec("s2", "/tmp/proj-b")])
    tools.sync(str(w.home), store=store)
    result = tools.sync(str(w.home), store=store)
    assert result["read"] == 0
    assert result["unchanged"] == 2
    assert result["lenses"]["stats"]["ran"] == 0
    assert result["lenses"]["stats"]["skipped"] == 2


# --- sync(): a grown session is re-read and its finding updated ----------------


def test_sync_grown_session_is_reread_and_stats_updated(tmp_path, store):
    cwd = "/tmp/proj-grow"
    now = time.time()
    w = write_home(tmp_path, sessions=[_proj_spec("s-grow", cwd, mtime=now - 100)])
    r1 = tools.sync(str(w.home), store=store)
    assert r1["read"] == 1
    assert store.findings["stats/s-grow"][0]["evidence"]["n_turns"] == 1

    # session file grows: a second turn is appended and the mtime bumped forward.
    from fixtures import append_records

    append_records(
        w.paths["s-grow"], "s-grow", cwd,
        turns=[{"prompt": "more", "final_text": "more done"}],
        mtime=now + 100,
    )
    r2 = tools.sync(str(w.home), store=store)
    assert r2["read"] == 1
    assert r2["unchanged"] == 0
    assert r2["lenses"]["stats"]["ran"] == 1
    assert len(store.turns["s-grow"]) == 2
    assert store.findings["stats/s-grow"][0]["evidence"]["n_turns"] == 2

    # a third, unchanged sync now skips again.
    r3 = tools.sync(str(w.home), store=store)
    assert r3["read"] == 0
    assert r3["lenses"]["stats"]["skipped"] == 1


# --- sync(): force=True re-runs -------------------------------------------------


def test_sync_force_rereads_and_reruns_even_when_unchanged(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("s1", "/tmp/proj-a")])
    tools.sync(str(w.home), store=store)
    tools.sync(str(w.home), store=store)  # now fully skipped
    result = tools.sync(str(w.home), store=store, force=True)
    assert result["read"] == 1
    assert result["lenses"]["stats"]["ran"] == 1
    assert result["lenses"]["stats"]["skipped"] == 0


# --- sync(): lenses='none' -------------------------------------------------------


def test_sync_lenses_none_runs_no_lens(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("s1", "/tmp/proj-a")])
    result = tools.sync(str(w.home), store=store, lenses="none")
    assert result["read"] == 1
    assert result["lenses"] == {}
    assert "stats/s1" not in store.findings


# --- sync(): max_sessions -------------------------------------------------------


def test_sync_max_sessions_caps_how_many_are_read(tmp_path, store):
    now = time.time()
    w = write_home(
        tmp_path,
        sessions=[
            _proj_spec("s1", "/tmp/proj-a", mtime=now - 30),
            _proj_spec("s2", "/tmp/proj-b", mtime=now - 20),
            _proj_spec("s3", "/tmp/proj-c", mtime=now - 10),
        ],
    )
    result = tools.sync(str(w.home), store=store, max_sessions=1)
    assert result["seen"] == 1
    assert result["session_ids"] == ["s3"]


# --- sessions() ------------------------------------------------------------------


def test_sessions_lists_synced_sessions(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("s1", "/tmp/proj-alpha")])
    tools.sync(str(w.home), store=store)
    result = tools.sessions(store=store)
    assert result["n"] == 1
    row = result["sessions"][0]
    assert row["session_id"] == "s1"
    assert row["project"] == "proj-alpha"
    assert row["n_turns"] == 1
    assert row["lenses"]["stats"] == 1


def test_sessions_project_filter(tmp_path, store):
    w = write_home(
        tmp_path,
        sessions=[_proj_spec("s1", "/tmp/proj-alpha"), _proj_spec("s2", "/tmp/proj-beta")],
    )
    tools.sync(str(w.home), store=store)
    result = tools.sessions(store=store, project="alpha")
    assert result["n"] == 1
    assert result["sessions"][0]["session_id"] == "s1"


# --- show() ------------------------------------------------------------------------


def test_show_resolves_unique_prefix(tmp_path, store):
    w = write_home(tmp_path, sessions=[_proj_spec("abc111", "/tmp/proj-a")])
    tools.sync(str(w.home), store=store)
    result = tools.show("abc1", store=store)
    assert result["session"]["session_id"] == "abc111"
    assert result["ledger"]["n_turns"] == 1
    assert len(result["turns"]) == 1
    assert result["turns"][0]["user_prompt"] == "hi"
    assert result["findings"]["stats"] == 1


def test_show_ambiguous_prefix_raises(tmp_path, store):
    w = write_home(
        tmp_path,
        sessions=[_proj_spec("abc111", "/tmp/proj-a"), _proj_spec("abc222", "/tmp/proj-b")],
    )
    tools.sync(str(w.home), store=store)
    with pytest.raises(KeyError):
        tools.show("abc", store=store)


def test_show_exact_id_matches_even_if_also_a_prefix(tmp_path, store):
    w = write_home(
        tmp_path,
        sessions=[_proj_spec("abc", "/tmp/proj-a"), _proj_spec("abc111", "/tmp/proj-b")],
    )
    tools.sync(str(w.home), store=store)
    result = tools.show("abc", store=store)
    assert result["session"]["session_id"] == "abc"


# --- lenses() ------------------------------------------------------------------------


def test_lenses_lists_stats():
    result = tools.lenses()
    names = {l["name"] for l in result["lenses"]}
    assert "stats" in names
    stats_entry = next(l for l in result["lenses"] if l["name"] == "stats")
    assert stats_entry["kind"] == "H"
    assert stats_entry["doc"]


# --- _run_lens(): incremental lens gets from_index and prior -------------------


def test_run_lens_incremental_receives_from_index_and_prior(clean_lenses):
    calls = []

    @lens("fake_l", version=1, kind="L", incremental=True)
    def fake_l(session, turns, **ctx):
        calls.append({"from_index": ctx.get("from_index"), "prior": ctx.get("prior"), "n_turns": len(turns)})
        return [finding("fake_l", session, kind="probe", evidence={"n": len(turns)})]

    store = MemoryStore()
    session = {"session_id": "sid1", "project": "p", "home": "claude"}
    records_full = mk_records(
        "sid1", "/tmp/p",
        turns=[{"prompt": "t1", "final_text": "a1"}, {"prompt": "t2", "final_text": "a2"}],
    )
    all_turns = list(iter_turns(records_full))
    turns_v1 = all_turns[:1]

    r1 = tools._run_lens(store, "fake_l", session, turns_v1)
    assert r1["action"] == "full"
    r2 = tools._run_lens(store, "fake_l", session, all_turns)
    assert r2["action"] == "incremental"

    assert calls[0] == {"from_index": 0, "prior": [], "n_turns": 1}
    assert calls[1]["from_index"] == 1
    assert calls[1]["n_turns"] == 1
    assert len(calls[1]["prior"]) == 1
    assert calls[1]["prior"][0]["evidence"] == {"n": 1}

    # the stored findings accumulate: the prior finding plus the new one.
    assert len(store.findings["fake_l/sid1"]) == 2


def test_run_lens_skip_when_already_covered(clean_lenses):
    calls = []

    @lens("fake_h", version=1, kind="H", incremental=False)
    def fake_h(session, turns, **ctx):
        calls.append(1)
        return [finding("fake_h", session, kind="probe", evidence={})]

    store = MemoryStore()
    session = {"session_id": "sid1"}
    turns = list(iter_turns(mk_records("sid1", "/tmp/p", turns=[{"prompt": "t1", "final_text": "a1"}])))

    tools._run_lens(store, "fake_h", session, turns)
    r2 = tools._run_lens(store, "fake_h", session, turns)
    assert r2["action"] == "skip"
    assert len(calls) == 1  # not called again


def test_lens_names_unknown_raises():
    with pytest.raises(KeyError):
        tools._lens_names("not-a-real-lens", kind=None)


def test_lens_names_none_is_empty():
    assert tools._lens_names("none", kind="H") == []


# --- judge(): strict_schema is plumbed through to the CLI judge --------------


def test_mk_claude_judge_defaults_to_loose_schema(monkeypatch):
    captured = {}

    def fake_claude_judge(prompt, **kw):
        captured.update(kw)
        from astern.judge import Judgment
        return Judgment(text="{}", data={}, model=kw.get("model", ""))

    monkeypatch.setattr("astern.judge.claude_judge", fake_claude_judge)
    judge_fn = tools._mk_claude_judge(model="haiku", effort=None)
    judge_fn("hi", schema={"type": "object"})
    assert captured["strict_schema"] is False


def test_mk_claude_judge_passes_strict_schema_through(monkeypatch):
    captured = {}

    def fake_claude_judge(prompt, **kw):
        captured.update(kw)
        from astern.judge import Judgment
        return Judgment(text="{}", data={}, model=kw.get("model", ""))

    monkeypatch.setattr("astern.judge.claude_judge", fake_claude_judge)
    judge_fn = tools._mk_claude_judge(model="haiku", effort=None, strict_schema=True)
    judge_fn("hi", schema={"type": "object"})
    assert captured["strict_schema"] is True


def test_judge_dry_run_reports_strict_schema_flag(store):
    from astern.lenses import load_builtin_lenses

    load_builtin_lenses()
    result = tools.judge("synopsis", store=store, dry_run=True)
    assert result["strict_schema"] is False
    result2 = tools.judge("synopsis", store=store, dry_run=True, strict_schema=True)
    assert result2["strict_schema"] is True
