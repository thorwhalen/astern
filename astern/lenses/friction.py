"""``friction``: where an agent got stuck. Zero tokens; pure pattern detection.

One finding per stuck moment, all pointing back at the turn that produced it
(``finding(..., turn=turn)``). Six kinds:

- ``tool_error``    a tool result came back with ``is_error``.
- ``retry``         the same ``(tool, digest)`` pair recurs in the same turn or in
                    consecutive turns — the shape of "try again, slightly differently".
- ``pivot``         assistant prose contains a phrase that signals a change of plan
                    (:data:`PIVOT_PHRASES`).
- ``compaction``    a ``compact_boundary`` system record — context got full enough to
                    summarize away.
- ``long_turn``     a turn's wall-clock duration (``turn_duration`` system record)
                    is at or above :data:`LONG_TURN_MS`.
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

from collections import defaultdict
from collections.abc import Iterator

from astern.lenses import finding, lens

#: A turn at or above this wall-clock duration is "long" enough to be friction on
#: its own, independent of whether anything errored.
LONG_TURN_MS = 120_000

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


def _error_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("is_error"):
            yield finding("friction", session, kind="tool_error", turn=turn,
                          evidence={"tool": tc["name"], "digest": tc.get("digest", "")})


def _pivot_findings(session: dict, turn: dict) -> Iterator[dict]:
    text = (turn.get("assistant_full") or "")
    low = text.lower()
    for phrase in PIVOT_PHRASES:
        idx = low.find(phrase)
        if idx >= 0:
            yield finding("friction", session, kind="pivot", turn=turn,
                          evidence={"phrase": phrase, "snippet": text[max(0, idx - 40):idx + 80]})


def _compaction_findings(session: dict, turn: dict) -> Iterator[dict]:
    for s in turn.get("system") or []:
        if s.get("subtype") == "compact_boundary":
            meta = s.get("compactMetadata") or {}
            yield finding("friction", session, kind="compaction", turn=turn,
                          evidence={"pre_tokens": meta.get("preTokens"), "post_tokens": meta.get("postTokens")})


def _long_turn_findings(session: dict, turn: dict, *, threshold_ms: int = LONG_TURN_MS) -> Iterator[dict]:
    for s in turn.get("system") or []:
        is_duration = s.get("subtype") == "turn_duration" and isinstance(s.get("durationMs"), (int, float))
        if is_duration and s["durationMs"] >= threshold_ms:
            yield finding("friction", session, kind="long_turn", turn=turn,
                          evidence={"duration_ms": s["durationMs"], "threshold_ms": threshold_ms})


def _tool_search_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("name") == "ToolSearch":
            yield finding("friction", session, kind="tool_search", turn=turn,
                          evidence={"query": tc.get("digest", "")})


def _retry_findings(session: dict, turns: list[dict]) -> Iterator[dict]:
    """Same ``(tool, digest)`` appearing again in the same turn, or in the next one."""
    occurrences: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_index = {t["index"]: t for t in turns}
    for t in turns:
        for tc in t.get("tools") or []:
            occurrences[(tc.get("name", ""), tc.get("digest", ""))].append(t["index"])
    for (name, digest), idxs in occurrences.items():
        idxs = sorted(idxs)
        i = 0
        while i < len(idxs):
            j = i
            while j + 1 < len(idxs) and idxs[j + 1] - idxs[j] <= 1:
                j += 1
            run = idxs[i:j + 1]
            if len(run) >= MIN_RETRY_ATTEMPTS:
                last_turn = by_index.get(run[-1])
                yield finding("friction", session, kind="retry", turn=last_turn,
                              evidence={"tool": name, "digest": digest, "attempts": len(run),
                                       "turn_indices": run})
            i = j + 1


@lens("friction", version=1, kind="H")
def friction(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Detect stuck moments: errors, retries, pivots, compactions, long turns, tool search."""
    out: list[dict] = []
    for t in turns:
        out.extend(_error_findings(session, t))
        out.extend(_pivot_findings(session, t))
        out.extend(_compaction_findings(session, t))
        out.extend(_long_turn_findings(session, t))
        out.extend(_tool_search_findings(session, t))
    out.extend(_retry_findings(session, turns))
    return out
