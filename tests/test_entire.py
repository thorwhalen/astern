"""Tests for astern.entire: enabling Entire safely, and reading what is already true.

``subprocess.run`` is monkeypatched throughout so no real ``entire`` process ever
runs -- a call whose argv doesn't start with ``git`` is answered from a small canned
table (optionally with a file-writing side effect, to mirror what the real command
does) instead of executing anything. Git calls fall through to the real ``git``
binary against a throwaway repo under ``tmp_path``, because git itself is safe to
run and ``entire_status``/``entire_enable`` both lean on it directly.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from astern import entire

# --- the subprocess/shutil stub ------------------------------------------------


def _stub_run(monkeypatch, canned: dict):
    """Patch ``astern.entire.subprocess.run``: ``git`` passes through for real; every
    other command is answered from ``canned`` (a ``tuple(cmd) -> CompletedProcess`` or
    ``-> callable(cmd, **kw) -> CompletedProcess`` mapping, matched by prefix), and
    anything unlisted is refused loudly so a forgotten stub fails the test instead of
    silently no-oping.
    """
    real_run = subprocess.run
    calls = []

    def run(cmd, **kw):
        calls.append(list(cmd))
        if cmd and cmd[0] == "git":
            return real_run(cmd, **kw)
        for prefix, handler in canned.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return handler(cmd, **kw) if callable(handler) else handler
        raise AssertionError(f"unstubbed command: {cmd!r}")

    monkeypatch.setattr("astern.entire.subprocess.run", run)
    return calls


def _stub_which(monkeypatch, *, installed: bool = True):
    monkeypatch.setattr(
        "astern.entire.shutil.which",
        lambda name: ("/opt/homebrew/bin/entire" if installed and name == "entire" else None),
    )


def _init_repo(tmp_path, *, origin: str | None = None):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    if origin is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", origin], cwd=repo, check=True
        )
    return repo


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["entire"], 0, stdout, "")


# --- entire_status: read-only, no `entire` command needed ----------------------


def test_status_reports_disabled_when_no_hooks_are_present(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=False)
    repo = _init_repo(tmp_path)

    out = entire.entire_status(str(repo))

    assert out["entire_installed"] is False
    assert out["is_git_repo"] is True
    assert out["enabled"] is False
    assert out["hooks"] == []
    assert out["push_sessions"] is True  # entire's own default when unset
    assert out["telemetry"] is True
    assert out["n_local_checkpoints"] == 0
    assert out["risk"] is None


def test_status_reads_settings_when_hooks_and_settings_are_present(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path, origin="https://github.com/thorwhalen/astern.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"matcher": ""}],
                    "PostToolUse": [{"matcher": "Agent"}, {"matcher": "TaskCreate"}],
                }
            }
        )
    )
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text('#!/bin/sh\nentire hooks git pre-push "$1"\n')
    (repo / ".entire").mkdir()
    (repo / ".entire" / "settings.json").write_text(
        json.dumps({"enabled": True, "strategy_options": {"push_sessions": False}, "telemetry": False})
    )

    out = entire.entire_status(str(repo))

    assert out["entire_installed"] is True
    assert out["enabled"] is True
    assert out["hooks"] == ["PostToolUse/Agent", "PostToolUse/TaskCreate", "Stop/*"]
    assert out["push_sessions"] is False
    assert out["telemetry"] is False
    assert out["is_github_origin"] is True
    assert out["risk"] is None  # push_sessions is off, so no risk


def test_status_flags_the_risk_when_push_sessions_is_on_and_origin_is_github(
    tmp_path, monkeypatch
):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path, origin="git@github.com:thorwhalen/astern.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"matcher": ""}]}})
    )
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nentire hooks git pre-push\n")
    # No .entire/settings.json at all -> entire's own defaults apply (push_sessions True).

    out = entire.entire_status(str(repo))

    assert out["enabled"] is True
    assert out["push_sessions"] is True
    assert out["is_github_origin"] is True
    assert out["risk"] == "checkpoints will be pushed on next git push"


def test_status_on_a_non_github_origin_never_flags_risk(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path, origin="https://gitlab.example.com/x/y.git")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"matcher": ""}]}})
    )
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nentire hooks git pre-push\n")

    out = entire.entire_status(str(repo))

    assert out["is_github_origin"] is False
    assert out["risk"] is None


def test_status_is_not_a_git_repo(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=False)
    out = entire.entire_status(str(tmp_path / "not-a-repo"))
    assert out["is_git_repo"] is False
    assert out["enabled"] is False
    assert out["push_sessions"] is True
    assert out["telemetry"] is True


# --- entire_enable: missing binary / not a repo ---------------------------------


def test_enable_reports_missing_entire_with_the_install_command(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=False)
    out = entire.entire_enable(str(tmp_path))
    assert out["ok"] is False
    assert "PATH" in out["error"]
    assert out["install"] == "brew install --cask entireio/tap/entire"


def test_enable_rejects_a_non_git_directory(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    out = entire.entire_enable(str(tmp_path / "nope"))
    assert out["ok"] is False
    assert "not a git repository" in out["error"]


# --- entire_enable: dry_run never invokes `entire` ------------------------------


def test_enable_dry_run_never_calls_entire(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path)
    calls = _stub_run(monkeypatch, canned={})  # any `entire` call would raise

    out = entire.entire_enable(str(repo), dry_run=True)

    assert out["ok"] is True
    assert out["dry_run"] is True
    cmds = [c["cmd"] for c in out["commands"]]
    assert ["entire", "agent", "add", "claude-code"] in cmds
    assert ["entire", "configure", "--skip-push-sessions"] in cmds
    assert ["entire", "configure", "--telemetry=false"] in cmds
    assert all(c[0] == "git" for c in calls)  # only git ever actually ran


def test_enable_dry_run_warns_on_github_origin_with_push_sessions_true(
    tmp_path, monkeypatch
):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path, origin="https://github.com/thorwhalen/astern.git")
    _stub_run(monkeypatch, canned={})

    out = entire.entire_enable(str(repo), push_sessions=True, dry_run=True)

    assert out["warnings"] and "github.com" in out["warnings"][0]
    cmds = [c["cmd"] for c in out["commands"]]
    assert ["entire", "configure", "--skip-push-sessions"] not in cmds


# --- entire_enable: the real (mocked) run ---------------------------------------


def _mk_agent_add_side_effect(repo):
    def side_effect(cmd, **kw):
        claude_settings = repo / ".claude" / "settings.json"
        claude_settings.parent.mkdir(parents=True, exist_ok=True)
        claude_settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [{"matcher": "Agent"}],
                        "PostToolUse": [
                            {"matcher": "Agent"},
                            {"matcher": "TaskCreate|TaskUpdate"},
                        ],
                        "SessionStart": [{"matcher": ""}],
                        "SessionEnd": [{"matcher": ""}],
                        "Stop": [{"matcher": ""}],
                        "SubagentStop": [{"matcher": ""}],
                        "UserPromptSubmit": [{"matcher": ""}],
                    }
                }
            )
        )
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "pre-push").write_text('#!/bin/sh\nentire hooks git pre-push "$1"\n')
        (hooks_dir / "post-commit").write_text("#!/bin/sh\nentire hooks git post-commit\n")
        entire_dir = repo / ".entire"
        entire_dir.mkdir(exist_ok=True)
        (entire_dir / "settings.json").write_text(json.dumps({"enabled": True}))
        return _ok("Installed 8 hooks for Claude Code")

    return side_effect


def _mk_configure_side_effect(repo, patch: dict):
    def side_effect(cmd, **kw):
        path = repo / ".entire" / "settings.json"
        data = json.loads(path.read_text()) if path.is_file() else {}
        data.update(patch)
        path.write_text(json.dumps(data))
        return _ok("Settings updated")

    return side_effect


def test_enable_runs_agent_add_then_configure_and_reports_the_diff(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path)
    canned = {
        ("entire", "agent", "add", "claude-code"): _mk_agent_add_side_effect(repo),
        ("entire", "configure", "--skip-push-sessions"): _mk_configure_side_effect(
            repo, {"strategy_options": {"push_sessions": False}}
        ),
        ("entire", "configure", "--telemetry=false"): _mk_configure_side_effect(
            repo, {"telemetry": False}
        ),
    }
    _stub_run(monkeypatch, canned)

    out = entire.entire_enable(str(repo))

    assert out["ok"] is True
    assert len(out["commands"]) == 3
    assert all(c["ok"] for c in out["commands"])
    assert set(out["hooks_added"]) == {
        "PostToolUse/Agent",
        "PostToolUse/TaskCreate|TaskUpdate",
        "PreToolUse/Agent",
        "SessionEnd/*",
        "SessionStart/*",
        "Stop/*",
        "SubagentStop/*",
        "UserPromptSubmit/*",
    }
    assert "pre-push" in out["git_hooks_installed"]
    assert "post-commit" in out["git_hooks_installed"]
    assert out["settings"]["strategy_options"]["push_sessions"] is False
    assert out["settings"]["telemetry"] is False
    assert out["warnings"] == []


def test_enable_skips_configure_calls_that_are_opted_back_in(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path, origin="https://github.com/thorwhalen/astern.git")
    canned = {
        ("entire", "agent", "add", "claude-code"): _mk_agent_add_side_effect(repo),
    }
    _stub_run(monkeypatch, canned)

    out = entire.entire_enable(str(repo), push_sessions=True, telemetry=True)

    assert len(out["commands"]) == 1  # only agent add; no configure calls
    assert out["warnings"] and "github.com" in out["warnings"][0]


def test_enable_reports_failure_without_raising(tmp_path, monkeypatch):
    _stub_which(monkeypatch, installed=True)
    repo = _init_repo(tmp_path)
    failing = subprocess.CompletedProcess(["entire"], 1, "", "boom")
    canned = {("entire", "agent", "add", "claude-code"): failing}
    _stub_run(monkeypatch, canned)

    out = entire.entire_enable(str(repo), push_sessions=True, telemetry=True)

    assert out["ok"] is False
    assert out["commands"][0]["ok"] is False
    assert out["commands"][0]["stderr"] == "boom"
    assert any("failed" in w for w in out["warnings"])


# --- module doctest sanity (also exercised by --doctest-modules) ---------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@github.com:thorwhalen/astern.git", True),
        ("https://github.com/thorwhalen/astern.git", True),
        ("https://gitlab.com/x/y.git", False),
        (None, False),
    ],
)
def test_is_github_origin(url, expected):
    assert entire._is_github_origin(url) is expected
