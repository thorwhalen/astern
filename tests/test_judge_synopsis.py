"""The synopsis lens end to end on a synthetic session: idempotency, increments, errors.

No real transcript is read here. The session is three inline records in the shape
:mod:`astern.turns` produces, which is the only shape the lens depends on, and the
judge is :func:`astern.judge.replay_judge` — so the suite spends no tokens and
cannot leak a transcript into the repo.
"""

from __future__ import annotations

import json

import pytest

from astern.estimate import features, fit, predict
from astern.judge import Judgment, replay_judge
from astern.lenses import load_builtin_lenses
from astern.lenses.synopsis import synopsis
from astern.store import MemoryStore
from astern.tools import _run_lens
from astern.turns import iter_turns
from astern.views import session_view, view_stats

SYNOPSIS = {
    "goal": "make the build green",
    "outcome": "done",
    "problems": [
        {
            "problem": "stale lockfile",
            "solution": "regenerate it",
            "category": "packaging",
        }
    ],
    "friction": [
        {"what": "retried uv build", "cause_guess": "env drift", "turn_indices": [0]}
    ],
    "corrections": [
        {"what_user_said": "no, use uv", "rule_candidate": "prefer uv over pip"}
    ],
    "user_terms": [
        {
            "phrase": "the pth thing",
            "established_term": "path configuration file",
            "definition": "a .pth file consumed by site",
        }
    ],
    "agent_terms": [
        {
            "term": "editable install",
            "definition": "an install that points at a source tree",
        }
    ],
    "skill_candidates": [
        {"name": "fix-lockfile", "why": "recurs", "recurrence_hint": "twice"}
    ],
    "reusable_code": [
        {"what": "lockfile regeneration", "suggested_function": "relock(path)"}
    ],
    "notable_decisions": ["pin the resolver"],
}


def _records(n_turns: int = 1) -> list[dict]:
    """A synthetic transcript: ``n_turns`` prompt/answer pairs, one failing Bash each."""
    recs: list[dict] = []
    for i in range(n_turns):
        recs.append(
            {
                "type": "user",
                "uuid": f"u{i}",
                "sessionId": "sid",
                "cwd": "/p/proj",
                "timestamp": f"2026-09-0{i + 1}T10:00:00Z",
                "message": {"role": "user", "content": f"turn {i}: make the build green"},
            }
        )
        recs.append(
            {
                "type": "assistant",
                "uuid": f"a{i}",
                "sessionId": "sid",
                "message": {
                    "id": f"m{i}",
                    "model": "claude-x",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"t{i}",
                            "name": "Bash",
                            "input": {"command": "uv build"},
                        }
                    ],
                },
            }
        )
        recs.append(
            {
                "type": "user",
                "uuid": f"r{i}",
                "sessionId": "sid",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"t{i}",
                            "is_error": True,
                            "content": "boom",
                        }
                    ],
                },
            }
        )
        recs.append(
            {
                "type": "assistant",
                "uuid": f"b{i}",
                "sessionId": "sid",
                "message": {
                    "id": f"n{i}",
                    "model": "claude-x",
                    "usage": {"input_tokens": 20, "output_tokens": 7},
                    "content": [{"type": "text", "text": f"Fixed it on turn {i}."}],
                },
            }
        )
    return recs


def _session(n_turns: int = 1) -> tuple[dict, list[dict]]:
    session = {
        "session_id": "sid",
        "title": "Green build",
        "project": "proj",
        "source": {"size": 4096},
    }
    return session, list(iter_turns(_records(n_turns)))


def _judge(answer=SYNOPSIS):
    return replay_judge({}, default=json.dumps(answer))


