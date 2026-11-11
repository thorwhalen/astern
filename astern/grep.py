"""Regex search over raw transcripts, across every home, with no index to maintain.

The index-backed route (:func:`astern.tools.recall`, over ``ir``) needs ``sync`` and
``index`` to have run and finds paraphrases; this one reads the JSONL files where
they lie and finds exact things: a name, an identifier, an error string, a URL, a
phrase. It is *structure-aware*: the pattern is matched against the text of the
conversational fields only (the human prompt, the assistant's prose, optionally the
tool inputs and session titles), never against the raw JSON line, so the
``CLAUDE.md`` attachment every session carries, JSON escapes and record keys do not
produce hits.

Two stages keep it fast on gigabytes: ``rg -l`` picks the files whose raw bytes
could match (a superset, see :func:`raw_prefilter_pattern`), then only those files
are parsed and matched field by field. Without ``rg`` every candidate is parsed.

>>> import json, tempfile
>>> recs = [
...   {"type": "user", "sessionId": "s1", "cwd": "/p/shop", "timestamp": "2026-10-01T10:00:00Z",
...    "message": {"role": "user", "content": "Where is the Acme-corp agreement?"}},
...   {"type": "assistant", "timestamp": "2026-10-01T10:01:00Z",
...    "message": {"content": [{"type": "text", "text": "In the agreement folder."}]}},
... ]
>>> with tempfile.TemporaryDirectory() as d:
...     p = Path(d, ".claude", "projects", "-p-shop"); p.mkdir(parents=True)
...     _ = Path(p, "s1.jsonl").write_text("\\n".join(json.dumps(r) for r in recs))
...     r = grep_sessions("acme\\\\w*", home=Path(d, ".claude"), use_rg=False)
>>> r["n_sessions"], r["sessions"][0]["n_hits"], r["sessions"][0]["hits"][0]["role"]
(1, 1, 'user')
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from astern.sources import (
    ALL_HOMES,
    SessionFile,
    fast_loads,
    homes,
    iter_session_files,
    load_records,
)
from astern.turns import (
    _blocks,
    _content,
    _text_blocks,
    clean_prompt,
    is_user_prompt,
    tool_input_text,
)

#: The fields a hit can come from. ``user`` is the human prompt (wrapper tags and
#: system reminders stripped), ``assistant`` the assistant's prose, ``tool`` the
#: tool inputs (commands, file bodies, queries), ``title`` the session's titles.
ROLES = ("user", "assistant", "tool", "title")
DFLT_ROLES = "user,assistant,title"

#: How many files ``rg`` is handed per call (keeps argv under the OS limit).
_RG_BATCH = 500


def raw_prefilter_pattern(pattern: str) -> str:
    r"""Widen ``pattern`` so it matches the *raw JSON line* wherever it matches the text.

    In a JSONL transcript a newline inside a message is the two characters ``\n``,
    and a double quote is ``\"``. So ``\s`` becomes ``[\s\\nrt]`` (whitespace, or the
    letters of an escape), likewise ``\W``, ``"`` becomes ``\\?"``, and the assertions that cannot
    hold inside a JSON string (``\b`` after an escaped newline, ``^``, ``$``) are
    dropped. The result only picks candidate lines: it may over-match, the real
    pattern is then applied to the decoded text.

    >>> raw_prefilter_pattern(r'foo\s+bar')
    'foo[\\s\\\\nrt]+bar'
    >>> raw_prefilter_pattern(r'a\W+b')
    'a[\\W\\\\nrt]+b'
    >>> raw_prefilter_pattern('say "hi"')
    'say \\\\?"hi\\\\?"'
    >>> raw_prefilter_pattern(r'^\bac\b$|[^a]')
    'ac|[^a]'
    """
    out = re.sub(r"(?<!\\)\\([sW])", r"[\\\1\\\\nrt]", pattern)
    out = re.sub(r"(?<!\\)\\[bBAzZ]", "", out)
    out = re.sub(r"(?<![\[\\])\^", "", out)
    out = re.sub(r"(?<!\\)\$", "", out)
    return out.replace('"', '\\\\?"')


def _rg_lines(
    files: list[Path], pattern: str, *, ignore_case: bool
) -> dict[Path, list[str]] | None:
    """The raw lines (per file) that could match, or ``None`` if ``rg`` can't say.

    Only these lines are decoded and matched field by field, so a 9 GB home costs
    what ``rg`` costs, not what parsing every record would.
    """
    rg = shutil.which("rg")
    if not rg:
        return None
    raw = raw_prefilter_pattern(pattern)
    out: dict[Path, list[str]] = {}
    for i in range(0, len(files), _RG_BATCH):
        batch = [str(f) for f in files[i : i + _RG_BATCH]]
        argv = [rg, "--null", "--no-line-number", "--no-heading", "--no-messages"]
        argv += ["--no-ignore", "--with-filename", "-e", raw]
        if ignore_case:
            argv.insert(1, "-i")
        proc = subprocess.run(
            [*argv, "--", *batch],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        if proc.returncode == 2 and not proc.stdout:  # a pattern rg can't compile
            return None
        for line in proc.stdout.splitlines():
            path, _, rec = line.partition("\0")
            if rec:
                out.setdefault(Path(path), []).append(rec)
    return out


def _decode(lines: Iterable[str]) -> list[dict]:
    out = []
    for line in lines:
        try:
            obj = fast_loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _head_and_titles(path: Path, *, head_lines: int = 50) -> list[dict]:
    """The first records (for cwd and start time) and every title record of a file."""
    recs: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if (
                    i < head_lines
                    or '"title"' in line[:200].lower()
                    or ('"agent-name"' in line[:200])
                ):
                    recs.extend(_decode([line.strip()]))
    except OSError:
        pass
    return recs


def _fields(records: list[dict], roles: set[str]) -> Iterator[tuple[str, str, str]]:
    """Yield ``(role, timestamp, text)`` for the searchable fields of one transcript."""
    for m in records:
        t = m.get("type")
        ts = m.get("timestamp") or ""
        if t == "user" and "user" in roles and is_user_prompt(m):
            text = clean_prompt(" ".join(_text_blocks(_content(m))))
            if text:
                yield "user", ts, text
        elif t == "assistant":
            if "assistant" in roles:
                for text in _text_blocks(_content(m)):
                    yield "assistant", ts, text
            if "tool" in roles:
                for b in _blocks(m, "tool_use"):
                    text = tool_input_text(b.get("name", ""), b.get("input"))
                    if text:
                        yield "tool", ts, f"[{b.get('name', '')}] {text}"
        elif "title" in roles and t in ("ai-title", "custom-title", "agent-name"):
            key = {"ai-title": "aiTitle", "custom-title": "customTitle"}.get(
                t, "agentName"
            )
            if m.get(key):
                yield "title", ts, str(m[key])


def _snippet(text: str, match: re.Match, *, context: int) -> str:
    a, b = max(0, match.start() - context), min(len(text), match.end() + context)
    s = " ".join(text[a:b].split())
    return ("…" if a else "") + s + ("…" if b < len(text) else "")


def _session_hits(
    sf: SessionFile,
    rx: re.Pattern,
    *,
    lines: list[str] | None,
    roles: set[str],
    context: int,
    max_snippets: int,
    project: str | None,
) -> dict | None:
    """One session's hits. ``lines`` = the prefiltered raw lines, ``None`` = parse all."""
    records = load_records(sf.path) if lines is None else _decode(lines)
    hits, n = [], 0
    for role, ts, text in _fields(records, roles):
        for match in rx.finditer(text):
            n += 1
            if len(hits) < max_snippets:
                hits.append(
                    {
                        "role": role,
                        "timestamp": ts,
                        "text": _snippet(text, match, context=context),
                    }
                )
    if not n:
        return None
    meta = records if lines is None else _head_and_titles(sf.path) + records
    cwd = next((r["cwd"] for r in meta if r.get("cwd")), "")
    if project and project.lower() not in (cwd + " " + sf.project_slug).lower():
        return None
    stamps = sorted(r["timestamp"] for r in meta if r.get("timestamp"))
    titles = [
        r.get("customTitle") or r.get("aiTitle") or r.get("agentName")
        for r in meta
        if r.get("type") in ("custom-title", "ai-title", "agent-name")
    ]
    return {
        "session_id": sf.session_id,
        "home": sf.home,
        "kind": sf.kind,
        "parent_id": sf.parent_id,
        "cwd": cwd,
        "title": next((t for t in reversed(titles) if t), ""),
        "started_at": stamps[0] if stamps else "",
        "ended_at": datetime.fromtimestamp(sf.mtime, timezone.utc).isoformat(
            timespec="seconds"
        ),
        "n_hits": n,
        "hits": hits,
        "path": str(sf.path),
    }


