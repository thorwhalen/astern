"""``tooling``: which skills/subagents/MCP tools got used — one finding, kind ``tooling``.

Pure tallying over one session's tool calls: every tool name, the subset with an
``mcp__`` prefix, ``Skill`` invocations by skill name, ``Agent`` subagent spawns
(keyed by whatever :func:`astern.turns.tool_digest` extracted — usually the prompt,
since that is the field :mod:`astern.turns` picks first), and every ``ToolSearch``
query. Cross-session, :mod:`astern.report` turns this into the never-triggering /
missing-skill lists the plan's lens N describes.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "run the deploy skill"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "x",
...    "usage": {}, "content": [{"type": "tool_use", "id": "t1", "name": "Skill",
...    "input": {"skill": "twp-deploy"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user", "content":
...    [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "ok"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> f = tooling({"session_id": "s"}, t)[0]
>>> f["kind"], f["evidence"]["skills"]
('tooling', {'twp-deploy': 1})
"""

from __future__ import annotations

from collections import Counter

from astern.lenses import finding, lens


@lens("tooling", version=1, kind="H")
def tooling(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Tool/MCP/skill/subagent/ToolSearch tallies for one session."""
    tool_counts: Counter = Counter()
    mcp_counts: Counter = Counter()
    skill_counts: Counter = Counter()
    subagent_counts: Counter = Counter()
    tool_search_queries: list[str] = []
    for t in turns:
        for tc in t.get("tools") or []:
            name, digest = tc.get("name", ""), tc.get("digest", "")
            tool_counts[name] += 1
            if name.startswith("mcp__"):
                mcp_counts[name] += 1
            elif name == "Skill" and digest:
                skill_counts[digest] += 1
            elif name == "Agent" and digest:
                subagent_counts[digest] += 1
            elif name == "ToolSearch" and digest:
                tool_search_queries.append(digest)
    ev = {
        "tools": dict(tool_counts.most_common()),
        "mcp_tools": dict(mcp_counts.most_common()),
        "skills": dict(skill_counts.most_common()),
        "subagents": dict(subagent_counts.most_common()),
        "tool_search_queries": tool_search_queries,
    }
    return [finding("tooling", session, kind="tooling", evidence=ev)]
