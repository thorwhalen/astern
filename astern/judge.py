"""The ``judge=`` seam: an LLM call that returns text or JSON *and says what it cost*.

The default judge is the local ``claude`` CLI run headless (``claude -p``). That is a
deliberate choice, not a shortcut: astern is used from Claude Code, by someone on a
Claude subscription, so the subscription is the budget and the CLI is the only
client that draws on it. ``--output-format json`` reports the usage of every call,
which is exactly the dependent variable the cost model in :mod:`astern.estimate`
needs. ``--no-session-persistence`` keeps the judge's own calls out of
``~/.claude/projects`` (verified by counting transcripts before and after), so the
miner does not grow the corpus it mines.

Four things about the flags, each measured against the real CLI on 2026-09-07 and
each load-bearing:

- **Not** ``--bare``. Its own help says Anthropic auth is *strictly*
  ``ANTHROPIC_API_KEY`` or an ``apiKeyHelper`` — "OAuth and keychain are never
  read" — so on a subscription every ``--bare`` call comes back
  ``"Not logged in · Please run /login"``. :data:`SANDBOX_FLAGS` uses
  ``--safe-mode`` instead, which disables the same customizations (CLAUDE.md,
  skills, hooks, plugins, MCP) and leaves authentication alone.
- ``--tools ""`` really does empty the tool set: same prompt, same system prompt,
  1,109 input tokens with it against 14,925 without. (Asking the model what tools
  it has is not a test — it confidently lists six it does not have.)
- A failed call **exits 0**. The "not logged in" answer above came back on
  ``returncode == 0`` with ``is_error: true`` in the JSON, so the return code alone
  is not a success check.
- ``--json-schema`` is honoured and the parsed object lands under
  ``structured_output``; ``result`` still holds the text. Passing our own
  ``--system-prompt`` also removes the ~4k-token default preamble from every call
  and moves the whole input into ``input_tokens`` (nothing is prompt-cached), which
  is what makes the regression in :mod:`astern.estimate` a straight line.

A replacement is any callable with the same signature returning a :class:`Judgment`
— an ``aix.prompt_func`` for API billing, or a recorded-replay judge for tests.

>>> j = replay_judge({'hello': '{"answer": 1}'})
>>> r = j('hello', schema={'type': 'object'})
>>> r.data, r.usage['input_tokens'] > 0, r.error
({'answer': 1}, True, None)
>>> total_tokens(r.usage) == r.usage['input_tokens'] + r.usage['output_tokens']
True
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field

DFLT_MODEL = "haiku"
DFLT_TIMEOUT_S = 900

#: Flags that make one headless call a stateless, tool-less, corpus-neutral judge.
#: ``--safe-mode`` (not ``--bare``: see the module docstring) drops CLAUDE.md, skills,
#: hooks, plugins and MCP; ``--tools ""`` empties the tool set; the persistence flag
#: keeps the call out of ``~/.claude/projects``.
SANDBOX_FLAGS = ("--safe-mode", "--no-session-persistence", "--tools", "")

#: The usage keys that are billed as input. ``input_tokens`` alone under-reports by
#: an order of magnitude whenever the default system prompt is cached.
INPUT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


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
    #: How many API round trips the CLI made for this one call. It is normally 1, but a
    #: structured answer the schema rejects is retried with the whole conversation
    #: resent, so this is the multiplier on the input the view actually explains — the
    #: single largest source of variance in the cost model, and invisible without it.
    num_turns: int = 1
    raw: dict | None = None
    error: str | None = None

    def as_record(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        d["input_tokens"] = input_tokens(self.usage)
        d["output_tokens"] = int(self.usage.get("output_tokens") or 0)
        d["total_tokens"] = total_tokens(self.usage)
        return d


Judge = Callable[..., Judgment]


def input_tokens(usage: Mapping) -> int:
    """Billed input of one call: fresh input plus both halves of the cache.

    >>> input_tokens({'input_tokens': 9, 'cache_creation_input_tokens': 4241})
    4250
    """
    return sum(int(usage.get(k) or 0) for k in INPUT_KEYS)


def total_tokens(usage: Mapping) -> int:
    """Input (all three flavours) plus output.

    >>> total_tokens({'input_tokens': 10, 'output_tokens': 5})
    15
    """
    return input_tokens(usage) + int(usage.get("output_tokens") or 0)


def _usage_record(usage: Mapping) -> dict:
    """Keep the scalar usage fields (cache ones included); drop the nested detail.

    The CLI nests ``iterations``, ``cache_creation`` and ``server_tool_use`` inside
    ``usage``; a cost model regresses on numbers, and a store keyed by session should
    not carry a per-request log.

    >>> sorted(_usage_record({'input_tokens': 1, 'iterations': [{'x': 2}], 'speed': 's'}))
    ['cache_creation_input_tokens', 'cache_read_input_tokens', 'input_tokens', 'n_iterations', 'output_tokens']
    """
    out = {k: int(usage.get(k) or 0) for k in INPUT_KEYS}
    out["output_tokens"] = int(usage.get("output_tokens") or 0)
    for k, v in usage.items():
        if isinstance(v, (int, float)) and k not in out:
            out[k] = v
    iters = usage.get("iterations")
    if isinstance(iters, list):
        out["n_iterations"] = len(iters)
    details = usage.get("output_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("thinking_tokens"), (int, float)):
        out["thinking_tokens"] = int(details["thinking_tokens"])
    return out


def _parse_json_loose(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json")
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


def _model_name(raw: Mapping, fallback: str) -> str:
    """The model the CLI actually used, from ``modelUsage``'s single key."""
    mu = raw.get("modelUsage")
    if isinstance(mu, Mapping) and mu:
        return str(next(iter(mu)))
    return fallback


