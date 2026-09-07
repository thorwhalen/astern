"""The compressed markdown a judge reads instead of a transcript.

A session file here is a median 1.2 MB and 88% of it is tool I/O. A judge does not
need the tool I/O: it needs what was asked, what was done, and whether it worked.
:func:`session_view` renders that — a header, then one block per turn carrying the
human's prompt, a one-line tool summary and the assistant's closing text — and
guarantees a ceiling on its own size, which is what makes the cost of a batch
predictable at all.

Shrinking happens in a fixed order, and the view says what it did, because a judge
that is silently handed half a session will confidently synopsise half a session:

1. per-turn budgets are scaled down proportionally (never below :data:`MIN_*`),
2. then middle turns are dropped, keeping the first and the last ones — the goal is
   usually stated at the start and the outcome shown at the end,
3. and an explicit ``turns … omitted`` line goes into the view either way.

When ``prior`` is given — the synopsis a previous run produced for the same session
— the view opens with a short "Previously in this session" recap and then covers
only the new turns. That is the incremental path: a resumed session pays for its
new turns only.

>>> turns = [{'index': 0, 'timestamp': '2026-09-01T10:00:00Z', 'user_prompt': 'fix the build',
...           'assistant_full': 'Fixed it.', 'tools': [
...              {'name': 'Bash', 'digest': 'uv build', 'is_error': True},
...              {'name': 'Bash', 'digest': 'uv build', 'is_error': False}]}]
>>> v = session_view({'session_id': 'abc123', 'title': 'Build fix'}, turns)
>>> print(v)  # doctest: +NORMALIZE_WHITESPACE
# Session abc123 — Build fix
<BLANKLINE>
turns: 1 (showing 1)
<BLANKLINE>
### turn 0 (2026-09-01T10:00:00Z)
user: fix the build
tools: Bash×2, 1 error: Bash "uv build"
assistant: Fixed it.
>>> view_stats(v)['n_turns_shown'], view_stats(v)['n_turns_dropped']
(1, 0)
"""

from __future__ import annotations

from collections import Counter

#: A judge call is ~4 chars per token, so a 24k-char view is ~6k input tokens: under
#: the plan's ≤8k-per-synopsis budget with room for the schema and the system prompt.
DFLT_MAX_CHARS = 24000
DFLT_PROMPT_CHARS = 1200
DFLT_REPLY_CHARS = 1500

#: Floors for the proportional-shrink step. Below these a turn stops being evidence
#: and becomes noise, so the next step (dropping whole turns) is the honest one.
MIN_PROMPT_CHARS = 160
MIN_REPLY_CHARS = 160
MIN_TOOL_ERRORS = 2

_OMITTED = "_… {n} turns omitted from the middle …_"


