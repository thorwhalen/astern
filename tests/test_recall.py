"""Tests for astern.recall: the record shapes, and recall() against a fake ir.

The record half is pure and needs nothing but a store; the search half is
exercised against a stand-in ``ir`` module (:class:`_FakeIr`) so the suite never
embeds anything. One integration test at the bottom does use the real ``ir``,
with its numpy-only ``light`` embedder, and skips when ``ir`` is not installed.
"""

from __future__ import annotations

import sys
import types

import pytest
from fixtures import mk_records, write_home

from astern import recall as R
from astern import tools
from astern.store import MemoryStore

# --- store fixtures ----------------------------------------------------------


def _store_with(sessions):
    """A MemoryStore holding ``{sid: (session_meta, turns, synopsis_evidence)}``."""
    store = MemoryStore()
    for sid, (session, turns, evidence) in sessions.items():
        store.sessions[sid] = {"session_id": sid, **session}
        store.turns[sid] = turns
        if evidence is not None:
            store.findings[f"synopsis/{sid}"] = [
                {"kind": "synopsis", "evidence": evidence}
            ]
    return store


def _turn(index, uuid, prompt, summary, **kw):
    return {
        "index": index,
        "uuid": uuid,
        "timestamp": kw.pop("timestamp", "2026-09-01T10:00:00Z"),
        "user_prompt": prompt,
        "assistant_summary": summary,
        "assistant_full": kw.pop("assistant_full", summary),
        "tools": kw.pop("tools", []),
        "n_tool_calls": kw.pop("n_tool_calls", 0),
        "n_errors": kw.pop("n_errors", 0),
        "git_branch": kw.pop("git_branch", "main"),
        "models": kw.pop("models", ["claude-haiku"]),
        **kw,
    }


@pytest.fixture
def store():
    return _store_with(
        {
            "s1": (
                {
                    "title": "Ledger and resumed sessions",
                    "project": "astern",
                    "cwd": "/p/astern",
                    "started_at": "2026-09-01T09:00:00Z",
                    "ended_at": "2026-09-01T12:00:00Z",
                    "prs": [{"repo": "o/r", "number": 3, "url": "u"}],
                },
                [
                    _turn(
                        0,
                        "u0",
                        "why does the miner re-judge a resumed session?",
                        "because the ledger only stored the lens version",
                        tools=[
                            {"name": "Edit", "digest": "/p/astern/astern/ledger.py"},
                            {"name": "Bash", "digest": "pytest -q", "is_error": True},
                        ],
                        n_tool_calls=2,
                        n_errors=1,
                    ),
                    _turn(1, "u1", "", "", tools=[]),  # no prose: skipped
                ],
                {
                    "goal": "stop re-judging resumed sessions",
                    "outcome": "done",
                    "problems": [
                        {"problem": "ledger lost the turn index", "solution": "store it"}
                    ],
                    "notable_decisions": ["a failed judge call is not a covered session"],
                },
            ),
            "s2": (
                {
                    "title": "Dark mode",
                    "project": "other",
                    "cwd": "/p/other",
                    "ended_at": "2025-01-01T10:00:00Z",
                },
                [_turn(0, "u0", "add a dark mode toggle", "added it")],
                None,
            ),
        }
    )


# --- records() ---------------------------------------------------------------


def test_synopsis_records_shape(store):
    (rec,) = list(R.records("session_synopses", store=store))
    assert rec["id"] == "s1"
    assert rec["session_id"] == "s1" and rec["project"] == "astern"
    assert rec["title"] == "Ledger and resumed sessions"
    assert rec["outcome"] == "done" and rec["prs"] == ["o/r#3"]
    assert rec["timestamp"] == rec["ended_at"] == "2026-09-01T12:00:00Z"
    assert rec["n_turns"] == 2
    assert rec["pointer"] == "astern show s1"
    assert "Goal: stop re-judging resumed sessions" in rec["text"]
    assert "ledger lost the turn index — store it" in rec["text"]
    assert "a failed judge call is not a covered session" in rec["text"]


def test_synopsis_records_merge_an_incrementally_judged_session():
    store = _store_with({"s": ({}, [], None)})
    store.findings["synopsis/s"] = [
        {"kind": "synopsis", "evidence": {"goal": "first half"}},
        {"kind": "judge_error", "error": "boom"},
        {"kind": "synopsis", "evidence": {"goal": "second half"}},
    ]
    (rec,) = list(R.records("session_synopses", store=store))
    assert "first half" in rec["text"] and "second half" in rec["text"]


def test_a_session_with_no_synopsis_yields_no_synopsis_record(store):
    assert [r["id"] for r in R.records("session_synopses", store=store)] == ["s1"]


