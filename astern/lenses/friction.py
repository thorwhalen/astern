"""``friction``: where an agent got stuck. Zero tokens; pure pattern detection.

One finding per stuck moment, all pointing back at the turn that produced it
(``finding(..., turn=turn)``). Six kinds:

- ``tool_error``    a tool result came back with ``is_error``.
- ``retry``         the same tool is issued again within the same turn or the next
                    one, either with the *same full input* (whitespace-normalized
                    ``input_text``) — "try that again" — or with the same ``digest``
                    right after an ``is_error`` result — "try again, differently".
                    Successive successful edits of one path (different content) are
                    not a retry, and neither is a plain re-``Read`` of a path unless
                    it follows an error: for ``Read`` the digest *is* the input text
                    (see :func:`astern.turns.tool_input_text`), so without this
                    exception every re-read would look like a repeat.
- ``pivot``         assistant prose contains a phrase that signals a change of plan
                    (:data:`PIVOT_PHRASES`).
- ``compaction``    a ``compact_boundary`` system record — context got full enough to
                    summarize away.
- ``long_turn``     a turn's wall-clock duration (``turn_duration`` system record) is
                    at or above ``max(``:data:`LONG_TURN_MS`, the session's own
                    :data:`LONG_TURN_PERCENTILE` th percentile)`` — a floor plus an
                    outlier bar relative to *this* session, so a session made of long
                    autonomous turns doesn't flag every one of them.
- ``tool_search``   a ``ToolSearch`` call — a deferred tool had to be looked up before
                    it could be used at all.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "fix it"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "x",
...    "usage": {}, "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
...    "input": {"command": "pytest -q"}}, {"type": "text",
...    "text": "That didn't work, let me try another approach."}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user", "content":
...    [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> kinds = sorted({f["kind"] for f in friction({"session_id": "s"}, t)})
>>> "pivot" in kinds and "tool_error" in kinds
True
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterator

from astern.lenses import finding, lens

#: A turn at or above this wall-clock duration is "long" enough to be friction on
#: its own, independent of whether anything errored. Also the floor under the
#: per-session percentile bar (:data:`LONG_TURN_PERCENTILE`) — a quiet session with a
#: handful of quick turns should never have its 90th percentile flag something short.
LONG_TURN_MS = 120_000

#: A turn is "long" when its duration is at or above the session's own turn-duration
#: distribution at this percentile (and at or above :data:`LONG_TURN_MS`). Flags the
#: outliers *for this session* rather than every turn past one fleet-wide number.
LONG_TURN_PERCENTILE = 90

#: Substrings in assistant prose that signal an in-flight change of plan. Lowercase;
#: matched against lowercased text.
PIVOT_PHRASES = (
    "let me try another approach",
    "that didn't work",
    "instead, i'll",
    "hmm",
    "wait,",
)

#: A retry run needs at least this many attempts to be worth reporting.
MIN_RETRY_ATTEMPTS = 2

#: Tools excluded from the "same full input" retry rule. For these tools
#: :func:`astern.turns.tool_input_text` falls back to the digest (there is no
#: distinguishing content beyond the path/query), so two unrelated, successful
#: calls would otherwise look identical and be mistaken for a retry. Such tools
#: can still produce a ``retry`` finding through the after-error rule.
RETRY_INPUT_MATCH_EXCLUDED_TOOLS = frozenset({"Read"})


def _error_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("is_error"):
            yield finding(
                "friction",
                session,
                kind="tool_error",
                turn=turn,
                evidence={"tool": tc["name"], "digest": tc.get("digest", "")},
            )


def _pivot_findings(session: dict, turn: dict) -> Iterator[dict]:
    text = turn.get("assistant_full") or ""
    low = text.lower()
    for phrase in PIVOT_PHRASES:
        idx = low.find(phrase)
        if idx >= 0:
            yield finding(
                "friction",
                session,
                kind="pivot",
                turn=turn,
                evidence={"phrase": phrase, "snippet": text[max(0, idx - 40) : idx + 80]},
            )


def _compaction_findings(session: dict, turn: dict) -> Iterator[dict]:
    for s in turn.get("system") or []:
        if s.get("subtype") == "compact_boundary":
            meta = s.get("compactMetadata") or {}
            yield finding(
                "friction",
                session,
                kind="compaction",
                turn=turn,
                evidence={
                    "pre_tokens": meta.get("preTokens"),
                    "post_tokens": meta.get("postTokens"),
                },
            )


def _turn_durations_ms(turns: list[dict]) -> list[float]:
    """Every ``turn_duration`` value in the session, in no particular order."""
    return [
        s["durationMs"]
        for t in turns
        for s in (t.get("system") or [])
        if s.get("subtype") == "turn_duration"
        and isinstance(s.get("durationMs"), (int, float))
    ]


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (no numpy dependency).

    >>> _percentile([1, 2, 3, 4, 5], 90)
    4.6
    >>> _percentile([7], 90)
    7
    >>> _percentile([], 90)
    0
    """
    if not values:
        return 0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * (pct / 100)
    lo, hi = int(rank), min(int(rank) + 1, len(xs) - 1)
    if lo == hi:
        return xs[lo]
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def _long_turn_threshold(turns: list[dict]) -> tuple[float, float]:
    """``(threshold_ms, session_median_ms)`` for this session's own turn durations."""
    durations = _turn_durations_ms(turns)
    if not durations:
        return LONG_TURN_MS, 0
    p = _percentile(durations, LONG_TURN_PERCENTILE)
    return max(LONG_TURN_MS, p), statistics.median(durations)


