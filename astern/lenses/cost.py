"""``cost``: where the tokens and dollars went — one finding, kind ``session_cost``.

Two free sources, no judge involved: the ``cost-state`` record Claude Code itself
writes (``totalCostUSD``, per-model ``modelUsage``, durations, lines added/removed),
and the turn records' already-deduplicated ``usage`` totals, re-summed here per
model. Per-turn cost share is deliberately not computed — the plan calls it out as
unneeded for v1, and ``cost-state`` already gives the authoritative total.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "hi"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "claude-x",
...    "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "text", "text": "hey"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> f = cost({"session_id": "s", "cost_state": {"totalCostUSD": 0.01}}, t)[0]
>>> f["kind"], f["evidence"]["cost_state"]["totalCostUSD"]
('session_cost', 0.01)
>>> f["evidence"]["usage_by_model"]["claude-x"]["input_tokens"]
10
"""

from __future__ import annotations

from collections import Counter, defaultdict

from astern.lenses import finding, lens


@lens("cost", version=1, kind="H")
def cost(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """``cost-state`` plus de-duplicated per-model API usage, for one session."""
    usage_by_model: dict[str, Counter] = defaultdict(Counter)
    for t in turns:
        models = t.get("models") or ["unknown"]
        model = models[0]
        for k, v in (t.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage_by_model[model][k] += v
    ev = {
        "cost_state": session.get("cost_state"),
        "usage_by_model": {m: dict(c) for m, c in usage_by_model.items()},
    }
    return [finding("cost", session, kind="session_cost", evidence=ev)]
