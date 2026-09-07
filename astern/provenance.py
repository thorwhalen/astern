"""From a line of code back to the session turn that wrote it.

The chain, and why each link is needed:

1. **git** — ``git blame -L`` names the *last* commit to touch the line, which after
   a CI ``ruff format`` pass is a bot; ``git log -S`` names the commit that
   *introduced* the text. Both are reported, because when they differ the second is
   almost always the one you wanted.
2. **the trailer** — a ``Claude-Session: https://claude.ai/code/session_<id>`` line in
   the commit message. That ``<id>`` is the *claude.ai* session id, which is **not**
   the local transcript's ``sessionId``: the transcript carries a ``bridge-session``
   record (``{"sessionId": <uuid>, "bridgeSessionId": "cse_<id>"}``) that joins the
   two, and :func:`astern.turns.session_meta` keeps it as ``bridge_session_id``.
3. **the store** — the session's turns *and its subagents'* turns are searched for the
   ``Edit`` / ``Write`` / ``Bash`` call whose ``input_text`` contains the line. The
   subagent half is not optional: an agent that delegates writes the code from the
   subagent transcript, and the parent's turns never contain the line.

With no trailer (older work, another machine) step 2 has nothing to say, so step 3
runs over every session whose ``cwd`` is inside the repo, narrowed to a date window
around the commit.

>>> normalize('  return  sum(x)   ')
'return sum(x)'
>>> loose("f('a', 12)")
"f('a', 0)"
>>> session_id_from_trailer('feat: x\\n\\nClaude-Session: https://claude.ai/code/session_01ABC')
'01ABC'
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from pathlib import Path

from astern.store import Store

#: The tools whose ``input_text`` can carry a line of source, best first. A ``Write``
#: or ``Edit`` *is* the line; a ``Bash`` heredoc merely might be, so it ranks lower.
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash",)

#: How much of the turn's prose to carry back, per the answer's contract.
PROMPT_CHARS = 300
EXCERPT_CHARS = 500

#: A line shorter than this matches too much to be evidence of anything.
MIN_NEEDLE_CHARS = 8

#: The same floor for a *path* needle, which is specific at a length a line is not.
MIN_PATH_CHARS = 4

_TRAILER_RE = re.compile(r"Claude-Session:\s*(\S+)", re.IGNORECASE)
_SESSION_IN_URL_RE = re.compile(r"session_([A-Za-z0-9]+)")
_QUOTES_RE = re.compile(r"[\"'`]")
_DIGITS_RE = re.compile(r"\d+")


# --- normalization ------------------------------------------------------------


def normalize(text: str) -> str:
    """Collapse every run of whitespace, so indentation and reflow stop mattering.

    >>> normalize('a\\t b\\n  c ')
    'a b c'
    """
    return " ".join(str(text).split())


def loose(text: str) -> str:
    """:func:`normalize`, then flatten quote style and every integer literal.

    A formatter rewrites ``'x'`` to ``"x"`` and a refactor renumbers a constant
    without either being a different line; this is the second pass that still finds it.

    >>> loose('assert n == 42, "boom"')
    "assert n == 0, 'boom'"
    """
    return _DIGITS_RE.sub("0", _QUOTES_RE.sub("'", normalize(text)))


# --- git ----------------------------------------------------------------------


def _git(repo: str | Path, *args: str, timeout: float = 60.0) -> str:
    """Run one ``git`` command in ``repo``; empty string if it fails at all."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def repo_root(path: str | Path) -> str | None:
    """The work tree containing ``path`` (a file or a directory), or ``None``."""
    p = Path(path).expanduser().resolve()
    start = p if p.is_dir() else p.parent
    root = _git(start, "rev-parse", "--show-toplevel").strip()
    return root or None


def session_id_from_trailer(message: str) -> str | None:
    """The claude.ai session id in a commit message's ``Claude-Session:`` trailer.

    Accepts the URL form and a bare ``session_<id>``; returns the id without prefix.

    >>> session_id_from_trailer('no trailer here') is None
    True
    >>> session_id_from_trailer('x\\n\\nClaude-Session: session_01Z9')
    '01Z9'
    """
    m = _TRAILER_RE.search(message or "")
    if not m:
        return None
    found = _SESSION_IN_URL_RE.search(m.group(1))
    return found.group(1) if found else m.group(1).strip() or None


