"""Synthetic Claude Code transcript builder for tests.

Never copies a real transcript: everything here is generated from small, secret-free
specs. :func:`mk_records` builds the raw JSONL-style dicts one session file holds
(mirroring what :mod:`astern.sources` / :mod:`astern.turns` actually parse);
:func:`write_home` lays those out on disk as a fake ``~/.claude``-shaped directory
tree, including one nested ``<sid>/subagents/agent-x.jsonl`` transcript.

>>> recs = mk_records('s1', '/tmp/proj', turns=[{'prompt': 'hi', 'final_text': 'hello'}])
>>> [r['type'] for r in recs]
['user', 'assistant']
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _slug(cwd: str) -> str:
    """A project-dir-name-shaped slug for a ``cwd``, mirroring Claude Code's own scheme."""
    return "-" + cwd.strip("/").replace("/", "-")


def _counter():
    return itertools.count(1)


def mk_records(
    sid: str,
    cwd: str,
    *,
    turns: list[Mapping[str, Any]] = (),
    meta: list[Mapping[str, Any]] = (),
    version: str = "1.2.3",
    git_branch: str = "main",
) -> list[dict]:
    """Build the raw records of one transcript from a small per-turn spec list.

    Each item of ``turns`` is a dict describing one turn (or, with ``kind='meta_user'``,
    a non-turn filler line such as a queued/injected prompt marked ``isMeta``):

    - ``prompt`` (str): the human's text. ``prompt_blocks=True`` wraps it as a
      content-block list instead of a bare string; ``wrap_reminder=True`` wraps it in
      ``<system-reminder>...</system-reminder>`` the way the CLI injects context.
    - ``tool_uses`` (list of ``{"name", "input", "is_error", "result"}``): tool calls
      the assistant makes, each paired with a matching ``tool_result`` user line.
    - ``repeat_usage`` (bool): emit a second ``assistant`` line sharing the same
      ``message.id`` and ``usage`` — the real-world shape that must be de-duplicated.
    - ``duration_ms`` / ``compact_boundary``: add a ``system`` record of that subtype.
    - ``final_text``: the turn-ending assistant prose (defaults to ``"done"``; pass
      ``None`` to omit it).
    - ``model``, ``usage``, ``message_id``, ``final_usage``, ``final_message_id``,
      ``is_sidechain``: passed through to the underlying records.

    ``meta`` is a list of raw session-level records (``ai-title``, ``pr-link``, ...,
    see the factory helpers below) appended after the transcript body.
    """
    records: list[dict] = []
    n = _counter()

    def ts() -> str:
        return (_BASE + timedelta(seconds=next(n))).isoformat().replace("+00:00", "Z")

    def rid(prefix: str) -> str:
        return f"{sid}-{prefix}{next(n)}"

    for spec in turns:
        if spec.get("kind") == "meta_user":
            content = spec.get("prompt", "queued")
            if spec.get("wrap_reminder"):
                content = f"<system-reminder>{content}</system-reminder>"
            if spec.get("prompt_blocks"):
                content = [{"type": "text", "text": content}]
            records.append(
                {
                    "type": "user",
                    "uuid": rid("u"),
                    "sessionId": sid,
                    "cwd": cwd,
                    "timestamp": ts(),
                    "version": version,
                    "gitBranch": git_branch,
                    "isMeta": True,
                    "message": {"role": "user", "content": content},
                }
            )
            continue

        prompt = spec.get("prompt", "hello")
        if spec.get("wrap_reminder"):
            prompt = f"<system-reminder>context</system-reminder> {prompt}"
        content = [{"type": "text", "text": prompt}] if spec.get("prompt_blocks") else prompt
        records.append(
            {
                "type": "user",
                "uuid": rid("u"),
                "sessionId": sid,
                "cwd": cwd,
                "timestamp": ts(),
                "version": version,
                "gitBranch": git_branch,
                "isMeta": False,
                "isSidechain": bool(spec.get("is_sidechain", False)),
                "message": {"role": "user", "content": content},
            }
        )

        tool_uses = list(spec.get("tool_uses") or [])
        tool_ids = [rid("t") for _ in tool_uses]
        model = spec.get("model", "claude-haiku")
        message_id = spec.get("message_id") or rid("m")
        usage = dict(spec.get("usage", {"input_tokens": 10, "output_tokens": 5}))

        if tool_uses:
            blocks = [
                {"type": "tool_use", "id": tid, "name": tu["name"], "input": tu.get("input", {})}
                for tid, tu in zip(tool_ids, tool_uses)
            ]
            records.append(
                {
                    "type": "assistant",
                    "uuid": rid("a"),
                    "sessionId": sid,
                    "timestamp": ts(),
                    "message": {"id": message_id, "model": model, "usage": usage, "content": blocks},
                }
            )
            if spec.get("repeat_usage"):
                records.append(
                    {
                        "type": "assistant",
                        "uuid": rid("a"),
                        "sessionId": sid,
                        "timestamp": ts(),
                        "message": {"id": message_id, "model": model, "usage": usage, "content": []},
                    }
                )
            for tid, tu in zip(tool_ids, tool_uses):
                records.append(
                    {
                        "type": "user",
                        "uuid": rid("u"),
                        "sessionId": sid,
                        "timestamp": ts(),
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tid,
                                    "is_error": bool(tu.get("is_error", False)),
                                    "content": tu.get("result", "ok"),
                                }
                            ],
                        },
                    }
                )

        if spec.get("duration_ms") is not None:
            records.append(
                {
                    "type": "system",
                    "subtype": "turn_duration",
                    "sessionId": sid,
                    "timestamp": ts(),
                    "durationMs": spec["duration_ms"],
                }
            )
        if spec.get("compact_boundary") is not None:
            records.append(
                {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "sessionId": sid,
                    "timestamp": ts(),
                    "compactMetadata": spec["compact_boundary"],
                }
            )

        final_text = spec.get("final_text", "done")
        if final_text is not None:
            records.append(
                {
                    "type": "assistant",
                    "uuid": rid("a"),
                    "sessionId": sid,
                    "timestamp": ts(),
                    "message": {
                        "id": spec.get("final_message_id") or rid("m"),
                        "model": model,
                        "usage": spec.get("final_usage", usage),
                        "content": [{"type": "text", "text": final_text}],
                    },
                }
            )

    records.extend(meta)
    return records