def test_turn_records_shape_and_prose_filter(store):
    recs = list(R.records("session_turns", store=store, project="astern"))
    assert [r["id"] for r in recs] == ["s1:u0"]  # the prose-less turn is skipped
    (rec,) = recs
    assert rec["user_prompt"].startswith("why does the miner")
    assert rec["assistant_summary"].startswith("because the ledger")
    assert rec["turn_index"] == 0 and rec["n_errors"] == 1
    assert rec["tools"] == ["Bash", "Edit"]
    assert rec["files"] == ["/p/astern/astern/ledger.py"]  # from the Edit digest
    assert rec["session_title"] == "Ledger and resumed sessions"
    assert rec["pointer"] == "astern show s1 --turns 1"
    assert "assistant_full" not in rec  # off by default: the summary is the signal


def test_turn_records_can_include_the_full_assistant_text(store):
    recs = list(R.records("session_turns", store=store, include_full=True))
    assert all("assistant_full" in r for r in recs)


def test_records_filters_by_project_and_recency(store):
    ids = {r["id"] for r in R.records("session_turns", store=store, project="other")}
    assert ids == {"s2:u0"}
    recent = {
        r["session_id"] for r in R.records("session_turns", store=store, since_days=3650)
    }
    assert recent == {"s1", "s2"}
    assert not list(R.records("session_turns", store=store, since_days=0.0001))


def test_records_rejects_an_unknown_grain(store):
    with pytest.raises(ValueError):
        list(R.records("episodes", store=store))


# --- the session_turns / subagent_turns split ---------------------------------


@pytest.fixture
def store_with_subagent(store):
    """``store`` plus a subagent and a workflow transcript delegated by ``s1``."""
    store.sessions["sub1"] = {
        "session_id": "sub1",
        "kind": "subagent",
        "parent_id": "s1",
        "title": "",  # a subagent almost never has its own ai-title
        "project": "astern",
        "cwd": "/p/astern",
        "ended_at": "2026-09-01T11:00:00Z",
    }
    store.turns["sub1"] = [
        _turn(0, "su0", "fix the ledger bug in astern/ledger.py", "fixed it")
    ]
    store.sessions["wf1"] = {
        "session_id": "wf1",
        "kind": "workflow",
        "parent_id": "s1",
        "project": "astern",
        "cwd": "/p/astern",
        "ended_at": "2026-09-01T11:30:00Z",
    }
    store.turns["wf1"] = [_turn(0, "wu0", "run the release workflow", "released")]
    return store


def test_session_turns_excludes_subagent_and_workflow_sessions(store_with_subagent):
    ids = {r["id"] for r in R.records("session_turns", store=store_with_subagent)}
    assert ids == {"s1:u0", "s2:u0"}  # sub1 and wf1 never leak into session_turns
    assert "sub1:su0" not in ids and "wf1:wu0" not in ids


def test_subagent_turns_includes_only_subagent_and_workflow_sessions(
    store_with_subagent,
):
    recs = list(R.records("subagent_turns", store=store_with_subagent))
    assert {r["id"] for r in recs} == {"sub1:su0", "wf1:wu0"}


def test_subagent_turns_carries_kind_parent_id_and_the_parents_title_project(
    store_with_subagent,
):
    recs = {r["id"]: r for r in R.records("subagent_turns", store=store_with_subagent)}
    sub = recs["sub1:su0"]
    assert sub["kind"] == "subagent"
    assert sub["parent_id"] == "s1"
    assert sub["parent_title"] == "Ledger and resumed sessions"
    assert sub["parent_project"] == "astern"
    assert recs["wf1:wu0"]["kind"] == "workflow"


def test_subagent_turns_tolerates_a_missing_parent():
    store = MemoryStore()
    store.sessions["sub1"] = {
        "session_id": "sub1",
        "kind": "subagent",
        "parent_id": "gone",
    }
    store.turns["sub1"] = [_turn(0, "u0", "do a thing", "did it")]
    (rec,) = list(R.records("subagent_turns", store=store))
    assert rec["parent_title"] == "" and rec["parent_project"] == ""


def test_a_session_missing_the_kind_field_defaults_to_session():
    """Older stores (and every other fixture here) never set ``kind`` at all."""
    store = MemoryStore()
    store.sessions["s"] = {"session_id": "s"}  # no 'kind' key
    store.turns["s"] = [_turn(0, "u0", "hi", "hello")]
    assert [r["id"] for r in R.records("session_turns", store=store)] == ["s:u0"]
    assert list(R.records("subagent_turns", store=store)) == []


def test_fetchers_read_the_default_store(tmp_path, monkeypatch):
    """The registry holds a *name*, so the fetcher must find the store by env."""
    w = write_home(
        tmp_path,
        sessions=[
            {
                "sid": "s9",
                "cwd": "/tmp/proj-x",
                "turns": [{"prompt": "hello there", "final_text": "hi back"}],
            }
        ],
    )
    monkeypatch.setenv("ASTERN_DATA_DIR", str(tmp_path / "data"))
    tools.sync(str(w.home), store=None)
    assert [r["id"].split(":")[0] for r in R.turn_records()] == ["s9"]
    assert R.synopsis_records() == []  # no synopsis lens has run