def _long_turn_findings(
    session: dict, turn: dict, *, threshold_ms: float, session_median_ms: float
) -> Iterator[dict]:
    for s in turn.get("system") or []:
        is_duration = s.get("subtype") == "turn_duration" and isinstance(
            s.get("durationMs"), (int, float)
        )
        if is_duration and s["durationMs"] >= threshold_ms:
            yield finding(
                "friction",
                session,
                kind="long_turn",
                turn=turn,
                evidence={
                    "duration_ms": s["durationMs"],
                    "threshold_ms": threshold_ms,
                    "session_median_ms": session_median_ms,
                },
            )


def _tool_search_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("name") == "ToolSearch":
            yield finding(
                "friction",
                session,
                kind="tool_search",
                turn=turn,
                evidence={"query": tc.get("digest", "")},
            )


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


def _is_retry_step(prev: dict, cur: dict, *, tool: str) -> bool:
    """Does ``cur`` count as a repeat of ``prev`` (same tool, adjacent occurrence)?"""
    same_input = (
        tool not in RETRY_INPUT_MATCH_EXCLUDED_TOOLS
        and prev["input_norm"] == cur["input_norm"]
    )
    after_error = bool(prev["is_error"])
    return same_input or after_error


def _retry_findings(session: dict, turns: list[dict]) -> Iterator[dict]:
    """A tool reissued within the same/next turn, same full input or after an error."""
    by_index = {t["index"]: t for t in turns}

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in turns:
        for tc in t.get("tools") or []:
            key = (tc.get("name", ""), tc.get("digest", ""))
            groups[key].append(
                {
                    "turn_index": t["index"],
                    "input_norm": _normalize_ws(tc.get("input_text", "")),
                    "is_error": bool(tc.get("is_error")),
                }
            )

    for (tool, digest), items in groups.items():
        # `items` is already chronological: turns are visited in order and, within a
        # turn, tools keep call order (astern.turns._tools preserves it).
        i, n = 0, len(items)
        while i < n:
            chain = [items[i]]
            j = i + 1
            followed_error = False
            while j < n:
                prev, cur = chain[-1], items[j]
                if cur["turn_index"] - prev["turn_index"] > 1:
                    break
                if not _is_retry_step(prev, cur, tool=tool):
                    break
                followed_error = followed_error or prev["is_error"]
                chain.append(cur)
                j += 1
            if len(chain) >= MIN_RETRY_ATTEMPTS:
                turn_indices = [c["turn_index"] for c in chain]
                last_turn = by_index.get(turn_indices[-1])
                yield finding(
                    "friction",
                    session,
                    kind="retry",
                    turn=last_turn,
                    evidence={
                        "tool": tool,
                        "digest": digest,
                        "attempts": len(chain),
                        "turn_indices": turn_indices,
                        "first_turn_index": turn_indices[0],
                        "after_error": followed_error,
                    },
                )
                i = j
            else:
                i += 1


@lens("friction", version=2, kind="H")
def friction(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Detect stuck moments: errors, retries, pivots, compactions, long turns, tool search."""
    threshold_ms, session_median_ms = _long_turn_threshold(turns)
    out: list[dict] = []
    for t in turns:
        out.extend(_error_findings(session, t))
        out.extend(_pivot_findings(session, t))
        out.extend(_compaction_findings(session, t))
        out.extend(
            _long_turn_findings(
                session, t, threshold_ms=threshold_ms, session_median_ms=session_median_ms
            )
        )
        out.extend(_tool_search_findings(session, t))
    out.extend(_retry_findings(session, turns))
    return out