def claude_judge(prompt: str, *, schema: Mapping | None = None, model: str = DFLT_MODEL,
                 effort: str | None = None, system: str | None = None,
                 timeout_s: int = DFLT_TIMEOUT_S, claude_bin: str = "claude") -> Judgment:
    """Run ``claude -p`` headless on ``prompt`` and return a :class:`Judgment`.

    Tools are disabled and customizations skipped (:data:`SANDBOX_FLAGS`): the judge
    reads what it is given and answers; it never explores. ``schema`` requests
    structured output, which comes back under ``structured_output``.
    """
    if shutil.which(claude_bin) is None:
        return Judgment(text="", error=f"{claude_bin!r} not found on PATH", model=model,
                        prompt_chars=len(prompt))
    cmd = [claude_bin, "-p", *SANDBOX_FLAGS, "--output-format", "json", "--model", model]
    if effort:
        cmd += ["--effort", effort]
    if system:
        cmd += ["--system-prompt", system]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(dict(schema))]
    try:
        # check=False: a failed judge is data (it becomes the `error` below), never an
        # exception that takes the batch down with it.
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return Judgment(text="", error=f"timeout after {timeout_s}s", model=model,
                        prompt_chars=len(prompt))
    raw = _parse_json_loose(proc.stdout)
    if not isinstance(raw, dict):
        detail = (proc.stderr or proc.stdout).strip()[:500]
        return Judgment(text=proc.stdout, model=model, prompt_chars=len(prompt),
                        error=f"exit {proc.returncode}: {detail}" if proc.returncode
                        else "unparseable claude output")
    usage = _usage_record(raw.get("usage") or {})
    text = raw["result"] if isinstance(raw.get("result"), str) else json.dumps(raw.get("result"))
    # The CLI exits 0 on an errored turn (an unauthenticated call returns
    # `is_error: true` and returncode 0), so the flag in the payload is the check.
    error = None
    if raw.get("is_error") or proc.returncode != 0:
        error = f"{raw.get('subtype') or 'error'}: {(text or '').strip()[:300]}"
    data = raw.get("structured_output")
    if data is None and schema is not None and not error:
        data = _parse_json_loose(text or "")
    return Judgment(
        text=text or "",
        data=data,
        usage=usage,
        cost_usd=raw.get("total_cost_usd"),
        duration_ms=raw.get("duration_ms") or raw.get("duration_api_ms"),
        model=_model_name(raw, model),
        prompt_chars=len(prompt),
        num_turns=int(raw.get("num_turns") or len((raw.get("usage") or {}).get("iterations") or [1])),
        raw=raw,
        error=error,
    )


def replay_judge(answers: Mapping[str, str], *, default: str | None = None) -> Judge:
    """A judge that answers from a mapping of prompt → text; for tests and dry runs.

    A prompt with no recorded answer is an *error* judgment unless ``default`` is
    given — silently answering ``""`` would let a test mistake a missing recording
    for a real (empty) verdict, which is the one thing a replay judge must not do.

    >>> replay_judge({})('unseen').error
    'no recorded answer'
    >>> replay_judge({}, default='{}')('unseen').error is None
    True
    """

    def judge(prompt: str, *, schema=None, **_) -> Judgment:
        text = answers.get(prompt, default)
        if text is None:
            return Judgment(text="", model="replay", prompt_chars=len(prompt),
                            error="no recorded answer")
        return Judgment(text=text, data=_parse_json_loose(text) if schema is not None else None,
                        usage={"input_tokens": max(1, len(prompt) // 4),
                               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                               "output_tokens": max(1, len(text) // 4)},
                        cost_usd=0.0, duration_ms=0, model="replay", prompt_chars=len(prompt))

    return judge