# --- recall(), against a stand-in ir -----------------------------------------


class _Disclosure:
    def __init__(self, artifact_id, score, summary, metadata, source):
        self.artifact_id, self.score, self.summary = artifact_id, score, summary
        self.metadata, self.source, self.name, self.pointer = (
            metadata,
            source,
            artifact_id,
            None,
        )


class _Result:
    def __init__(self, results):
        self.results, self.abstained, self.reason = results, False, ""
        self.n_retrieved = len(results)


class _Corpus:
    def __init__(self, n):
        self._n = n

    def __len__(self):
        return self._n


def _fake_ir(*, built=("session_synopses", "session_turns"), calls=None):
    """A stand-in ``ir`` module: enough surface for index()/recall(), no embedding."""
    module = types.ModuleType("ir")
    registry_entries: dict = {}

    def register(name, kind, *, embedder="default", **params):
        registry_entries[name] = {"kind": kind, "embedder": embedder, "params": params}
        (calls if calls is not None else []).append(("register", name, embedder))
        return registry_entries[name]

    def open_corpus(name):
        if name not in built:
            raise KeyError(name)
        return _Corpus(2)

    def build_corpus(name, **kw):
        (calls if calls is not None else []).append(("build", name))
        corpus = _Corpus(2)
        corpus.embedder_id = "hashing__dim512"
        corpus.store = types.SimpleNamespace(ledger_items=lambda: iter([("a", {})]))
        return corpus

    def discover(corpora, query, **kw):
        (calls if calls is not None else []).append(("discover", tuple(corpora), kw))
        return _Result(
            [
                _Disclosure(
                    "s1",
                    0.42,
                    "Goal: stop re-judging resumed sessions",
                    {
                        "session_id": "s1",
                        "title": "Ledger",
                        "project": "astern",
                        "timestamp": "2026-09-01T12:00:00Z",
                        "pointer": "astern show s1",
                    },
                    "session_synopses",
                )
            ]
        )

    module.register = register
    module.registry = types.SimpleNamespace(get=registry_entries.get)
    module.open_corpus = open_corpus
    module.build_corpus = build_corpus
    module.discover = discover
    module.config = types.SimpleNamespace(
        corpus_dir=lambda name: f"/data/{name}",
        registry_path=lambda: "/config/corpora.json",
    )
    return module


@pytest.fixture
def fake_ir(monkeypatch):
    calls: list = []
    monkeypatch.setitem(sys.modules, "ir", _fake_ir(calls=calls))
    return calls


def test_index_registers_then_builds_each_grain(fake_ir):
    out = R.index(store=MemoryStore().root)
    assert [c[0] for c in fake_ir] == ["register", "build"] * len(R.GRAINS)
    assert set(out["corpora"]) == set(R.GRAINS)
    assert "subagent_turns" in out["corpora"]  # index(grain='all') builds it too
    assert out["corpora"]["session_turns"]["n_records"] == 2


def test_index_of_one_grain_only(fake_ir):
    out = R.index("session_turns")
    assert list(out["corpora"]) == ["session_turns"]


def test_recall_returns_small_cited_hits(fake_ir):
    out = R.recall("re-judging a resumed session")
    (hit,) = out["hits"]
    assert hit["grain"] == "session_synopses"
    assert hit["session_id"] == "s1" and hit["pointer"] == "astern show s1"
    assert hit["text"].startswith("Goal: stop re-judging")
    assert out["abstained"] is False


def test_recall_passes_the_project_filter_and_drops_companion_corpora(fake_ir, store):
    out = R.recall("x", project="astern", store=store)
    (_, corpora, kwargs) = next(c for c in fake_ir if c[0] == "discover")
    assert corpora == ("session_synopses", "session_turns")
    # The store resolves a project to its session ids — a vd filter has no
    # substring operator, and s2's cwd (/p/other) is not this project's.
    assert kwargs["filter"] == {"session_id": {"$in": ["s1"]}}
    assert any("skipped" in note for note in out["notes"])


def test_recall_says_when_no_session_matches_the_project(fake_ir, store):
    out = R.recall("x", project="nosuchproject", store=store)
    assert any("no synced session matches" in note for note in out["notes"])


def test_recall_project_matches_a_worktree_cwd(fake_ir):
    """A session run from a worktree names the project only in its cwd."""
    worktree = _store_with(
        {
            "w1": (
                {"project": "agent-x", "cwd": "/p/astern/.claude/worktrees/agent-x"},
                [],
                None,
            )
        }
    )
    R.recall("x", project="astern", store=worktree)
    (_, _corpora, kwargs) = next(c for c in fake_ir if c[0] == "discover")
    assert kwargs["filter"] == {"session_id": {"$in": ["w1"]}}