def test_synopsis_produces_a_finding_with_usage_and_a_stored_judgment():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    res = _run_lens(store, "synopsis", session, turns, judge=_judge())
    assert res["action"] == "full" and res["n_findings"] == 1
    (f,) = store.findings["synopsis/sid"]
    assert f["kind"] == "synopsis"
    assert f["evidence"]["goal"] == "make the build green"
    assert f["label"] == "done"
    assert f["usage"]["input_tokens"] > 0 and f["usage"]["output_tokens"] > 0
    assert f["view_chars"] > 0 and f["evidence"]["turn_index"] == 0
    # the raw call is stored next to the free features -- estimate.fit's only data
    rec = store.judgments["synopsis/sid/0"]
    assert rec["features"]["view_chars"] == f["view_chars"]
    assert rec["features"]["n_turns"] == 1 and rec["features"]["bytes"] == 4096
    assert rec["total_tokens"] == rec["input_tokens"] + rec["output_tokens"]


def test_a_second_run_over_the_same_session_is_a_skip():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    _run_lens(store, "synopsis", session, turns, judge=_judge())

    def refuse(*a, **k):  # a skip must not reach the judge at all
        raise AssertionError("the judge was called for an already-covered session")

    res = _run_lens(store, "synopsis", session, turns, judge=refuse)
    assert res["action"] == "skip"
    assert len(store.judgments) == 1


def test_new_turns_run_incrementally_and_the_judge_sees_the_prior_synopsis():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session(1)
    _run_lens(store, "synopsis", session, turns, judge=_judge())
    session, grown = _session(3)
    seen: list[str] = []

    def spy(prompt, *, schema=None, **kw):
        seen.append(prompt)
        return _judge()(prompt, schema=schema, **kw)

    res = _run_lens(store, "synopsis", session, grown, judge=spy)
    assert res["action"] == "incremental"
    prompt = seen[0]
    assert "Previously in this session" in prompt
    assert "make the build green" in prompt  # the prior synopsis, rendered
    assert "turn 0:" not in prompt and "turn 1:" in prompt  # only the new turns
    assert len(store.findings["synopsis/sid"]) == 2  # extended, not replaced
    assert sorted(store.judgments) == ["synopsis/sid/0", "synopsis/sid/1"]


def test_a_judge_error_does_not_advance_the_ledger():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session(2)

    def broken(prompt, *, schema=None, **kw):
        return Judgment(
            text="Not logged in",
            error="error_during_execution: Not logged in",
            model="haiku",
            prompt_chars=len(prompt),
        )

    res = _run_lens(store, "synopsis", session, turns, judge=broken)
    assert res["failed"] is True and res["n_findings"] == 0
    assert res["usage"] == {}  # an errored call reports no usage
    assert "synopsis/sid" not in store.findings
    assert (store.ledger.get("sid") or {}).get("lenses", {}).get("synopsis") is None
    # the failure is still recorded as a judgment, so a rerun is a full run
    assert "synopsis/sid/0" in store.judgments
    res2 = _run_lens(store, "synopsis", session, turns, judge=_judge())
    assert res2["action"] == "full" and res2["n_findings"] == 1


def test_a_judge_that_answers_no_json_is_an_error_too():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    res = _run_lens(
        store, "synopsis", session, turns, judge=replay_judge({}, default="sorry, no")
    )
    assert res["failed"] is True
    assert "synopsis/sid" not in store.findings


# --- loose-mode tolerance: schema-off-ness is a finding, never a failed call ---


def test_a_fenced_preambled_answer_still_produces_a_synopsis():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    fenced = (
        "Sure, here is the JSON:\n```json\n"
        + json.dumps(SYNOPSIS)
        + "\n```\nHope that helps!"
    )
    res = _run_lens(
        store, "synopsis", session, turns, judge=replay_judge({}, default=fenced)
    )
    assert res["action"] == "full" and res["n_findings"] == 1
    (f,) = store.findings["synopsis/sid"]
    assert f["kind"] == "synopsis" and f["evidence"]["goal"] == SYNOPSIS["goal"]


def test_an_unparseable_answer_yields_a_judge_error_finding_and_leaves_the_ledger_untouched():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    judge = replay_judge({}, default="sorry, I cannot help with that")
    # the lens function itself, so the finding it produces can be inspected directly
    out = synopsis(session, turns, judge=judge, from_index=0, prior=[], store=None)
    assert len(out) == 1 and out[0]["kind"] == "judge_error" and out[0]["error"]

    res = _run_lens(store, "synopsis", session, turns, judge=judge)
    assert res["failed"] is True
    assert "synopsis/sid" not in store.findings
    assert (store.ledger.get("sid") or {}).get("lenses", {}).get("synopsis") is None


