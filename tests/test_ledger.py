"""Tests for astern.ledger: the plan/mark idempotency state machine."""

from __future__ import annotations

from astern import ledger


# --- plan() ------------------------------------------------------------------


def test_plan_never_analyzed_is_full():
    p = ledger.plan({}, lens="stats", version=1, n_turns=5, incremental=False)
    assert p.action == "full"
    assert p.from_index == 0


def test_plan_skip_when_all_turns_covered():
    entry = {}
    ledger.mark(entry, lens="stats", version=1, through_index=5, through_uuid="u5")
    p = ledger.plan(entry, lens="stats", version=1, n_turns=5, incremental=False)
    assert p.action == "skip"


def test_plan_incremental_when_grown_and_lens_is_incremental():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=10, through_uuid="u10")
    p = ledger.plan(entry, lens="problems", version=1, n_turns=14, incremental=True)
    assert p.action == "incremental"
    assert p.from_index == 10


def test_plan_full_when_grown_but_lens_not_incremental():
    entry = {}
    ledger.mark(entry, lens="stats", version=1, through_index=10, through_uuid="u10")
    p = ledger.plan(entry, lens="stats", version=1, n_turns=14, incremental=False)
    assert p.action == "full"
    assert p.from_index == 0


def test_plan_full_on_version_bump():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=10, through_uuid="u10")
    p = ledger.plan(entry, lens="problems", version=2, n_turns=10, incremental=True)
    assert p.action == "full"
    assert p.from_index == 0
    assert "1" in p.reason and "2" in p.reason


def test_plan_full_on_version_bump_even_when_shrunk_or_equal_turns():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=20, through_uuid="u20")
    # even if the "new" turn count is smaller, a version bump always forces full.
    p = ledger.plan(entry, lens="problems", version=2, n_turns=5, incremental=True)
    assert p.action == "full"


# --- mark(): usage accumulation ------------------------------------------------


def test_mark_usage_accumulates_across_incremental_runs_same_version():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=5, usage={"input_tokens": 100})
    ledger.mark(entry, lens="problems", version=1, through_index=10, usage={"input_tokens": 50})
    assert entry["lenses"]["problems"]["usage"]["input_tokens"] == 150
    assert entry["lenses"]["problems"]["runs"] == 2
    assert entry["lenses"]["problems"]["through_index"] == 10


def test_mark_usage_resets_on_version_change():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=5, usage={"input_tokens": 100})
    ledger.mark(entry, lens="problems", version=2, through_index=5, usage={"input_tokens": 7})
    assert entry["lenses"]["problems"]["usage"]["input_tokens"] == 7
    assert entry["lenses"]["problems"]["version"] == 2
    assert entry["lenses"]["problems"]["runs"] == 1


def test_mark_without_usage_leaves_empty_usage_dict():
    entry = {}
    ledger.mark(entry, lens="stats", version=1, through_index=1)
    assert entry["lenses"]["stats"]["usage"] == {}


def test_mark_ignores_non_numeric_usage_values():
    entry = {}
    ledger.mark(entry, lens="problems", version=1, through_index=1, usage={"note": "n/a", "input_tokens": 3})
    assert entry["lenses"]["problems"]["usage"] == {"input_tokens": 3}


def test_mark_two_different_lenses_are_independent():
    entry = {}
    ledger.mark(entry, lens="stats", version=1, through_index=5)
    ledger.mark(entry, lens="friction", version=1, through_index=3)
    assert set(entry["lenses"]) == {"stats", "friction"}
    assert entry["lenses"]["stats"]["through_index"] == 5
    assert entry["lenses"]["friction"]["through_index"] == 3


# --- source_changed() / touch_source() ------------------------------------------


def test_source_changed_true_when_no_prior_source():
    assert ledger.source_changed({}, {"size": 10, "mtime": 1.0}) is True


def test_touch_source_then_unchanged_fingerprint_is_not_changed():
    entry = {}
    fp = {"size": 10, "mtime": 123.456}
    ledger.touch_source(entry, path="/p/s.jsonl", home="claude", fingerprint=fp, n_turns=3, last_uuid="u3")
    assert ledger.source_changed(entry, fp) is False
    assert entry["n_turns"] == 3
    assert entry["last_turn_uuid"] == "u3"
    assert entry["source"]["path"] == "/p/s.jsonl"
    assert entry["source"]["home"] == "claude"


def test_touch_source_then_changed_fingerprint_is_changed():
    entry = {}
    fp1 = {"size": 10, "mtime": 100.0}
    fp2 = {"size": 20, "mtime": 200.0}
    ledger.touch_source(entry, path="/p/s.jsonl", home="claude", fingerprint=fp1, n_turns=3, last_uuid="u3")
    assert ledger.source_changed(entry, fp2) is True


# --- get_entry() ---------------------------------------------------------------


def test_get_entry_missing_returns_empty_dict():
    assert ledger.get_entry({}, "nope") == {}


def test_get_entry_returns_a_copy_not_a_live_reference():
    store = {"sid": {"n_turns": 1}}
    e = ledger.get_entry(store, "sid")
    e["n_turns"] = 999
    assert store["sid"]["n_turns"] == 1
