"""The ``judge=`` seam: an LLM call that returns text or JSON *and says what it cost*.

The default judge is the local ``claude`` CLI run headless (``claude -p``). That is a
deliberate choice, not a shortcut: astern is used from Claude Code, by someone on a
Claude subscription, so the subscription is the budget and the CLI is the only
client that draws on it. ``--output-format json`` reports the usage of every call,
which is exactly the dependent variable the cost model in :mod:`astern.estimate`
needs. ``--no-session-persistence`` keeps the judge's own calls out of
``~/.claude/projects``, so the miner does not grow the corpus it mines.

A replacement is any callable with the same signature returning a :class:`Judgment`
— an ``aix.prompt_func`` for API billing, or a recorded-replay judge for tests.

>>> j = replay_judge({'hello': '{"answer": 1}'})
>>> j('hello', schema={'type': 'object'}).data
{'answer': 1}
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Mapping

DFLT_MODEL = "haiku"
DFLT_TIMEOUT_S = 900


@dataclass
class Judgment:
    """What one judge call returned and what it cost."""

    text: str
    data: dict | list | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    duration_ms: int | None = None
    model: str = ""
    prompt_chars: int = 0
    raw: dict | None = None
    error: str | None = None

    def as_record(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


Judge = Callable[..., Judgment]


def _parse_json_loose(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                return None
    return None


def claude_judge(prompt: str, *, schema: Mapping | None = None, model: str = DFLT_MODEL,
                 effort: str | None = None, system: str | None = None,
                 timeout_s: int = DFLT_TIMEOUT_S, claude_bin: str = "claude") -> Judgment:
    """Run ``claude -p`` headless on ``prompt`` and return a :class:`Judgment`.

    Tools are disabled and hooks skipped (``--bare``): the judge reads what it is
    given and answers; it never explores. ``schema`` requests structured output.
    """
    if shutil.which(claude_bin) is None:
        return Judgment(text="", error=f"{claude_bin!r} not found on PATH", model=model,
                        prompt_chars=len(prompt))
    cmd = [claude_bin, "-p", "--bare", "--no-session-persistence", "--tools", "",
           "--output-format", "json", "--model", model]
    if effort:
        cmd += ["--effort", effort]
    if system:
        cmd += ["--system-prompt", system]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(dict(schema))]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Judgment(text="", error=f"timeout after {timeout_s}s", model=model, prompt_chars=len(prompt))
    if proc.returncode != 0:
        return Judgment(text=proc.stdout, error=f"exit {proc.returncode}: {proc.stderr.strip()[:500]}",
                        model=model, prompt_chars=len(prompt))
    raw = _parse_json_loose(proc.stdout)
    if not isinstance(raw, dict):
        return Judgment(text=proc.stdout, error="unparseable claude output", model=model,
                        prompt_chars=len(prompt))
    text = raw.get("result") if isinstance(raw.get("result"), str) else json.dumps(raw.get("result"))
    data = raw.get("structured_output")
    if data is None and schema is not None:
        data = _parse_json_loose(text or "")
    return Judgment(
        text=text or "",
        data=data,
        usage=dict(raw.get("usage") or {}),
        cost_usd=raw.get("total_cost_usd"),
        duration_ms=raw.get("duration_api_ms") or raw.get("duration_ms"),
        model=str((raw.get("modelUsage") and next(iter(raw["modelUsage"]), "")) or model),
        prompt_chars=len(prompt),
        raw=raw,
    )


def replay_judge(answers: Mapping[str, str]) -> Judge:
    """A judge that answers from a mapping of prompt → text; for tests and dry runs."""

    def judge(prompt: str, *, schema=None, **_) -> Judgment:
        text = answers.get(prompt, "")
        return Judgment(text=text, data=_parse_json_loose(text) if schema is not None else None,
                        usage={"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4},
                        cost_usd=0.0, model="replay", prompt_chars=len(prompt))

    return judge
