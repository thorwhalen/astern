"""Enable and inspect the `Entire CLI <https://github.com/entireio/cli>`_ safely, per repo.

``entire agent add claude-code`` is one command, but two of its defaults are unsafe
for a public repo: it installs a ``pre-push`` git hook that, by default, pushes
``refs/entire/checkpoints/*`` — verbatim Claude Code transcripts, absolute paths
unscrubbed — to origin on every ordinary ``git push``, and it opts into PostHog
telemetry. Neither shows up in ``git config --get-all remote.origin.push`` or
``git push --dry-run``: the push happens from a git hook, not a refspec, so the usual
way of auditing what a push sends says nothing about it.

This module exists to make enabling Entire safe *by default*: :func:`entire_enable`
runs the same ``entire agent add claude-code`` the docs tell you to run, then turns
both defaults off unless asked otherwise, and reports exactly what changed.
:func:`entire_status` answers what is already true of a repo — without invoking
``entire`` itself — so it works offline and never mutates anything.

Measured against Entire CLI 0.10.5 (2026-09-07, in a throwaway clone, never a real
repo — see ``tests/test_entire.py``):

- ``entire agent add claude-code`` writes 8 hook entries into ``.claude/settings.json``
  (``PreToolUse``, two under ``PostToolUse``, ``SessionStart``, ``SessionEnd``,
  ``Stop``, ``SubagentStop``, ``UserPromptSubmit``) and five git hooks
  (``commit-msg``, ``post-commit``, ``post-rewrite``, ``pre-push``,
  ``prepare-commit-msg``) — the ``pre-push`` one runs
  ``entire hooks git pre-push "$1"`` unconditionally whenever ``entire`` is on PATH.
- ``entire configure --skip-push-sessions`` writes
  ``strategy_options.push_sessions: false`` into ``.entire/settings.json``.
- ``entire configure --telemetry=false`` writes ``telemetry: false`` into the same
  file. (``--telemetry`` is a bool flag; ``entire configure --help`` documents both
  defaulting to enabled/true when unset.)

>>> _hook_slugs({'hooks': {'Stop': [{'matcher': ''}], 'PostToolUse': [{'matcher': 'Agent'}, {'matcher': 'TaskCreate'}]}})
['PostToolUse/Agent', 'PostToolUse/TaskCreate', 'Stop/*']
>>> _is_github_origin('git@github.com:thorwhalen/astern.git')
True
>>> _is_github_origin('https://gitlab.com/x/y.git')
False
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

__all__ = ["entire_enable", "entire_status"]

#: Where to point someone whose PATH doesn't have `entire` yet.
INSTALL_CMD = "brew install --cask entireio/tap/entire"

#: entire's own defaults (both true) when the key is absent from `.entire/settings.json`.
DFLT_PUSH_SESSIONS = True
DFLT_TELEMETRY = True


def _run(cmd: list[str], *, cwd: Path, timeout: float = 30.0):
    """One subprocess call; ``None`` (never a raised exception) if it could not run."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _cmd_result(cmd: list[str], res) -> dict:
    if res is None:
        return {
            "cmd": cmd,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "failed to execute",
        }
    return {
        "cmd": cmd,
        "ok": res.returncode == 0,
        "returncode": res.returncode,
        "stdout": (res.stdout or "").strip(),
        "stderr": (res.stderr or "").strip(),
    }


def is_git_repo(repo: str | Path) -> bool:
    """Is ``repo`` inside a git work tree?"""
    res = _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=Path(repo).expanduser())
    return bool(res) and res.returncode == 0 and res.stdout.strip() == "true"


def origin_url(repo: str | Path) -> str | None:
    """``git config --get remote.origin.url``, or ``None`` when there is no origin."""
    res = _run(
        ["git", "config", "--get", "remote.origin.url"], cwd=Path(repo).expanduser()
    )
    if not res or res.returncode != 0:
        return None
    url = res.stdout.strip()
    return url or None


def _is_github_origin(url: str | None) -> bool:
    """Does ``url`` name a github.com remote (https or ssh form)?

    >>> _is_github_origin('https://github.com/thorwhalen/astern.git')
    True
    >>> _is_github_origin(None)
    False
    """
    if not url:
        return False
    return "github.com" in url