def test_missing_schema_fields_are_defaulted_and_outcome_is_normalized():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    sparse = json.dumps({"goal": "just the goal", "outcome": "kind of, I guess"})
    res = _run_lens(
        store, "synopsis", session, turns, judge=replay_judge({}, default=sparse)
    )
    assert res["action"] == "full" and res["n_findings"] == 1
    (f,) = store.findings["synopsis/sid"]
    ev = f["evidence"]
    assert ev["goal"] == "just the goal"
    assert ev["outcome"] == "unclear"  # not one of the recognised outcomes
    for field in (
        "problems",
        "friction",
        "corrections",
        "user_terms",
        "agent_terms",
        "skill_candidates",
        "reusable_code",
        "notable_decisions",
    ):
        assert ev[field] == []


def test_malformed_list_items_are_dropped_rather_than_failing_the_call():
    load_builtin_lenses()
    store, (session, turns) = MemoryStore(), _session()
    messy = dict(SYNOPSIS)
    messy["problems"] = [messy["problems"][0], "not a dict", 42, {"problem": "p2"}]
    res = _run_lens(
        store,
        "synopsis",
        session,
        turns,
        judge=replay_judge({}, default=json.dumps(messy)),
    )
    assert res["action"] == "full" and res["n_findings"] == 1
    (f,) = store.findings["synopsis/sid"]
    problems = f["evidence"]["problems"]
    # "not a dict" and 42 are dropped; the two dict items survive, the sparse one defaulted
    assert len(problems) == 2
    assert problems[1] == {"problem": "p2", "solution": "", "category": ""}


def test_the_view_shrinks_and_says_so():
    session, turns = _session(30)
    view = session_view(session, turns, max_chars=1500)
    stats = view_stats(view)
    assert len(view) <= 1500
    assert stats["n_turns_dropped"] > 0
    assert stats["n_turns_shown"] + stats["n_turns_dropped"] == 30
    assert "omitted" in view


def test_features_are_computable_without_a_model():
    session, turns = _session(2)
    f = features(session, turns, "x" * 100)
    assert f["n_turns"] == 2 and f["n_tool_calls"] == 2 and f["n_errors"] == 2
    assert f["prose_chars"] == f["prompt_chars"] + f["assistant_chars"]
    assert f["view_chars"] == 100 and f["view_tokens_est"] == 25


def test_fit_recovers_a_known_slope():
    slope, intercept = 0.30, 1200.0  # tokens per view char, plus the fixed call cost
    pts = [
        {
            "features": {"view_chars": c, "n_turns": 4},
            "cost_usd": 0.001 * (c / 1000),
            "usage": {"input_tokens": int(intercept + slope * c), "output_tokens": 900},
        }
        for c in (2000, 5000, 11000, 19000)
    ]
    m = fit(pts)
    assert m["n"] == 4 and m["method"] == "ols"
    assert m["input"]["per_char"] == pytest.approx(slope, abs=0.01)
    assert m["input"]["intercept"] == pytest.approx(intercept, abs=20)
    assert m["input"]["r2"] > 0.999
    assert m["input"]["per_1k_chars"] == pytest.approx(300, abs=10)
    assert m["output"]["mean"] == 900
    p = predict(m, {"view_chars": 8000})
    assert p["input_tokens"] == pytest.approx(intercept + slope * 8000, abs=30)
    assert p["total_tokens"] == p["input_tokens"] + p["output_tokens"]
    assert p["low"] <= p["total_tokens"] <= p["high"]


def test_fit_falls_back_gracefully_below_three_points():
    assert fit([])["method"] == "prior"
    one = fit(
        [
            {
                "features": {"view_chars": 4000, "n_turns": 2},
                "usage": {"input_tokens": 2300, "output_tokens": 800},
            }
        ]
    )
    assert one["method"] == "ratio" and one["input"]["per_char"] > 0
    assert predict(one, {"view_chars": 4000})["input_tokens"] == pytest.approx(
        2300, abs=5
    )