def _oneline(text: str, limit: int) -> str:
    """Collapse whitespace and clip to ``limit``.

    >>> _oneline('a\\n  b', 10)
    'a b'
    >>> _oneline('abcdef', 3)
    'abc…'
    """
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _clip(text: str, limit: int) -> str:
    """Clip a block, saying how much was dropped (a judge must know it saw a part).

    >>> _clip('hello', 99)
    'hello'
    >>> _clip('abcdefghij', 4)
    'abcd… (+6 chars cut)'
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"… (+{len(text) - limit} chars cut)"


def tool_summary(turn: dict, *, max_errors: int = 3, digest_chars: int = 70) -> str:
    """One line for a turn's tool calls: the mix, then the errors that matter.

    >>> tool_summary({'tools': [{'name': 'Bash', 'digest': 'ls', 'is_error': False},
    ...                         {'name': 'Bash', 'digest': 'uv build', 'is_error': True},
    ...                         {'name': 'Edit', 'digest': '/a.py', 'is_error': False}]})
    'Bash×2 Edit, 1 error: Bash "uv build"'
    >>> tool_summary({'tools': []})
    ''
    """
    tools = turn.get("tools") or []
    if not tools:
        return ""
    counts = Counter(t.get("name", "?") for t in tools)
    mix = " ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.most_common())
    errs = [t for t in tools if t.get("is_error")]
    if not errs:
        return mix
    shown = ", ".join(f'{t.get("name", "?")} "{_oneline(t.get("digest", ""), digest_chars)}"'
                      for t in errs[:max_errors])
    return f'{mix}, {len(errs)} error{"s" if len(errs) > 1 else ""}: {shown}'


def _header(session: dict, *, n_turns: int, n_shown: int) -> str:
    sid = session.get("session_id", "")
    title = (session.get("title") or "").strip()
    lines = [f"# Session {sid[:8]}" + (f" — {title}" if title else ""), ""]
    facts = []
    if session.get("project"):
        facts.append(f"project: {session['project']}")
    if session.get("git_branch"):
        facts.append(f"branch: {session['git_branch']}")
    span = " → ".join(x for x in (session.get("started_at", ""), session.get("ended_at", "")) if x)
    if span:
        facts.append(span)
    models = [m.split("-2")[0] for m in (session.get("models") or [])]
    if models:
        facts.append("models: " + ", ".join(dict.fromkeys(models)))
    prs = [f"{p.get('repo') or ''}#{p.get('number')}" for p in (session.get("prs") or [])]
    if prs:
        facts.append("PRs: " + ", ".join(prs))
    if facts:
        lines += [" | ".join(facts), ""]
    lines.append(f"turns: {n_turns} (showing {n_shown})")
    return "\n".join(lines)


def _prior_block(prior: dict, *, limit: int = 1500) -> str:
    """Render a previous synopsis briefly, so an increment extends rather than redoes."""
    if not prior:
        return ""
    lines = ["## Previously in this session", ""]
    if prior.get("goal"):
        lines.append(f"goal: {_oneline(str(prior['goal']), 300)}")
    if prior.get("outcome"):
        lines.append(f"outcome so far: {prior['outcome']}")
    for key, field in (("problems", "problem"), ("friction", "what"),
                       ("corrections", "what_user_said"), ("skill_candidates", "name")):
        items = prior.get(key) or []
        got = [_oneline(str(i.get(field, i) if isinstance(i, dict) else i), 90) for i in items[:4]]
        if got:
            lines.append(f"{key}: " + "; ".join(got))
    return _clip("\n".join(lines), limit)


def _turn_block(turn: dict, *, prompt_chars: int, reply_chars: int, max_errors: int) -> str:
    head = f"### turn {turn.get('index', '?')}"
    ts = turn.get("timestamp") or ""
    if ts:
        head += f" ({ts})"
    parts = [head]
    prompt = _clip(turn.get("user_prompt", ""), prompt_chars)
    parts.append(f"user: {prompt}" if prompt else "user: (no prose)")
    tools = tool_summary(turn, max_errors=max_errors)
    if tools:
        parts.append(f"tools: {tools}")
    reply = _clip(turn.get("assistant_full") or turn.get("assistant_summary", ""), reply_chars)
    if reply:
        parts.append(f"assistant: {reply}")
    return "\n".join(parts)


def _assemble(header: str, prior_text: str, blocks: list[str], n_dropped: int) -> str:
    chunks = [header]
    if prior_text:
        chunks.append(prior_text)
    if n_dropped:
        half = len(blocks) // 2
        blocks = blocks[:half] + [_OMITTED.format(n=n_dropped)] + blocks[half:]
    chunks += blocks
    return "\n\n".join(c for c in chunks if c).strip() + "\n"


def session_view(session: dict, turns: list[dict], *, max_chars: int = DFLT_MAX_CHARS,
                 prompt_chars: int = DFLT_PROMPT_CHARS, reply_chars: int = DFLT_REPLY_CHARS,
                 prior: dict | None = None) -> str:
    """A judge-sized markdown view of ``turns``, never longer than ``max_chars``.

    ``prior`` is the previous synopsis dict for the same session; when given, the
    view recaps it and ``turns`` should be the new turns only.

    >>> ts = [{'index': i, 'user_prompt': 'x' * 400, 'assistant_full': 'y' * 400,
    ...        'tools': []} for i in range(20)]
    >>> v = session_view({'session_id': 's'}, ts, max_chars=2000)
    >>> len(v) <= 2000, view_stats(v)['n_turns_dropped'] > 0
    (True, True)
    >>> 'Previously in this session' in session_view({'session_id': 's'}, ts[:1],
    ...                                              prior={'goal': 'ship it'})
    True
    """
    n_turns = len(turns)
    prior_text = _prior_block(prior or {})
    header = _header(session, n_turns=n_turns, n_shown=n_turns)
    fixed = len(header) + len(prior_text) + 8

    def build(pc: int, rc: int, me: int, keep: list[dict], dropped: int) -> str:
        blocks = [_turn_block(t, prompt_chars=pc, reply_chars=rc, max_errors=me) for t in keep]
        head = _header(session, n_turns=n_turns, n_shown=len(keep))
        return _assemble(head, prior_text, blocks, dropped)

    view = build(prompt_chars, reply_chars, 3, turns, 0)
    if len(view) <= max_chars or not turns:
        return view
    # 1. shrink every turn proportionally before losing any turn entirely.
    body = max(1, len(view) - fixed)
    scale = max(0.0, (max_chars - fixed) / body)
    pc = max(MIN_PROMPT_CHARS, int(prompt_chars * scale))
    rc = max(MIN_REPLY_CHARS, int(reply_chars * scale))
    view = build(pc, rc, MIN_TOOL_ERRORS, turns, 0)
    if len(view) <= max_chars:
        return view
    # 2. drop from the middle: the goal is stated first, the outcome shown last.
    keep = list(turns)
    dropped = 0
    while len(keep) > 2 and len(view) > max_chars:
        keep.pop(len(keep) // 2)
        dropped += 1
        view = build(pc, rc, MIN_TOOL_ERRORS, keep, dropped)
    if len(view) > max_chars:  # two turns that still do not fit: hard clip, and say so
        view = view[: max_chars - 24].rstrip() + "\n\n_… view truncated …_\n"
    return view


def view_stats(text: str) -> dict:
    """Size and coverage of a rendered view — the cost model's cheap regressor.

    >>> view_stats('# Session s\\n\\n### turn 0\\nuser: hi\\n')
    {'chars': 33, 'tokens_est': 8, 'n_turns_shown': 1, 'n_turns_dropped': 0}
    """
    shown = text.count("\n### turn ") + (1 if text.startswith("### turn ") else 0)
    dropped = 0
    for line in text.splitlines():
        if line.startswith("_… ") and "omitted" in line:
            head = line.split("_… ", 1)[1].split(" ", 1)[0]
            dropped = int(head) if head.isdigit() else 0
    return {"chars": len(text), "tokens_est": len(text) // 4, "n_turns_shown": shown,
            "n_turns_dropped": dropped}
