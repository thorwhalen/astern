"""``timeline``: what a session touched, cheaply — one finding, kind ``session_timeline``.

No judgement, no scoring, just facts already sitting in the turn records and session
meta: when it ran, what project, which git branches it saw, which PRs it produced,
which files it edited, which skills it invoked, how many subagents it spawned, and
its title. This is the "what did I work on" answer that :mod:`astern.report` turns
into a newest-first weekly table.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "gitBranch": "main", "message": {"role": "user", "content": "ship it"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "x",
...    "usage": {}, "content": [{"type": "tool_use", "id": "t1", "name": "Edit",
...    "input": {"file_path": "/p/x/a.py"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user", "content":
...    [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "ok"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> f = timeline({"session_id": "s", "project": "x", "title": "Ship it", "prs": []}, t)[0]
>>> f["kind"], f["evidence"]["files_touched"], f["evidence"]["git_branches"]
('session_timeline', ['/p/x/a.py'], ['main'])
"""

from __future__ import annotations

from astern.lenses import finding, lens


@lens("timeline", version=1, kind="H")
def timeline(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """One session's shape: dates, branches, PRs, files, skills, subagent spawns, title."""
    branches: list[str] = []
    files: set[str] = set()
    skills: list[str] = []
    n_subagents = 0
    for t in turns:
        b = t.get("git_branch")
        if b and b not in branches:
            branches.append(b)
        for tc in t.get("tools") or []:
            name, digest = tc.get("name", ""), tc.get("digest", "")
            if name in ("Edit", "Write") and digest:
                files.add(digest)
            elif name == "Skill" and digest and digest not in skills:
                skills.append(digest)
            elif name == "Agent":
                n_subagents += 1
    ev = {
        "started_at": session.get("started_at", "") or (turns[0]["timestamp"] if turns else ""),
        "ended_at": session.get("ended_at", "") or (turns[-1]["timestamp"] if turns else ""),
        "project": session.get("project", ""),
        "git_branches": branches,
        "prs": session.get("prs") or [],
        "files_touched": sorted(files),
        "skills": skills,
        "n_subagent_spawns": n_subagents,
        "title": session.get("title", ""),
    }
    return [finding("timeline", session, kind="session_timeline", evidence=ev)]
