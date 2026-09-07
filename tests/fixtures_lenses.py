"""Synthetic, secret-free JSONL-style records for the heuristic lens tests.

Never copy a real transcript into this repo. :func:`mk_records` invents one small
session that exercises exactly the surface the ``H`` lenses key on: a user prompt,
a Bash error followed by a same-command retry (plus a pivot phrase), an ``Edit``
with no prior ``Read``, a ``Write`` under ``/tmp/``, a Bash heredoc, a
``ToolSearch`` call, a ``Skill`` call, a ``compact_boundary``, a ``turn_duration``,
an ``ai-title``, a ``pr-link``, and a ``cost-state``.

>>> len(mk_records()) > 10
True
"""

from __future__ import annotations

SID = "sess-test-1"
CWD = "/home/dev/proj/demo"
VERSION = "2.1.0"


#: Small raw-record builders, shaped like the JSONL the CLI logs. Public (no leading
#: underscore) because ``tests/test_lenses_h.py`` also uses them to build tiny,
#: single-purpose fixtures for cases that don't belong in :func:`mk_records`.


def user(uuid: str, text: str, *, ts: str, sid: str = SID) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": sid,
        "cwd": CWD,
        "timestamp": ts,
        "gitBranch": "main",
        "version": VERSION,
        "message": {"role": "user", "content": text},
    }


def tool_result(
    uuid: str, tool_use_id: str, *, is_error: bool, content: str, ts: str, sid: str = SID
) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": is_error,
                    "content": content,
                }
            ],
        },
    }


def assistant(
    uuid: str, msg_id: str, *, blocks: list[dict], ts: str, sid: str = SID
) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "sessionId": sid,
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": "claude-demo",
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "content": blocks,
        },
    }


def tool_use(tool_id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def system_record(subtype: str, *, ts: str, sid: str = SID, **extra) -> dict:
    return {
        "type": "system",
        "subtype": subtype,
        "sessionId": sid,
        "timestamp": ts,
        **extra,
    }


# Back-compat aliases used by :func:`mk_records` below.
_user, _tool_result, _assistant, _tool_use, _text, _system = (
    user,
    tool_result,
    assistant,
    tool_use,
    text,
    system_record,
)


def mk_records() -> list[dict]:
    """One small synthetic session covering every ``H`` lens signal."""
    return [
        {"type": "ai-title", "sessionId": SID, "aiTitle": "Fix flaky tests"},
        {
            "type": "pr-link",
            "sessionId": SID,
            "prNumber": 42,
            "prUrl": "https://example/pr/42",
            "prRepository": "demo/demo",
        },
        {
            "type": "cost-state",
            "sessionId": SID,
            "totalCostUSD": 0.1234,
            "modelUsage": {"claude-demo": {"inputTokens": 100, "outputTokens": 40}},
            "linesAdded": 12,
            "linesRemoved": 3,
        },
        # Turn 0: a Bash error, a same-command retry (with a pivot phrase), friction system records.
        _user("u0", "please fix the failing tests", ts="2026-09-01T00:00:00Z"),
        _assistant(
            "a0",
            "m0",
            ts="2026-09-01T00:00:01Z",
            blocks=[_tool_use("t1", "Bash", {"command": "pytest -q"})],
        ),
        _tool_result(
            "u0r1",
            "t1",
            is_error=True,
            content="FAILED tests/test_x.py",
            ts="2026-09-01T00:00:02Z",
        ),
        _assistant(
            "a0b",
            "m0b",
            ts="2026-09-01T00:00:03Z",
            blocks=[
                _tool_use("t2", "Bash", {"command": "pytest -q"}),
                _text("Hmm, that's odd. Let me try that again."),
            ],
        ),
        _tool_result(
            "u0r2", "t2", is_error=False, content="3 passed", ts="2026-09-01T00:00:04Z"
        ),
        _assistant(
            "a0c",
            "m0c",
            ts="2026-09-01T00:00:05Z",
            blocks=[_text("Tests are passing now.")],
        ),
        _system(
            "compact_boundary",
            ts="2026-09-01T00:00:06Z",
            compactMetadata={"preTokens": 90000, "postTokens": 8000},
        ),
        _system("turn_duration", ts="2026-09-01T00:00:07Z", durationMs=150000),
        # Turn 1: an Edit with no prior Read, a scratchpad Write, a Bash heredoc,
        # a ToolSearch call, a Skill call.
        _user(
            "u1",
            "now count lines in a.py and record a helper script",
            ts="2026-09-01T00:01:00Z",
        ),
        _assistant(
            "a1",
            "m1",
            ts="2026-09-01T00:01:01Z",
            blocks=[
                _tool_use(
                    "t3",
                    "Edit",
                    {
                        "file_path": "/home/dev/proj/demo/a.py",
                        "old_string": "x",
                        "new_string": "y",
                    },
                )
            ],
        ),
        _tool_result(
            "u1r1", "t3", is_error=False, content="ok", ts="2026-09-01T00:01:02Z"
        ),
        _assistant(
            "a1b",
            "m1b",
            ts="2026-09-01T00:01:03Z",
            blocks=[
                _tool_use(
                    "t4",
                    "Write",
                    {
                        "file_path": "/tmp/scratch_count.py",
                        "content": "import sys\nprint(sum(1 for _ in open(sys.argv[1])))",
                    },
                )
            ],
        ),
        _tool_result(
            "u1r2", "t4", is_error=False, content="ok", ts="2026-09-01T00:01:04Z"
        ),
        _assistant(
            "a1c",
            "m1c",
            ts="2026-09-01T00:01:05Z",
            blocks=[
                _tool_use(
                    "t5", "Bash", {"command": "python3 - <<'EOF'\nprint('counting')\nEOF"}
                )
            ],
        ),
        _tool_result(
            "u1r3", "t5", is_error=False, content="counting", ts="2026-09-01T00:01:06Z"
        ),
        _assistant(
            "a1d",
            "m1d",
            ts="2026-09-01T00:01:07Z",
            blocks=[_tool_use("t6", "ToolSearch", {"query": "select:Skill"})],
        ),
        _tool_result(
            "u1r4", "t6", is_error=False, content="ok", ts="2026-09-01T00:01:08Z"
        ),
        _assistant(
            "a1e",
            "m1e",
            ts="2026-09-01T00:01:09Z",
            blocks=[_tool_use("t7", "Skill", {"skill": "wads-ci-fix"})],
        ),
        _tool_result(
            "u1r5", "t7", is_error=False, content="ok", ts="2026-09-01T00:01:10Z"
        ),
        _assistant("a1f", "m1f", ts="2026-09-01T00:01:11Z", blocks=[_text("Done.")]),
    ]