def test_recall_unfiltered_includes_companion_corpora_when_built(monkeypatch):
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "ir",
        _fake_ir(built=("session_synopses", "session_turns", "skills"), calls=calls),
    )
    R.recall("x")
    (_, corpora, _kw) = next(c for c in calls if c[0] == "discover")
    assert "skills" in corpora


def test_recall_says_what_to_run_when_nothing_is_built(monkeypatch):
    monkeypatch.setitem(sys.modules, "ir", _fake_ir(built=()))
    out = R.recall("x")
    assert out["hits"] == [] and out["abstained"] is True
    assert any("astern index" in note for note in out["notes"])


def test_recall_since_days_becomes_a_timestamp_filter(fake_ir):
    R.recall("x", since_days=30)
    (_, _corpora, kwargs) = next(c for c in fake_ir if c[0] == "discover")
    assert "$gte" in kwargs["filter"]["timestamp"]


# --- default grains exclude subagent_turns ------------------------------------


def test_recall_default_grains_exclude_subagent_turns(monkeypatch):
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "ir",
        _fake_ir(
            built=("session_synopses", "session_turns", "subagent_turns"),
            calls=calls,
        ),
    )
    R.recall("x")
    (_, corpora, _kw) = next(c for c in calls if c[0] == "discover")
    assert "subagent_turns" not in corpora
    assert set(corpora) == {"session_synopses", "session_turns"}


def test_recall_include_subagents_true_adds_the_grain(monkeypatch):
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "ir",
        _fake_ir(
            built=("session_synopses", "session_turns", "subagent_turns"),
            calls=calls,
        ),
    )
    R.recall("x", include_subagents=True)
    (_, corpora, _kw) = next(c for c in calls if c[0] == "discover")
    assert "subagent_turns" in corpora


def test_recall_grains_naming_subagent_turns_includes_it_without_the_flag(monkeypatch):
    calls: list = []
    monkeypatch.setitem(
        sys.modules,
        "ir",
        _fake_ir(built=("subagent_turns",), calls=calls),
    )
    R.recall("x", grains="subagent_turns")
    (_, corpora, _kw) = next(c for c in calls if c[0] == "discover")
    assert corpora == ("subagent_turns",)


def test_missing_ir_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "ir", None)  # import ir -> ImportError
    with pytest.raises(ImportError, match=r"astern\[recall\]"):
        R.recall("x")


# --- the CLI surface ---------------------------------------------------------


def test_tools_exposes_index_recall_and_install_skills():
    names = [f.__name__ for f in tools._dispatch_funcs]
    assert {"index", "recall", "install_skills"} <= set(names)


def test_tools_recall_delegates(fake_ir):
    out = tools.recall("x", k=3)
    assert out["hits"] and out["query"] == "x"


# --- one real-ir integration test -------------------------------------------


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("ir") is None,
    reason="ir is not installed (optional extra `astern[recall]`)",
)
def test_index_and_recall_for_real_with_the_light_embedder(tmp_path, monkeypatch):
    monkeypatch.setenv("IR_CONFIG_DIR", str(tmp_path / "ir-config"))
    monkeypatch.setenv("IR_DATA_DIR", str(tmp_path / "ir-data"))
    monkeypatch.setenv("IR_CACHE_DIR", str(tmp_path / "ir-cache"))
    data_dir = tmp_path / "astern-data"
    monkeypatch.setenv("ASTERN_DATA_DIR", str(data_dir))

    w = write_home(
        tmp_path,
        sessions=[
            {
                "sid": "sess-recall",
                "cwd": "/tmp/proj-recall",
                "turns": [
                    {
                        "prompt": "the numpy abi mismatch breaks the build",
                        "final_text": "pinned numpy below 2 and the wheel builds",
                    },
                    {
                        "prompt": "add a dark mode toggle",
                        "final_text": "wired the toggle to a theme context",
                    },
                    {
                        "prompt": "why is the deploy stuck",
                        "final_text": "the lockfile was stale on the server",
                    },
                ],
            }
        ],
    )
    tools.sync(str(w.home))
    out = tools.index("session_turns", embedder="light")
    assert out["corpora"]["session_turns"]["n_records"] >= 3

    found = tools.recall("numpy abi mismatch", grains="session_turns", mode="lexical")
    assert found["hits"], found
    assert "numpy" in found["hits"][0]["text"].lower()
    assert found["hits"][0]["session_id"] == "sess-recall"


def test_mk_records_is_used_by_the_integration_fixture():
    """Guard the fixture import the integration test relies on."""
    assert mk_records("s", "/c", turns=[{"prompt": "p"}])