def bridge_key(raw: str) -> str:
    """The comparable half of a bridge id: ``cse_01AB`` and ``session_01AB`` agree.

    The transcript's ``bridge-session`` record prefixes the id ``cse_``; the commit
    trailer's URL prefixes the same id ``session_``. Only the tail is the identity.

    >>> bridge_key('cse_01AB'), bridge_key('session_01AB'), bridge_key('01AB')
    ('01AB', '01AB', '01AB')
    """
    raw = (raw or "").strip()
    return raw.rsplit("_", 1)[-1] if "_" in raw else raw


def commit_record(repo: str | Path, sha: str) -> dict | None:
    """One commit as ``{sha, short_sha, subject, author, date, session_id}``."""
    if not sha:
        return None
    out = _git(repo, "show", "-s", "--format=%H%x00%h%x00%s%x00%an%x00%aI%x00%B", sha)
    if not out:
        return None
    parts = out.split("\x00")
    if len(parts) < 6:
        return None
    return {
        "sha": parts[0].strip(),
        "short_sha": parts[1].strip(),
        "subject": parts[2].strip(),
        "author": parts[3].strip(),
        "date": parts[4].strip(),
        "session_id": session_id_from_trailer(parts[5]),
    }


def blame_commit(repo: str | Path, rel_path: str, line: int) -> str:
    """The sha of the commit that last touched ``line`` (``''`` when blame fails)."""
    out = _git(repo, "blame", "-L", f"{line},{line}", "--porcelain", "--", str(rel_path))
    if not out:
        return ""
    first = out.splitlines()[0].split(" ", 1)[0]
    return "" if set(first) == {"0"} else first


def introducing_commit(repo: str | Path, rel_path: str, text: str) -> str:
    """The oldest commit whose diff changed the count of ``text`` in ``rel_path``."""
    needle = normalize(text)
    if len(needle) < MIN_NEEDLE_CHARS:
        return ""
    out = _git(
        repo,
        "log",
        "--reverse",
        "--format=%H",
        f"-S{text.strip()}",
        "--",
        str(rel_path),
    )
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[0].strip() if lines else ""


def line_text(repo: str | Path, rel_path: str, line: int) -> str:
    """Line ``line`` of the working-tree file (1-based), or ``''``."""
    p = Path(repo) / rel_path
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, raw in enumerate(f, 1):
                if i == line:
                    return raw.rstrip("\n")
    except OSError:
        return ""
    return ""


def changed_files(repo: str | Path, sha: str) -> list[str]:
    """The paths one commit touched."""
    out = _git(repo, "show", "--name-only", "--format=", sha)
    return [l.strip() for l in out.splitlines() if l.strip()]


# --- the Entire CLI, when this repo is enabled ---------------------------------


def entire_available(repo: str | Path) -> bool:
    """Is the ``entire`` binary on PATH *and* enabled in this repo?"""
    if shutil.which("entire") is None:
        return False
    return (Path(repo) / ".entire" / "settings.json").is_file()


