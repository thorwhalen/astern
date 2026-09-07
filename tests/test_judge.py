"""Unit tests for astern.judge: command construction and loose vs strict parsing.

``claude_judge`` itself is never run here -- ``subprocess.run`` is monkeypatched so
the tests are fast, deterministic, and spend no tokens. What is under test is the
retry-avoiding contract: loose mode (the default) never builds ``--json-schema``
and never asks the CLI to retry; strict mode still does, exactly as before.
"""

from __future__ import annotations

import json
import subprocess

from astern.judge import Judgment, _command, claude_judge

SCHEMA = {"type": "object", "properties": {"goal": {"type": "string"}}}


def _fake_run(payload: dict):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    return run


# --- _command(): the argv builder, testable without running claude -----------


def test_command_omits_json_schema_by_default():
    cmd = _command(
        claude_bin="claude",
        model="haiku",
        effort=None,
        system=None,
        schema=SCHEMA,
        strict_schema=False,
    )
    assert "--json-schema" not in cmd
    assert cmd[-2:] == ["--model", "haiku"]


def test_command_includes_json_schema_only_when_strict():
    cmd = _command(
        claude_bin="claude",
        model="haiku",
        effort=None,
        system=None,
        schema=SCHEMA,
        strict_schema=True,
    )
    assert "--json-schema" in cmd
    i = cmd.index("--json-schema")
    assert json.loads(cmd[i + 1]) == SCHEMA


def test_command_never_adds_json_schema_when_no_schema_given():
    cmd = _command(
        claude_bin="claude",
        model="haiku",
        effort=None,
        system=None,
        schema=None,
        strict_schema=True,
    )
    assert "--json-schema" not in cmd


# --- claude_judge(): loose mode is the default and never retries -------------


def test_loose_mode_never_passes_json_schema_to_the_cli(monkeypatch):
    captured = {}

    def run(cmd, **kw):
        captured["cmd"] = cmd
        payload = {
            "result": json.dumps({"goal": "g"}),
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "num_turns": 1,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr("astern.judge.subprocess.run", run)
    j = claude_judge("hello", schema=SCHEMA, model="haiku")
    assert "--json-schema" not in captured["cmd"]
    assert j.data == {"goal": "g"}
    assert j.num_turns == 1


def test_loose_mode_embeds_the_schema_in_the_prompt(monkeypatch):
    captured = {}

    def run(cmd, *, input, **kw):
        captured["prompt"] = input
        payload = {
            "result": json.dumps({"goal": "g"}),
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "num_turns": 1,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr("astern.judge.subprocess.run", run)
    claude_judge("hello", schema=SCHEMA, model="haiku")
    assert "hello" in captured["prompt"]
    assert json.dumps(SCHEMA, separators=(",", ":")) in captured["prompt"]


def test_loose_mode_parses_a_fenced_preambled_answer(monkeypatch):
    fenced = (
        "Sure, here you go:\n```json\n" + json.dumps({"goal": "g"}) + "\n```\nThanks!"
    )
    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr(
        "astern.judge.subprocess.run",
        _fake_run(
            {
                "result": fenced,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "num_turns": 1,
            }
        ),
    )
    j = claude_judge("hello", schema=SCHEMA, model="haiku")
    assert j.data == {"goal": "g"} and j.error is None


def test_loose_mode_unparseable_answer_is_an_error_but_usage_is_recorded(monkeypatch):
    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr(
        "astern.judge.subprocess.run",
        _fake_run(
            {
                "result": "sorry, I can't help with that",
                "usage": {"input_tokens": 42, "output_tokens": 7},
                "num_turns": 1,
            }
        ),
    )
    j = claude_judge("hello", schema=SCHEMA, model="haiku")
    assert j.error == "unparseable JSON"
    assert j.data is None
    assert j.usage["input_tokens"] == 42  # the call happened and cost tokens either way


def test_strict_mode_still_passes_json_schema_and_uses_structured_output(monkeypatch):
    captured = {}

    def run(cmd, **kw):
        captured["cmd"] = cmd
        payload = {
            "result": json.dumps({"goal": "g"}),
            "structured_output": {"goal": "g"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "num_turns": 2,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr("astern.judge.subprocess.run", run)
    j = claude_judge("hello", schema=SCHEMA, model="haiku", strict_schema=True)
    assert "--json-schema" in captured["cmd"]
    assert j.data == {"goal": "g"}
    assert j.num_turns == 2  # the retry the strict CLI path can still take


def test_strict_mode_without_a_schema_never_adds_json_schema(monkeypatch):
    captured = {}

    def run(cmd, **kw):
        captured["cmd"] = cmd
        payload = {
            "result": "plain text",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: "/usr/bin/claude")
    monkeypatch.setattr("astern.judge.subprocess.run", run)
    j = claude_judge("hello", model="haiku", strict_schema=True)
    assert "--json-schema" not in captured["cmd"]
    assert j.data is None and j.error is None


def test_claude_not_found_is_reported_without_touching_subprocess(monkeypatch):
    monkeypatch.setattr("astern.judge.shutil.which", lambda _b: None)
    j = claude_judge("hello", schema=SCHEMA)
    assert isinstance(j, Judgment)
    assert "not found on PATH" in j.error
