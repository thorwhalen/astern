"""Tests for the heuristic (``kind='H'``, zero-token) lenses on a synthetic session.

Every assertion is about the *kind* of finding a lens produces, tying back to the
fixture built in ``tests/fixtures.py`` — never about a real transcript.
"""

from astern.lenses.commands import rewrites
from astern.lenses.cost import cost
from astern.lenses.friction import friction
from astern.lenses.hygiene import hygiene
from astern.lenses.timeline import timeline
from astern.lenses.tooling import tooling
from astern.turns import iter_turns, session_meta
from tests.fixtures import mk_records


def _session_and_turns():
    records = mk_records()
    return session_meta(records), list(iter_turns(records))


def test_friction_kinds():
    session, turns = _session_and_turns()
    kinds = {f["kind"] for f in friction(session, turns)}
    assert {"tool_error", "retry", "pivot", "compaction", "long_turn", "tool_search"} <= kinds


def test_rewrites_kinds():
    session, turns = _session_and_turns()
    findings = rewrites(session, turns)
    assert {f["kind"] for f in findings} == {"inline_script"}
    assert len(findings) >= 2
    assert {f["evidence"]["tool"] for f in findings} == {"Bash", "Write"}
    assert all(len(f["evidence"]["fingerprint"]) == 12 for f in findings)


def test_timeline_finding():
    session, turns = _session_and_turns()
    f = timeline(session, turns)[0]
    assert f["kind"] == "session_timeline"
    ev = f["evidence"]
    assert "/home/dev/proj/demo/a.py" in ev["files_touched"]
    assert "/tmp/scratch_count.py" in ev["files_touched"]
    assert ev["skills"] == ["wads-ci-fix"]
    assert ev["git_branches"] == ["main"]
    assert ev["title"] == "Fix flaky tests"


def test_cost_finding():
    session, turns = _session_and_turns()
    f = cost(session, turns)[0]
    assert f["kind"] == "session_cost"
    assert f["evidence"]["cost_state"]["totalCostUSD"] == 0.1234
    assert f["evidence"]["usage_by_model"]["claude-demo"]["input_tokens"] > 0


def test_hygiene_finding():
    session, turns = _session_and_turns()
    f = hygiene(session, turns)[0]
    assert f["kind"] == "hygiene"
    ev = f["evidence"]
    assert ev["n_unread_edits"] == 2  # the Edit and the scratchpad Write, neither preceded by a Read
    assert ev["version"] == "2.1.0"
    assert ev["n_edit"] == 1
    assert ev["n_write"] == 1


def test_tooling_finding():
    session, turns = _session_and_turns()
    f = tooling(session, turns)[0]
    assert f["kind"] == "tooling"
    ev = f["evidence"]
    assert ev["skills"] == {"wads-ci-fix": 1}
    assert ev["tool_search_queries"] == ["select:Skill"]
    assert ev["tools"]["Bash"] >= 2
