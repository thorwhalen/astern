"""``stats``: the shape of a session in numbers. Zero tokens; the cost model's features.

One finding per session, kind ``session_stats``. Everything a cost model could
regress on lives in ``evidence``: bytes, turns, tool calls, errors, prose chars,
API usage de-duplicated by message id, models, duration, compactions.

>>> from astern.turns import iter_turns, session_meta
>>> s = {'session_id': 's', 'source': {'size': 10}}
>>> f = stats(s, [{'index': 0, 'user_prompt': 'hi', 'assistant_full': 'yo', 'tools': [],
...   'n_tool_calls': 0, 'n_errors': 0, 'tool_result_chars': 0, 'usage': {'input_tokens': 3},
...   'system': [], 'models': ['m'], 'timestamp': ''}])[0]
>>> f['evidence']['n_turns'], f['evidence']['usage']['input_tokens'], f['evidence']['models']
(1, 3, ['m'])
"""

from __future__ import annotations

from collections import Counter

from astern.lenses import finding, lens


def _duration_ms(turns: list[dict]) -> int:
    tot = 0
    for t in turns:
        for s in t.get("system") or []:
            if s.get("subtype") == "turn_duration" and isinstance(
                s.get("durationMs"), (int, float)
            ):
                tot += int(s["durationMs"])
    return tot


@lens("stats", version=1, kind="H")
def stats(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Per-session size, activity, usage and tool-mix numbers."""
    usage: Counter = Counter()
    tools: Counter = Counter()
    models: list[str] = []
    n_compactions = 0
    for t in turns:
        for k, v in (t.get("usage") or {}).items():
            usage[k] += v
        for tc in t.get("tools") or []:
            tools[tc["name"]] += 1
        for m in t.get("models") or []:
            if m not in models:
                models.append(m)
        n_compactions += sum(
            1 for s in t.get("system") or [] if s.get("subtype") == "compact_boundary"
        )
    prompt_chars = sum(len(t.get("user_prompt", "")) for t in turns)
    prompt_chars_raw = sum(t.get("user_prompt_chars_raw", 0) for t in turns)
    asst_chars = sum(t.get("assistant_chars", 0) for t in turns)
    result_chars = sum(t.get("tool_result_chars", 0) for t in turns)
    ev = {
        "bytes": (session.get("source") or {}).get("size", 0),
        "n_records": session.get("n_records", 0),
        "n_turns": len(turns),
        "n_tool_calls": sum(t.get("n_tool_calls", 0) for t in turns),
        "n_errors": sum(t.get("n_errors", 0) for t in turns),
        "n_compactions": n_compactions,
        "prompt_chars": prompt_chars,
        "prompt_chars_raw": prompt_chars_raw,
        "assistant_chars": asst_chars,
        "tool_result_chars": result_chars,
        "prose_chars": prompt_chars + asst_chars,
        "usage": dict(usage),
        "tools": dict(tools.most_common()),
        "models": models,
        "duration_ms": _duration_ms(turns),
        "started_at": turns[0].get("timestamp", "")
        if turns
        else session.get("started_at", ""),
        "ended_at": turns[-1].get("timestamp", "")
        if turns
        else session.get("ended_at", ""),
        "cost_state": session.get("cost_state"),
        "n_prs": len(session.get("prs") or []),
    }
    return [finding("stats", session, kind="session_stats", evidence=ev)]