def entire_why(repo: str | Path, rel_path: str, line: int, *, timeout: float = 60.0):
    """``entire why <file>:<line> --json``, verbatim; ``None`` when unavailable.

    Generation-time provenance, and therefore the better answer when it exists —
    Entire recorded the checkpoint as the line was written. It only speaks for work
    done after the repo was enabled, which is why astern's own chain runs regardless.
    """
    if not entire_available(repo):
        return None
    try:
        out = subprocess.run(
            ["entire", "why", f"{rel_path}:{line}", "--json"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if out.returncode != 0:
        return {"error": (out.stderr or out.stdout).strip()[:500]}
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {"error": "entire why --json did not return JSON"}


# --- searching the store ------------------------------------------------------


def _iso(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def sessions_for_bridge(store: Store, bridge_id: str) -> list[str]:
    """Local session ids whose ``bridge_session_id`` is the trailer's claude.ai id."""
    want = bridge_key(bridge_id)
    return [
        sid
        for sid in store.sessions
        if bridge_key(store.sessions[sid].get("bridge_session_id") or "") == want and want
    ]


def with_subagents(store: Store, session_ids: Iterable[str]) -> list[str]:
    """``session_ids`` plus every stored transcript nested under one of them."""
    out = list(dict.fromkeys(session_ids))
    parents = set(out)
    for sid in store.sessions:
        if sid in parents:
            continue
        if (store.sessions[sid].get("parent_id") or "") in parents:
            out.append(sid)
    return out


def sessions_in_repo(
    store: Store, repo: str, *, around=None, window_days: float = 14.0
) -> list[str]:
    """Session ids whose ``cwd`` is inside ``repo``, optionally near a commit date.

    A worktree session's ``cwd`` is still under the repo root, so this keeps them;
    a session that edited the repo from somewhere else is what the date window and
    the whole-store scan are for.
    """
    root = str(Path(repo).resolve())
    lo = hi = None
    if around is not None:
        lo, hi = around - timedelta(days=window_days), around + timedelta(days=1)
    out = []
    for sid in store.sessions:
        s = store.sessions[sid]
        cwd = str(s.get("cwd") or "")
        if not cwd or not (cwd == root or cwd.startswith(root + os.sep)):
            continue
        if lo is not None:
            when = _iso(s.get("ended_at") or "") or _iso(s.get("started_at") or "")
            if when is None or not (lo <= when <= hi):
                continue
        out.append(sid)
    return out


def _tool_rank(name: str) -> int:
    if name in WRITE_TOOLS:
        return 0
    return 1 if name in SHELL_TOOLS else 2


def iter_hits(
    store: Store,
    session_ids: Iterable[str],
    text: str,
    *,
    fields: tuple[str, ...] = ("input_text",),
    min_chars: int = MIN_NEEDLE_CHARS,
) -> Iterator[dict]:
    """Every tool call in those sessions whose input text contains ``text``.

    Two passes over each candidate: the whitespace-normalized line (``match='exact'``)
    and then :func:`loose` (``match='loose'``), so a reformatted or renumbered line is
    still found, and the answer says which pass found it.

    ``fields`` says which part of the recorded tool call to search. ``input_text`` is
    the body a ``Write`` wrote or an ``Edit`` inserted, and is what a *line* is found
    in; ``digest`` is the call's short label (a path, a command), and is what a *file*
    is found in — a ``Write``'s body never contains its own path.

    ``min_chars`` is the floor below which a needle matches too much to be evidence.
    It is lower for a path than for a line: ``mod.py`` is short but still specific,
    where a six-character *line* is not.
    """
    strict, fuzzy = normalize(text), loose(text)
    if len(strict) < min_chars:
        return
    for sid in session_ids:
        if sid not in store.turns:
            continue
        session = store.sessions.get(sid, {})
        for turn in store.turns[sid]:
            for tool in turn.get("tools") or ():
                body = " ".join(str(tool.get(f) or "") for f in fields)
                if not body.strip():
                    continue
                if strict in normalize(body):
                    match = "exact"
                elif fuzzy in loose(body):
                    match = "loose"
                else:
                    continue
                yield {
                    "session_id": sid,
                    "parent_id": session.get("parent_id") or None,
                    "turn_index": turn.get("index"),
                    "timestamp": turn.get("timestamp") or "",
                    "tool": tool.get("name") or "",
                    "digest": tool.get("digest") or "",
                    "match": match,
                    "user_prompt": (turn.get("user_prompt") or "")[:PROMPT_CHARS],
                    "assistant_excerpt": (
                        turn.get("assistant_full") or turn.get("assistant_summary") or ""
                    )[:EXCERPT_CHARS],
                }


def rank_hits(hits: Iterable[dict]) -> list[dict]:
    """Best evidence first: an exact match, from a writing tool, earliest in time.

    Earliest wins because the question is *who wrote this line*, and a later turn
    that merely re-wrote the same file is the answer to a different question.

    >>> a = {'match': 'loose', 'tool': 'Write', 'timestamp': '2026-01-01'}
    >>> b = {'match': 'exact', 'tool': 'Bash', 'timestamp': '2026-01-02'}
    >>> c = {'match': 'exact', 'tool': 'Write', 'timestamp': '2026-01-03'}
    >>> [h['tool'] for h in rank_hits([a, b, c])]
    ['Write', 'Bash', 'Write']
    """
    return sorted(
        hits,
        key=lambda h: (
            0 if h.get("match") == "exact" else 1,
            _tool_rank(h.get("tool") or ""),
            h.get("timestamp") or "",
        ),
    )