def grep_sessions(
    pattern: str,
    *,
    home: str | Path | Iterable | None = ALL_HOMES,
    project: str | None = None,
    since_days: float | None = None,
    roles: str | Iterable[str] = DFLT_ROLES,
    kinds: str | Iterable[str] = "session",
    ignore_case: bool = True,
    k: int = 20,
    context: int = 100,
    max_snippets: int = 3,
    sort: str = "recent",
    use_rg: bool = True,
) -> dict:
    """Sessions whose conversational text matches the regex ``pattern``, best first.

    ``home`` defaults to every home (:data:`~astern.sources.ALL_HOMES`). ``project``
    is a case-insensitive substring of the session's cwd (or project slug).
    ``roles`` picks the fields (:data:`ROLES`; ``all`` for every one). ``sort`` is
    ``recent`` (last activity first) or ``hits`` (most matches first). The pattern is
    a Python regex; ``ignore_case`` is on by default.
    """
    roles = set(ROLES if roles == "all" else _split(roles))
    unknown = roles - set(ROLES)
    if unknown:
        raise ValueError(f"unknown role(s) {sorted(unknown)}; known: {ROLES}")
    if sort not in ("recent", "hits"):
        raise ValueError(f"sort must be 'recent' or 'hits', not {sort!r}")
    rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    hs = homes(home)
    files = list(
        iter_session_files(hs, since_days=since_days, kinds=tuple(_split(kinds)))
    )
    if project:  # cheap pre-cut on the slug (the cwd with '/' turned into '-')
        slug_part = re.sub(r"[^A-Za-z0-9]", "-", project).lower()
        files = [sf for sf in files if slug_part in sf.project_slug.lower()] or files
    by_file = (
        _rg_lines([sf.path for sf in files], pattern, ignore_case=ignore_case)
        if use_rg
        else None
    )
    scanned = [sf for sf in files if by_file is None or sf.path in by_file]
    rows = []
    for sf in scanned:
        row = _session_hits(
            sf,
            rx,
            lines=None if by_file is None else by_file[sf.path],
            roles=roles,
            context=context,
            max_snippets=max_snippets,
            project=project,
        )
        if row:
            rows.append(row)
    key = (lambda r: r["ended_at"]) if sort == "recent" else (lambda r: r["n_hits"])
    rows.sort(key=key, reverse=True)
    return {
        "pattern": pattern,
        "homes": {h.name: str(h.path) for h in hs},
        "n_files": len(files),
        "n_scanned": len(scanned),
        "prefilter": "rg" if by_file is not None else "none",
        "n_sessions": len(rows),
        "sessions": rows[:k],
    }


def _split(x: str | Iterable[str]) -> list[str]:
    if isinstance(x, str):
        return [s.strip() for s in x.split(",") if s.strip()]
    return list(x)
