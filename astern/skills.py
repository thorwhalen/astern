"""The skills astern ships, and the command that makes an agent host see them.

The real files live in ``astern/data/skills/<name>/SKILL.md`` — inside the
package, so ``pip install astern`` carries them and ``gh skill`` (which matches
any non-hidden ``**/skills/*/SKILL.md``) discovers them. Claude Code reads only
``.claude/skills/``, so this repo keeps a relative symlink there, and a *user*
gets the same bridge from :func:`install_skills`.

Assets are **discovered from the directory**, never listed here: shipping a new
skill is adding a directory. Installing one is a symlink, so upgrading the
package upgrades the skill; where symlinks are unavailable, a copy, and the plan
says which happened. Nothing already at a destination is overwritten — a foreign
file of the same name reads ``conflict`` and stays as it was until ``force``.

>>> [asset.name for asset in bundled()]
['astern-recall']
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Asset", "bundled", "claude_home", "install_skills"]


def claude_home(target: str | Path | None = None) -> Path:
    """Where an agent host looks: ``target`` → ``$CLAUDE_CONFIG_DIR`` → ``~/.claude``."""
    if target is not None:
        return Path(target).expanduser()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


@dataclass(frozen=True)
class Asset:
    """One installable thing: a skill directory shipped inside the package."""

    kind: str
    name: str
    source: Path

    def destination(self, host: Path) -> Path:
        return host / "skills" / self.name


def bundled() -> list[Asset]:
    """Every skill this package ships, discovered from ``astern/data/skills``."""
    skills = Path(__file__).parent / "data" / "skills"
    assets = [
        Asset("skill", p.name, p)
        for p in (sorted(skills.iterdir()) if skills.is_dir() else ())
        if (p / "SKILL.md").is_file()
    ]
    return sorted(assets, key=lambda a: a.name)


def _symlinks_work() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.symlink(tmp, Path(tmp) / "probe", target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            return False
    return True


def _points_here(destination: Path, source: Path) -> bool:
    try:
        return destination.is_symlink() and destination.resolve() == source.resolve()
    except OSError:
        return False


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _place(source: Path, destination: Path, *, link: bool) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove(destination)
    if link:
        try:
            os.symlink(source, destination, target_is_directory=source.is_dir())
            return "symlink"
        except OSError:
            pass
    shutil.copytree(source, destination)
    return "copy"


def install_skills(
    *,
    target: str | Path | None = None,
    only: Sequence[str] | str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Make the bundled skills visible to Claude Code. Idempotent.

    Links each into ``target`` (default ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``).
    Returns the plan: one row per asset with its action — ``install``, ``ok``
    (already ours) or ``conflict`` (something else is there; left alone unless
    ``force``).

    >>> plan = install_skills(target='/nonexistent/host', dry_run=True)
    >>> plan['counts']
    {'install': 1, 'ok': 0, 'conflict': 0}
    """
    host = claude_home(target)
    link = _symlinks_work()
    assets = bundled()
    if only is not None:
        wanted = {n.strip() for n in _as_names(only) if n.strip()}
        unknown = wanted - {a.name for a in assets}
        if unknown:
            raise ValueError(f"no bundled skill named {', '.join(sorted(unknown))}")
        assets = [a for a in assets if a.name in wanted]
    rows = []
    for asset in assets:
        destination = asset.destination(host)
        exists = destination.exists() or destination.is_symlink()
        if not exists:
            action, reason = "install", "not present"
        elif _points_here(destination, asset.source):
            action, reason = "ok", "already linked to this package"
        elif force:
            action, reason = "install", "replacing what was there"
        else:
            action, reason = "conflict", "something else with this name is there"
        method = ""
        if action == "install" and not dry_run:
            method = _place(asset.source, destination, link=link)
        rows.append(
            {
                "kind": asset.kind,
                "name": asset.name,
                "destination": str(destination),
                "action": action,
                "method": method,
                "reason": reason,
            }
        )
    counts = {
        k: sum(r["action"] == k for r in rows) for k in ("install", "ok", "conflict")
    }
    return {"target": str(host), "dry_run": dry_run, "actions": rows, "counts": counts}


def _as_names(only: Sequence[str] | str) -> list[str]:
    """``only`` as a list of names — a CLI hands one comma-separated string.

    >>> _as_names('a, b'), _as_names(['a'])
    (['a', 'b'], ['a'])
    """
    names = only.split(",") if isinstance(only, str) else list(only)
    return [n.strip() for n in names]