def _claude_settings_path(repo: Path) -> Path:
    return repo / ".claude" / "settings.json"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _hook_slugs(settings: dict) -> list[str]:
    """``{event}/{matcher or '*'}`` for every hook entry, sorted — the diffable unit.

    A matcher-less event (``Stop``, ``SessionStart``, ...) reads ``*``; an event with
    several matchers (``PostToolUse`` here) contributes one slug per matcher, which is
    how 7 event *names* becomes the 8 hook entries ``entire agent add`` actually writes.
    """
    hooks = settings.get("hooks") or {}
    out = []
    for event, entries in hooks.items():
        for entry in entries or ():
            matcher = (entry or {}).get("matcher") or "*"
            out.append(f"{event}/{matcher}")
    return sorted(out)


def claude_hooks(repo: str | Path) -> list[str]:
    """The hook slugs (see :func:`_hook_slugs`) currently in ``repo``'s ``.claude/settings.json``."""
    return _hook_slugs(_read_json(_claude_settings_path(Path(repo).expanduser())))


def _git_hook_paths(repo: Path) -> dict[str, Path]:
    hooks_dir = repo / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return {}
    return {
        p.name: p
        for p in hooks_dir.iterdir()
        if p.is_file() and not p.name.endswith(".sample")
    }


def _mentions_entire(path: Path) -> bool:
    try:
        return "entire" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def git_hooks_mentioning_entire(repo: str | Path) -> list[str]:
    """Which git hooks (by filename, e.g. ``pre-push``) reference ``entire``."""
    repo = Path(repo).expanduser()
    return sorted(
        name for name, p in _git_hook_paths(repo).items() if _mentions_entire(p)
    )


def _entire_settings_path(repo: Path) -> Path:
    return repo / ".entire" / "settings.json"


def _read_entire_settings(repo: Path) -> dict:
    return _read_json(_entire_settings_path(repo))


def entire_which() -> str | None:
    """``entire``'s path on ``$PATH``, or ``None``."""
    return shutil.which("entire")


def entire_version() -> str | None:
    """``entire --version``'s first line (e.g. ``'Entire CLI 0.10.5'``), or ``None``."""
    exe = entire_which()
    if exe is None:
        return None
    res = _run([exe, "--version"], cwd=Path.cwd())
    if not res or res.returncode != 0:
        return None
    first_line = (res.stdout or "").strip().splitlines()
    return first_line[0].strip() if first_line else None


def _count_local_checkpoints(repo: Path) -> int:
    res = _run(
        ["git", "for-each-ref", "refs/entire/checkpoints/*", "--format=%(refname)"],
        cwd=repo,
    )
    if not res or res.returncode != 0:
        return 0
    return len([l for l in res.stdout.splitlines() if l.strip()])


def _remote_entire_refs(repo: Path) -> bool | None:
    """Do any ``refs/entire/*`` exist on ``origin``? ``None`` when that can't be checked."""
    res = _run(["git", "ls-remote", "origin", "refs/entire/*"], cwd=repo, timeout=15.0)
    if not res or res.returncode != 0:
        return None
    return bool(res.stdout.strip())


