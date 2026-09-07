"""From raw transcript records to one record per user→assistant turn, plus session meta.

The turn record is the unit every lens works on. Its shape is the one ``priv``'s
``claude_transcripts`` established and ``ir`` indexes, extended with what the mining
lenses need and the indexer did not: the tool calls of the turn (name, a short
digest of the input, whether the result was an error), the ``system`` records that
fell inside the turn (``turn_duration``, ``compact_boundary``), and API usage
de-duplicated by ``message.id`` (one API turn spans several ``assistant`` lines that
repeat the same ``usage``; summing per line overcounts by an order of magnitude).

>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "fix the bug"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1",
...    "model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5},
...    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user",
...    "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}]}},
...   {"type": "assistant", "uuid": "a2", "sessionId": "s", "message": {"id": "m2",
...    "model": "claude-x", "usage": {"input_tokens": 20, "output_tokens": 7},
...    "content": [{"type": "text", "text": "Fixed."}]}},
... ]
>>> t = list(iter_turns(recs))[0]
>>> t["index"], t["user_prompt"], t["assistant_summary"], t["n_errors"]
(0, 'fix the bug', 'Fixed.', 1)
>>> t["tools"][0]["name"], t["tools"][0]["is_error"], t["usage"]["input_tokens"]
('Bash', True, 30)
>>> t["tools"][0]["input_text"], t["tools"][0]["input_chars"]
('pytest', 6)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator, Mapping

#: XML-ish wrapper tags the CLI logs around non-prose user lines — slash-command
#: echoes, local-command stdout, injected reminders. Stripped from the *user prompt*
#: so it carries the human's words, not tooling noise.
WRAPPER_TAGS = (
    "command-name",
    "command-message",
    "command-args",
    "local-command-stdout",
    "local-command-stderr",
    "local-command-caveat",
    "bash-input",
    "bash-stdout",
    "bash-stderr",
    "system-reminder",
    "user-prompt-submit-hook",
    "task-notification",
)
_WRAPPER_PAIR_RE = re.compile(r"<(" + "|".join(WRAPPER_TAGS) + r")\b[^>]*>.*?</\1>", re.DOTALL)
_WRAPPER_TAG_RE = re.compile(r"</?(?:" + "|".join(WRAPPER_TAGS) + r")\b[^>]*>")

#: Which ``system`` subtypes are kept on the turn record (others are counted only).
KEPT_SYSTEM_SUBTYPES = ("turn_duration", "compact_boundary", "away_summary", "stop_hook_summary")

#: Session-level records that are worth keeping as metadata, keyed by their type.
META_TYPES = ("ai-title", "custom-title", "agent-name", "pr-link", "cost-state", "permission-mode",
              "worktree-state", "frame-link")

DIGEST_CHARS = 200

#: How much of a tool's full input text (a Bash command, a Write's content) to keep
#: verbatim on the turn record. Big enough for real scripts, capped so one pasted
#: blob can't blow up the store.
MAX_INPUT_TEXT_CHARS = 20000


def clean_prompt(text: str) -> str:
    """Strip CLI wrapper tags (and their content) from a user line → prose only.

    >>> clean_prompt('<system-reminder>x</system-reminder> hello  world')
    'hello world'
    """
    text = _WRAPPER_PAIR_RE.sub(" ", text)
    text = _WRAPPER_TAG_RE.sub(" ", text)
    return " ".join(text.split()).strip()


def _content(msg: Mapping) -> Any:
    return (msg.get("message") or {}).get("content")


def _text_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content] if content.strip() else []
    if isinstance(content, list):
        return [b["text"] for b in content
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    return []


def _blocks(msg: Mapping, kind: str) -> list[dict]:
    c = _content(msg)
    if not isinstance(c, list):
        return []
    return [b for b in c if isinstance(b, dict) and b.get("type") == kind]


def is_tool_result(msg: Mapping) -> bool:
    return bool(_blocks(msg, "tool_result"))


def is_user_prompt(msg: Mapping) -> bool:
    """A real human prompt: a ``user`` line that is neither meta nor a tool result."""
    if msg.get("type") != "user" or msg.get("isMeta"):
        return False
    if is_tool_result(msg):
        return False
    return bool(_text_blocks(_content(msg)))


def iter_turn_pairs(records: list[dict]) -> Iterator[tuple[dict, list[dict]]]:
    """Yield ``(user_prompt_record, [records of the turn])`` up to the next real prompt.

    The turn's records include assistant lines, the ``tool_result`` user lines that
    continue the same turn, and ``system`` lines that fell inside it.
    """
    i, n = 0, len(records)
    while i < n:
        msg = records[i]
        if not is_user_prompt(msg):
            i += 1
            continue
        turn: list[dict] = []
        j = i + 1
        while j < n:
            mj = records[j]
            t = mj.get("type")
            if t == "user" and not is_tool_result(mj) and not mj.get("isMeta"):
                if _text_blocks(_content(mj)):
                    break
            if t in ("assistant", "user", "system"):
                turn.append(mj)
            j += 1
        yield msg, turn
        i = j


def tool_digest(name: str, inp: Any) -> str:
    """A short, comparable summary of a tool input: the command, the path, or the query.

    >>> tool_digest('Bash', {'command': 'ls -la'})
    'ls -la'
    >>> tool_digest('Edit', {'file_path': '/a/b.py', 'old_string': 'x'})
    '/a/b.py'
    >>> tool_digest('SendMessage', {'to': 'a', 'message': 'hi'})
    '{"message": "hi", "to": "a"}'
    """
    if not isinstance(inp, dict):
        return str(inp)[:DIGEST_CHARS]
    for key in ("command", "file_path", "path", "query", "pattern", "prompt", "description", "skill"):
        if inp.get(key):
            return str(inp[key])[:DIGEST_CHARS]
    # No known key: digest the *values*, never just the key names — a key-only digest
    # made every distinct SendMessage call in a session collapse onto one string, which
    # the friction lens then read as a 200-fold retry.
    return json.dumps(inp, sort_keys=True, default=str)[:DIGEST_CHARS]


def tool_input_text(name: str, inp: Any) -> str:
    """The full text of a tool input worth keeping verbatim: a command, a file body.

    Unlike :func:`tool_digest` (a short label for display) this is uncapped source
    text — a rewrites lens needs the whole script, not its first 200 chars.

    >>> tool_input_text('Bash', {'command': 'ls -la'})
    'ls -la'
    >>> tool_input_text('Write', {'file_path': '/tmp/x.py', 'content': 'print(1)'})
    'print(1)'
    """
    if not isinstance(inp, dict):
        return str(inp)
    for key in ("command", "content", "new_string", "query", "pattern", "prompt", "description"):
        if inp.get(key):
            return str(inp[key])
    return tool_digest(name, inp)


def _tools(turn: list[dict]) -> list[dict]:
    uses: dict[str, dict] = {}
    order: list[str] = []
    for m in turn:
        if m.get("type") == "assistant":
            for b in _blocks(m, "tool_use"):
                tid = b.get("id") or f"anon{len(order)}"
                name, inp = b.get("name", ""), b.get("input")
                full_text = tool_input_text(name, inp)
                uses[tid] = {"id": tid, "name": name, "digest": tool_digest(name, inp),
                             "is_error": False, "result_chars": 0,
                             "input_text": full_text[:MAX_INPUT_TEXT_CHARS], "input_chars": len(full_text)}
                order.append(tid)
        elif m.get("type") == "user":
            for b in _blocks(m, "tool_result"):
                tid = b.get("tool_use_id")
                if tid in uses:
                    uses[tid]["is_error"] = bool(b.get("is_error"))
                    uses[tid]["result_chars"] = len(str(b.get("content", "")))
    return [uses[t] for t in order]


def _usage(turn: list[dict]) -> dict:
    """API usage for the turn, de-duplicated by ``message.id``."""
    seen: dict[str, dict] = {}
    for m in turn:
        if m.get("type") != "assistant":
            continue
        msg = m.get("message") or {}
        u = msg.get("usage")
        if isinstance(u, dict):
            seen[msg.get("id") or m.get("uuid") or str(len(seen))] = u
    tot: dict[str, int] = {}
    for u in seen.values():
        for k, v in u.items():
            if isinstance(v, (int, float)):
                tot[k] = tot.get(k, 0) + int(v)
    tot["api_calls"] = len(seen)
    return tot


def _system(turn: list[dict]) -> list[dict]:
    out = []
    for m in turn:
        if m.get("type") == "system" and m.get("subtype") in KEPT_SYSTEM_SUBTYPES:
            keep = {k: v for k, v in m.items()
                    if k in ("subtype", "durationMs", "content", "compactMetadata", "timestamp")}
            out.append(keep)
    return out


def _models(turn: list[dict]) -> list[str]:
    seen: list[str] = []
    for m in turn:
        if m.get("type") == "assistant":
            model = (m.get("message") or {}).get("model")
            if model and model not in seen:
                seen.append(model)
    return seen


def iter_turns(records: list[dict]) -> Iterator[dict]:
    """Yield one JSON-able record per turn. See the module docstring for the shape."""
    for index, (user_msg, turn) in enumerate(iter_turn_pairs(records)):
        asst_texts = [t.strip() for m in turn if m.get("type") == "assistant"
                      for t in _text_blocks(_content(m)) if t.strip()]
        tools = _tools(turn)
        yield {
            "index": index,
            "uuid": user_msg.get("uuid") or "",
            "timestamp": user_msg.get("timestamp") or "",
            "user_prompt": clean_prompt(" ".join(_text_blocks(_content(user_msg)))),
            "user_prompt_chars_raw": sum(len(t) for t in _text_blocks(_content(user_msg))),
            "assistant_summary": asst_texts[-1] if asst_texts else "",
            "assistant_full": "\n\n".join(asst_texts),
            "assistant_chars": sum(len(t) for t in asst_texts),
            "tools": tools,
            "n_tool_calls": len(tools),
            "n_errors": sum(1 for t in tools if t["is_error"]),
            "tool_result_chars": sum(t["result_chars"] for t in tools),
            "system": _system(turn),
            "models": _models(turn),
            "usage": _usage(turn),
            "is_sidechain": bool(user_msg.get("isSidechain")),
            "git_branch": user_msg.get("gitBranch") or "",
        }


def session_meta(records: list[dict]) -> dict:
    """Session-level facts from the non-conversational records (last occurrence wins).

    >>> session_meta([{"type": "ai-title", "aiTitle": "Fix CI"}, {"type": "pr-link",
    ...   "prNumber": 3, "prUrl": "u", "prRepository": "o/r"}, {"type": "user", "sessionId": "s",
    ...   "cwd": "/p/x", "timestamp": "t", "version": "2.1", "message": {"content": "hi"}}])["title"]
    'Fix CI'
    """
    meta: dict = {"session_id": "", "cwd": "", "project": "", "git_branch": "", "started_at": "",
                  "ended_at": "", "version": "", "title": "", "ai_title": "", "custom_title": "",
                  "agent_name": "", "prs": [], "cost_state": None, "permission_mode": "",
                  "n_records": len(records), "record_types": {}}
    for m in records:
        t = m.get("type")
        meta["record_types"][t] = meta["record_types"].get(t, 0) + 1
        if m.get("sessionId") and m.get("cwd") and not meta["session_id"]:
            meta["session_id"] = m["sessionId"]
            meta["cwd"] = m["cwd"]
            meta["project"] = Path(m["cwd"]).name
            meta["git_branch"] = m.get("gitBranch") or ""
            meta["started_at"] = m.get("timestamp") or ""
            meta["version"] = m.get("version") or ""
        if m.get("timestamp"):
            meta["ended_at"] = m["timestamp"]
        if t == "ai-title" and m.get("aiTitle"):
            meta["ai_title"] = str(m["aiTitle"])
        elif t == "custom-title" and m.get("customTitle"):
            meta["custom_title"] = str(m["customTitle"])
        elif t == "agent-name" and m.get("agentName"):
            meta["agent_name"] = str(m["agentName"])
        elif t == "pr-link":
            pr = {"number": m.get("prNumber"), "url": m.get("prUrl"), "repo": m.get("prRepository")}
            if pr not in meta["prs"]:
                meta["prs"].append(pr)
        elif t == "cost-state":
            meta["cost_state"] = {k: v for k, v in m.items() if k not in ("type", "uuid", "sessionId")}
        elif t == "permission-mode" and m.get("permissionMode"):
            meta["permission_mode"] = m["permissionMode"]
    meta["title"] = (meta["custom_title"] or meta["ai_title"]).strip()
    return meta
