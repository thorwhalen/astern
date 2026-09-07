"""Tests for the heuristic (``kind='H'``, zero-token) lenses on a synthetic session.

Every assertion is about the *kind* of finding a lens produces, tying back to the
fixture built in ``tests/fixtures_lenses.py`` — never about a real transcript.
"""

import statistics

from fixtures_lenses import (
    assistant,
    mk_records,
    system_record,
    text,
    tool_result,
    tool_use,
    user,
)

from astern.lenses.commands import rewrites
from astern.lenses.cost import cost
from astern.lenses.friction import LONG_TURN_MS, friction
from astern.lenses.hygiene import hygiene
from astern.lenses.timeline import timeline
from astern.lenses.tooling import tooling
from astern.turns import iter_turns, session_meta


def _session_and_turns():
    records = mk_records()
    return session_meta(records), list(iter_turns(records))


def _from_records(records):
    return session_meta(records), list(iter_turns(records))


def _retries(session, turns, *, tool=None):
    kinds = friction(session, turns)
    out = [f for f in kinds if f["kind"] == "retry"]
    if tool is not None:
        out = [f for f in out if f["evidence"]["tool"] == tool]
    return out


def test_friction_kinds():
    session, turns = _session_and_turns()
    kinds = {f["kind"] for f in friction(session, turns)}
    assert {
        "tool_error",
        "retry",
        "pivot",
        "compaction",
        "long_turn",
        "tool_search",
    } <= kinds


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
    assert (
        ev["n_unread_edits"] == 2
    )  # the Edit and the scratchpad Write, neither preceded by a Read
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


def test_retry_identical_bash_command_twice_is_a_retry():
    # Two separate turns (adjacent), same full command, no error either time.
    records = [
        user("u0", "run the tests", ts="t0a"),
        assistant(
            "a0",
            "m0",
            ts="t0b",
            blocks=[tool_use("t1", "Bash", {"command": "pytest -q"})],
        ),
        tool_result("r0", "t1", is_error=False, content="3 passed", ts="t0c"),
        user("u1", "run them again", ts="t1a"),
        assistant(
            "a1",
            "m1",
            ts="t1b",
            blocks=[tool_use("t2", "Bash", {"command": "pytest -q"})],
        ),
        tool_result("r1", "t2", is_error=False, content="3 passed", ts="t1c"),
    ]
    session, turns = _from_records(records)
    retries = _retries(session, turns, tool="Bash")
    assert len(retries) == 1
    ev = retries[0]["evidence"]
    assert ev["digest"] == "pytest -q"
    assert ev["attempts"] == 2
    assert ev["after_error"] is False
    assert ev["first_turn_index"] == 0
    assert ev["turn_indices"] == [0, 1]


def test_retry_edit_same_path_different_content_is_not_a_retry():
    path = "/home/dev/proj/demo/a.py"
    records = [
        user("u0", "edit the file twice", ts="t0a"),
        assistant(
            "a0",
            "m0",
            ts="t0b",
            blocks=[
                tool_use(
                    "t1",
                    "Edit",
                    {"file_path": path, "old_string": "x", "new_string": "y"},
                )
            ],
        ),
        tool_result("r0", "t1", is_error=False, content="ok", ts="t0c"),
        assistant(
            "a0b",
            "m0b",
            ts="t0d",
            blocks=[
                tool_use(
                    "t2",
                    "Edit",
                    {"file_path": path, "old_string": "y", "new_string": "z"},
                )
            ],
        ),
        tool_result("r0b", "t2", is_error=False, content="ok", ts="t0e"),
    ]
    session, turns = _from_records(records)
    assert _retries(session, turns, tool="Edit") == []


def test_retry_edit_after_error_on_same_path_is_a_retry():
    path = "/home/dev/proj/demo/a.py"
    records = [
        user("u0", "edit the file", ts="t0a"),
        assistant(
            "a0",
            "m0",
            ts="t0b",
            blocks=[
                tool_use(
                    "t1",
                    "Edit",
                    {"file_path": path, "old_string": "x", "new_string": "y"},
                )
            ],
        ),
        tool_result("r0", "t1", is_error=True, content="old_string not found", ts="t0c"),
        assistant(
            "a0b",
            "m0b",
            ts="t0d",
            blocks=[
                tool_use(
                    "t2",
                    "Edit",
                    {"file_path": path, "old_string": "w", "new_string": "y"},
                )
            ],
        ),
        tool_result("r0b", "t2", is_error=False, content="ok", ts="t0e"),
    ]
    session, turns = _from_records(records)
    retries = _retries(session, turns, tool="Edit")
    assert len(retries) == 1
    ev = retries[0]["evidence"]
    assert ev["digest"] == path
    assert ev["attempts"] == 2
    assert ev["after_error"] is True


def test_retry_read_same_path_twice_is_not_a_retry_unless_after_an_error():
    path = "/home/dev/proj/demo/a.py"
    ok_records = [
        user("u0", "read it twice", ts="t0a"),
        assistant(
            "a0", "m0", ts="t0b", blocks=[tool_use("t1", "Read", {"file_path": path})]
        ),
        tool_result("r0", "t1", is_error=False, content="...", ts="t0c"),
        assistant(
            "a0b", "m0b", ts="t0d", blocks=[tool_use("t2", "Read", {"file_path": path})]
        ),
        tool_result("r0b", "t2", is_error=False, content="...", ts="t0e"),
    ]
    session, turns = _from_records(ok_records)
    assert _retries(session, turns, tool="Read") == []

    err_records = [
        user("u0", "read it after a failed read", ts="t0a"),
        assistant(
            "a0", "m0", ts="t0b", blocks=[tool_use("t1", "Read", {"file_path": path})]
        ),
        tool_result("r0", "t1", is_error=True, content="not found", ts="t0c"),
        assistant(
            "a0b", "m0b", ts="t0d", blocks=[tool_use("t2", "Read", {"file_path": path})]
        ),
        tool_result("r0b", "t2", is_error=False, content="...", ts="t0e"),
    ]
    session, turns = _from_records(err_records)
    retries = _retries(session, turns, tool="Read")
    assert len(retries) == 1
    assert retries[0]["evidence"]["after_error"] is True


def test_long_turn_uses_session_percentile_not_just_the_floor():
    # Four turns just over the fixed floor, one real outlier. The 90th-percentile
    # bar (computed from these five durations) sits well above the floor, so only
    # the outlier is flagged — the other four, which the old fixed threshold alone
    # would have flagged, are not.
    durations = [130_000, 130_000, 130_000, 130_000, 200_000]
    records = []
    for i, d in enumerate(durations):
        records.append(user(f"u{i}", f"do thing {i}", ts=f"t{i}a"))
        records.append(
            assistant(f"a{i}", f"m{i}", ts=f"t{i}b", blocks=[text(f"done {i}")])
        )
        records.append(system_record("turn_duration", ts=f"t{i}c", durationMs=d))
    session, turns = _from_records(records)
    long_turns = [f for f in friction(session, turns) if f["kind"] == "long_turn"]
    assert len(long_turns) == 1
    ev = long_turns[0]["evidence"]
    assert ev["duration_ms"] == 200_000
    assert ev["threshold_ms"] > LONG_TURN_MS
    assert ev["session_median_ms"] == statistics.median(durations)
