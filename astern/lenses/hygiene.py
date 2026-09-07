"""``hygiene``: does the agent read before it edits? One finding, kind ``hygiene``.

Cheap process metrics, keyed with the session's Claude Code ``version`` and the
models it used so :mod:`astern.report` (or a later ``regression`` lens) can build a
time series across harness/model changes: Read:Edit ratio, how many ``Edit``/
``Write`` calls touched a path with no prior ``Read`` of that same path *earlier in
the same session*, the Write-vs-Edit share, and tool calls per turn.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "fix a.py"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "claude-x",
...    "usage": {}, "content": [{"type": "tool_use", "id": "t1", "name": "Edit",
...    "input": {"file_path": "/p/x/a.py"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user", "content":
...    [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "ok"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> f = hygiene({"session_id": "s", "version": "2.1"}, t)[0]
>>> f["kind"], f["evidence"]["n_unread_edits"], f["evidence"]["version"]
('hygiene', 1, '2.1')
"""

from __future__ import annotations

from astern.lenses import finding, lens


@lens("hygiene", version=1, kind="H")
def hygiene(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Read:Edit ratio, unread-edit count, Write share, tool calls per turn — one session."""
    read_paths: set[str] = set()
    models: list[str] = []
    n_read = n_edit = n_write = n_unread = n_tool_calls = 0
    for t in turns:
        for m in t.get("models") or []:
            if m not in models:
                models.append(m)
        for tc in t.get("tools") or []:
            n_tool_calls += 1
            name, path = tc.get("name", ""), tc.get("digest", "")
            if name == "Read":
                n_read += 1
                if path:
                    read_paths.add(path)
            elif name in ("Edit", "Write"):
                if name == "Edit":
                    n_edit += 1
                else:
                    n_write += 1
                if path and path not in read_paths:
                    n_unread += 1
    n_turns = len(turns)
    n_edit_write = n_edit + n_write
    ev = {
        "n_read": n_read,
        "n_edit": n_edit,
        "n_write": n_write,
        "read_edit_ratio": (n_read / n_edit) if n_edit else None,
        "write_share": (n_write / n_edit_write) if n_edit_write else None,
        "n_unread_edits": n_unread,
        "n_tool_calls": n_tool_calls,
        "tool_calls_per_turn": (n_tool_calls / n_turns) if n_turns else 0.0,
        "version": session.get("version", ""),
        "models": models,
    }
    return [finding("hygiene", session, kind="hygiene", evidence=ev)]