def entire_status(repo: str = ".") -> dict:
    """What is already true of ``repo``'s Entire setup — reads only, never runs ``entire``.

    ``enabled`` means both halves are present: hooks in ``.claude/settings.json`` *and*
    a ``pre-push`` git hook that mentions ``entire`` — either alone (a manually edited
    settings file, a hook removed by hand) is not really enabled. ``push_sessions`` and
    ``telemetry`` fall back to Entire's own defaults (both ``True``) when
    ``.entire/settings.json`` doesn't mention them. ``remote_checkpoints`` is ``None``,
    not ``False``, when the remote can't be reached — offline is not evidence of
    nothing being pushed.
    """
    repo_path = Path(repo).expanduser().resolve()
    is_repo = is_git_repo(repo_path)
    hooks = claude_hooks(repo_path) if is_repo else []
    git_hooks = git_hooks_mentioning_entire(repo_path) if is_repo else []
    enabled = bool(hooks) and "pre-push" in git_hooks
    settings = _read_entire_settings(repo_path) if is_repo else {}
    strategy = settings.get("strategy_options") or {}
    push_sessions = bool(strategy.get("push_sessions", DFLT_PUSH_SESSIONS))
    telemetry = bool(settings.get("telemetry", DFLT_TELEMETRY))
    origin = origin_url(repo_path) if is_repo else None
    is_github = _is_github_origin(origin)
    risk = (
        "checkpoints will be pushed on next git push"
        if enabled and push_sessions and is_github
        else None
    )
    return {
        "repo": str(repo_path),
        "entire_installed": entire_which() is not None,
        "entire_version": entire_version(),
        "is_git_repo": is_repo,
        "enabled": enabled,
        "hooks": hooks,
        "git_hooks": git_hooks,
        "push_sessions": push_sessions,
        "telemetry": telemetry,
        "n_local_checkpoints": _count_local_checkpoints(repo_path) if is_repo else 0,
        "origin": origin,
        "is_github_origin": is_github,
        "remote_checkpoints": _remote_entire_refs(repo_path) if is_repo else None,
        "risk": risk,
    }


def _push_sessions_warning() -> str:
    return (
        "push_sessions is True on a repo whose origin is github.com: Entire's "
        "pre-push hook will push refs/entire/checkpoints/* (verbatim Claude Code "
        "transcripts, absolute paths unscrubbed) to origin on every ordinary "
        "`git push`. Turn it off with `entire configure --skip-push-sessions`."
    )


def entire_enable(
    repo: str = ".",
    *,
    push_sessions: bool = False,
    telemetry: bool = False,
    dry_run: bool = False,
) -> dict:
    """Enable Entire's Claude Code hooks in ``repo``, safe by default.

    Runs ``entire agent add claude-code``, then — unless told otherwise —
    ``entire configure --skip-push-sessions`` (so checkpoints stay local) and
    ``entire configure --telemetry=false`` (so nothing phones PostHog). Pass
    ``push_sessions=True`` or ``telemetry=True`` to keep either of Entire's own
    (opt-in) defaults; doing so on a github.com origin adds a plain-language entry
    to ``warnings`` rather than silently proceeding.

    ``dry_run=True`` reports the commands that would run and returns before running
    anything. Idempotent either way — ``entire agent add`` and ``entire configure``
    are themselves idempotent, so re-running this changes nothing that already
    matches.
    """
    if entire_which() is None:
        return {
            "ok": False,
            "error": "the `entire` CLI is not on PATH",
            "install": INSTALL_CMD,
        }
    repo_path = Path(repo).expanduser().resolve()
    if not is_git_repo(repo_path):
        return {"ok": False, "error": f"{repo_path} is not a git repository"}

    origin = origin_url(repo_path)
    is_github = _is_github_origin(origin)
    warnings = [_push_sessions_warning()] if push_sessions and is_github else []

    commands = [["entire", "agent", "add", "claude-code"]]
    if not push_sessions:
        commands.append(["entire", "configure", "--skip-push-sessions"])
    if not telemetry:
        commands.append(["entire", "configure", "--telemetry=false"])

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "repo": str(repo_path),
            "commands": [{"cmd": c, "would_run": True} for c in commands],
            "origin": origin,
            "is_github_origin": is_github,
            "warnings": warnings,
        }

    before_hooks = set(claude_hooks(repo_path))
    before_git_hooks = set(git_hooks_mentioning_entire(repo_path))

    results = [_cmd_result(c, _run(c, cwd=repo_path)) for c in commands]

    after_hooks = set(claude_hooks(repo_path))
    after_git_hooks = set(git_hooks_mentioning_entire(repo_path))
    settings = _read_entire_settings(repo_path)

    ok = all(r["ok"] for r in results)
    if not ok:
        warnings.append("one or more `entire` commands failed; see `commands` for stderr")

    return {
        "ok": ok,
        "dry_run": False,
        "repo": str(repo_path),
        "commands": results,
        "hooks_added": sorted(after_hooks - before_hooks),
        "git_hooks_installed": sorted(after_git_hooks - before_git_hooks),
        "settings": settings,
        "origin": origin,
        "is_github_origin": is_github,
        "warnings": warnings,
    }
