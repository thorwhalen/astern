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

import os
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


#: A decoded character JSON may have written as an escape: ``\"``, ``\\``, ``\n``,
#: ``\t``, ``\uXXXX``... An atom that can match such a character is OR-ed with this.
_JSON_ESCAPE = r"\\(?:u[0-9a-fA-F]{4}|[^u])"

#: Escapes that stand for one character JSON never escapes (word chars, digits) and
#: escaped punctuation other than quote and backslash: safe to keep as they are.
_SAFE_ESCAPES = set("wd") | set(".^$|?*+()[]{}/-#&~,:;=!<>%@'` ")


def raw_prefilter_pattern(pattern: str) -> str:
    r"""Widen ``pattern`` so it matches the *raw JSON line* wherever it matches the text.

    The real pattern runs on the decoded text of a field; this one only picks the
    raw lines worth decoding, so it may over-match but must never under-match. In
    JSON a quote, a backslash and control characters are escaped (``\"``, ``\\``,
    ``\n``, ``\u001b``), so every atom that could match one of those (``.``, ``\s``,
    ``\W``, ``\S``, ``\D``, a character class, a literal ``"`` or ``\\``, a control
    escape) also accepts any JSON escape. Assertions that cannot hold inside a JSON
    string (``\b``, ``\B``, ``^``, ``$``, ``\A``, ``\Z``) are dropped. Constructs
    ``rg`` cannot compile (lookarounds, backreferences) make the caller fall back to
    decoding every line.

    >>> raw_prefilter_pattern(r'foo\s+bar')
    'foo(?:\\\\(?:u[0-9a-fA-F]{4}|[^u])|\\s)+bar'
    >>> raw_prefilter_pattern(r'^\bac\b$')
    'ac'
    >>> raw_prefilter_pattern(r'ab\d+')
    'ab\\d+'
    """
    out: list[str] = []
    i, n = 0, len(pattern)

    def wrap(atom: str) -> str:
        return f"(?:{_JSON_ESCAPE}|{atom})"

    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            e = pattern[i + 1]
            if e in "bBAzZ":
                i += 2
                continue
            if e in "xu" or e == "N":  # \xhh, \uhhhh, \N{...}: copy then wrap
                j = i + 2
                if j < n and pattern[j] == "{":
                    j = pattern.index("}", j) + 1 if "}" in pattern[j:] else n
                else:
                    j += 2 if e == "x" else 4
                out.append(wrap(pattern[i:j]))
                i = j
                continue
            atom = pattern[i : i + 2]
            out.append(atom if e in _SAFE_ESCAPES else wrap(atom))
            i += 2
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            out.append(wrap(pattern[i : j + 1]))
            i = j + 1
        elif c in "^$":
            i += 1
        elif c == ".":
            out.append(wrap("."))
            i += 1
        elif c == '"' or ord(c) > 127:  # a quote, or non-ASCII a writer may \u-escape
            out.append(wrap(c))
            i += 1
        elif (
            c == "("
            and pattern.startswith("(?", i)
            and i + 2 < n
            and pattern[i + 2] in "aiLmsux-"
        ):
            j = pattern.index(")", i) if ")" in pattern[i:] else n - 1
            flags = pattern[i + 2 : j]
            if ":" in flags:  # (?i:...) scoped flags: keep the opener only
                k = pattern.index(":", i)
                out.append(pattern[i : k + 1])
                i = k + 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _rg_ok(rg: str, raw: str) -> bool:
    """Does ``rg`` compile ``raw``? (exit 2 = a regex it cannot handle)."""
    proc = subprocess.run(
        [rg, "--no-config", "-e", raw, "--", os.devnull],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode != 2


def _rg_lines(
    files: list[Path], pattern: str, *, ignore_case: bool
) -> Iterator[tuple[Path, list[str]]] | None:
    """The raw lines that could match, one file at a time, or ``None`` if ``rg`` can't.

    Streams ``rg``'s output (it prints each file's lines together), so memory holds
    one file's matching lines, not every match in every home.
    """
    rg = shutil.which("rg")
    if not rg:
        return None
    raw = raw_prefilter_pattern(pattern)
    if not _rg_ok(rg, raw):
        return None

    def gen() -> Iterator[tuple[Path, list[str]]]:
        for i in range(0, len(files), _RG_BATCH):
            batch = [str(f) for f in files[i : i + _RG_BATCH]]
            argv = [rg, "--no-config", "--null", "--no-line-number", "--no-heading"]
            argv += ["--no-messages", "--no-ignore", "--with-filename", "-j1"]
            argv += ["-e", raw] + (["-i"] if ignore_case else [])
            with subprocess.Popen(
                [*argv, "--", *batch],
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as proc:
                cur, lines = None, []
                for bline in proc.stdout:
                    path, _, rec = bline.partition(b"\0")
                    if path != cur:
                        if cur is not None:
                            yield Path(cur.decode()), lines
                        cur, lines = path, []
                    lines.append(rec.decode("utf-8", "replace").rstrip("\n"))
                if cur is not None:
                    yield Path(cur.decode()), lines

    return gen()


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
    streamed = (
        _rg_lines([sf.path for sf in files], pattern, ignore_case=ignore_case)
        if use_rg
        else None
    )
    by_path = {sf.path: sf for sf in files}
    work = (
        ((sf, None) for sf in files)
        if streamed is None
        else ((by_path[p], lines) for p, lines in streamed if p in by_path)
    )
    rows, n_scanned = [], 0
    for sf, lines in work:
        n_scanned += 1
        row = _session_hits(
            sf,
            rx,
            lines=lines,
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
        "n_scanned": n_scanned,
        "prefilter": "rg" if streamed is not None else "none",
        "n_sessions": len(rows),
        "sessions": rows[:k],
    }


def _split(x: str | Iterable[str]) -> list[str]:
    if isinstance(x, str):
        return [s.strip() for s in x.split(",") if s.strip()]
    return list(x)
