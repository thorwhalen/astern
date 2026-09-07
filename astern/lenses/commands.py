"""``rewrites``: one-off code an agent wrote inline, instead of reusing a function.

Three sources, one finding each (kind ``inline_script``):

- a **Bash heredoc** (``python3 - <<'EOF' ... EOF``, ``cat > file <<...``) — the body
  between the markers is the script.
- a **``Write``** whose path is under a scratchpad/tmp dir (``/scratchpad/`` or
  ``/tmp/`` in the path) — the file's content is the script.
- a **long Bash command** (over :data:`LONG_COMMAND_CHARS`) that is not a heredoc —
  still worth flagging as one-off shell logic.

Every finding carries a normalized ``fingerprint`` (:func:`fingerprint`) so
:mod:`astern.report` can group near-duplicate scripts across sessions — that
grouping is the whole point of the lens: a script rewritten in three different
sessions is a function nobody has extracted yet.

>>> from astern.turns import iter_turns
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "count the lines"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1", "model": "x",
...    "usage": {}, "content": [{"type": "tool_use", "id": "t1", "name": "Bash",
...    "input": {"command": "python3 - <<'EOF'\\nprint(open('f').read().count(chr(10)))\\nEOF"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user", "content":
...    [{"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "3"}]}},
... ]
>>> t = list(iter_turns(recs))
>>> out = rewrites({"session_id": "s"}, t)
>>> out[0]["kind"], out[0]["evidence"]["language"], len(out[0]["evidence"]["fingerprint"])
('inline_script', 'python', 12)
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterator

from astern.lenses import finding, lens

#: A Bash command longer than this is worth flagging as one-off shell logic even
#: without a heredoc.
LONG_COMMAND_CHARS = 400

#: File extension → language guess, checked before any content sniffing.
EXT_LANGUAGE = {
    ".py": "python", ".sh": "bash", ".js": "javascript", ".ts": "typescript",
    ".json": "json", ".md": "markdown", ".sql": "sql", ".yaml": "yaml", ".yml": "yaml",
}

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_DIGIT_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def heredoc_body(command: str) -> str | None:
    """The text between a heredoc's opening marker and its closing line, or ``None``.

    >>> heredoc_body("python3 - <<'EOF'\\nprint(1)\\nEOF")
    'print(1)'
    >>> heredoc_body("ls -la") is None
    True
    """
    m = _HEREDOC_RE.search(command)
    if not m:
        return None
    marker = m.group(2)
    rest = command[m.end():]
    if rest.startswith("\n"):
        rest = rest[1:]
    lines, body = rest.split("\n"), []
    for line in lines:
        if line.strip() == marker:
            return "\n".join(body).strip()
        body.append(line)
    return "\n".join(body).strip() or None


def normalize_script(text: str) -> str:
    """Lowercase, quoted strings and digits replaced by placeholders, whitespace collapsed.

    >>> normalize_script('print("hi") 42')
    'print(<str>) <num>'
    """
    text = _QUOTED_RE.sub("<str>", text)
    text = _DIGIT_RE.sub("<num>", text)
    return _WS_RE.sub(" ", text.lower()).strip()


def fingerprint(text: str) -> str:
    """A short hash of the normalized script, stable across trivial edits.

    >>> fingerprint('print(1)') == fingerprint('print(2)')
    True
    """
    return hashlib.sha1(normalize_script(text).encode("utf-8")).hexdigest()[:12]


#: Heredoc interpreter word → language, checked before content sniffing.
INTERPRETER_LANGUAGE = {"python": "python", "python3": "python", "python2": "python",
                        "node": "javascript", "bash": "bash", "sh": "bash", "zsh": "bash"}


def interpreter_hint(command: str) -> str:
    """The language implied by the word before a heredoc operator, or ``''``.

    >>> interpreter_hint("python3 - <<'EOF'")
    'python'
    >>> interpreter_hint("cat > f <<EOF")
    ''
    """
    prefix = command.split("<<", 1)[0]
    for tok in prefix.split():
        lang = INTERPRETER_LANGUAGE.get(tok.rsplit("/", 1)[-1])
        if lang:
            return lang
    return ""


def guess_language(text: str, *, path: str = "", hint: str = "") -> str:
    """Best-effort language guess: path extension, then an interpreter hint, then content.

    >>> guess_language('', path='/tmp/x.py')
    'python'
    >>> guess_language('print(1)', hint='python')
    'python'
    >>> guess_language('import os\\nprint(os.getcwd())')
    'python'
    """
    for ext, lang in EXT_LANGUAGE.items():
        if path.endswith(ext):
            return lang
    if hint:
        return hint
    head = text.lstrip()[:200]
    if "import " in head or "def " in head or head.startswith("#!/usr/bin/env python"):
        return "python"
    if re.search(r"\bfunction\b|\bconst \w+\s*=|=>", head):
        return "javascript"
    return "bash"


def _first_line(text: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _script_finding(session: dict, turn: dict, *, tool: str, script: str, path: str | None,
                    hint: str = "") -> dict:
    return finding("rewrites", session, kind="inline_script", turn=turn,
                   evidence={"tool": tool, "path": path, "first_line": _first_line(script),
                            "fingerprint": fingerprint(script), "chars": len(script),
                            "language": guess_language(script, path=path or "", hint=hint)})


def _bash_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("name") != "Bash":
            continue
        text = tc.get("input_text") or tc.get("digest") or ""
        if not text:
            continue
        body = heredoc_body(text)
        if body:
            yield _script_finding(session, turn, tool="Bash", script=body, path=None,
                                  hint=interpreter_hint(text))
        elif len(text) > LONG_COMMAND_CHARS:
            yield _script_finding(session, turn, tool="Bash", script=text, path=None)


def _write_findings(session: dict, turn: dict) -> Iterator[dict]:
    for tc in turn.get("tools") or []:
        if tc.get("name") != "Write":
            continue
        path = tc.get("digest") or ""
        if "/scratchpad/" not in path and "/tmp/" not in path:
            continue
        content = tc.get("input_text") or ""
        if not content:
            continue
        yield _script_finding(session, turn, tool="Write", script=content, path=path)


@lens("rewrites", version=1, kind="H")
def rewrites(session: dict, turns: list[dict], **ctx) -> list[dict]:
    """Inline scripts (Bash heredocs, long Bash commands, scratchpad Writes), fingerprinted."""
    out: list[dict] = []
    for t in turns:
        out.extend(_bash_findings(session, t))
        out.extend(_write_findings(session, t))
    return out