def _write_jsonl(path: Path, records: list[dict], *, extra_lines: list[str] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)
        f.writelines(line + "\n" for line in extra_lines)


def append_records(
    path: Path,
    sid: str,
    cwd: str,
    *,
    turns: list[Mapping[str, Any]],
    meta: list[Mapping[str, Any]] = (),
    version: str = "1.2.3",
    git_branch: str = "main",
    mtime: float | None = None,
) -> list[dict]:
    """Append more turns to an existing transcript file, then re-stamp its mtime.

    Used to simulate a resumed session growing between two ``sync`` runs. When
    ``mtime`` is given it is set explicitly (a test-determinism aid: the ledger keys
    on size *and* mtime, and appending within the same wall-clock second could
    otherwise leave the fingerprint unchanged on a coarse filesystem clock).
    """
    new_records = mk_records(sid, cwd, turns=turns, meta=meta, version=version, git_branch=git_branch)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in new_records)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    else:
        os.utime(path, None)
    return new_records


@dataclass
class WrittenHome:
    """What :func:`write_home` produced: the home dir and each session's file path."""

    home: Path
    paths: dict[str, Path] = field(default_factory=dict)
    slugs: dict[str, str] = field(default_factory=dict)


def write_home(tmp_path: Path, *, sessions: list[Mapping[str, Any]] = (), name: str = ".claude") -> WrittenHome:
    """Create a fake ``~/.claude``-shaped directory under ``tmp_path`` from session specs.

    Each item of ``sessions`` is a dict: ``sid``, ``cwd`` (required), ``turns``,
    ``meta``, ``slug`` (default: derived from ``cwd``), ``version``, ``git_branch``,
    ``mtime`` (explicit ``os.utime`` stamp), ``extra_lines`` (raw text lines appended
    verbatim — blank or malformed, for parser-tolerance tests), ``with_subagent``
    (bool: also write ``<sid>/subagents/agent-x.jsonl``) and ``subagent_turns``.
    """
    home = tmp_path / name
    (home / "projects").mkdir(parents=True, exist_ok=True)
    out = WrittenHome(home=home)
    for spec in sessions:
        sid = spec["sid"]
        cwd = spec["cwd"]
        slug = spec.get("slug") or _slug(cwd)
        version = spec.get("version", "1.2.3")
        git_branch = spec.get("git_branch", "main")
        proj_dir = home / "projects" / slug
        records = mk_records(
            sid, cwd, turns=spec.get("turns", ()), meta=spec.get("meta", ()),
            version=version, git_branch=git_branch,
        )
        path = proj_dir / f"{sid}.jsonl"
        _write_jsonl(path, records, extra_lines=spec.get("extra_lines", ()))
        if spec.get("mtime") is not None:
            os.utime(path, (spec["mtime"], spec["mtime"]))
        out.paths[sid] = path
        out.slugs[sid] = slug
        if spec.get("with_subagent"):
            sub_sid = "agent-x"
            sub_path = proj_dir / sid / "subagents" / f"{sub_sid}.jsonl"
            sub_records = mk_records(
                sub_sid, cwd, turns=spec.get("subagent_turns", spec.get("turns", ())),
                version=version, git_branch=git_branch,
            )
            _write_jsonl(sub_path, sub_records)
    return out


def ai_title(text: str) -> dict:
    return {"type": "ai-title", "aiTitle": text}


def custom_title(text: str) -> dict:
    return {"type": "custom-title", "customTitle": text}


def agent_name(name: str) -> dict:
    return {"type": "agent-name", "agentName": name}


def pr_link(number: int, url: str, repo: str) -> dict:
    return {"type": "pr-link", "prNumber": number, "prUrl": url, "prRepository": repo}


def cost_state(**kw) -> dict:
    return {"type": "cost-state", **kw}


def permission_mode(mode: str) -> dict:
    return {"type": "permission-mode", "permissionMode": mode}
