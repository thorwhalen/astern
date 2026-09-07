"""Where the transcripts are: homes, session files, raw records.

A *home* is one ``~/.claude``-shaped directory. The default is the one home this
process would use; a second account (``~/.claude-iq``) or a synced copy of another
machine's home is just another entry in the list. Nothing here interprets a record;
that is :mod:`astern.turns` (turn pairs) and, for session-level meaning,
``openloops.transcripts``.

>>> h = homes()  # doctest: +SKIP
>>> [sf.session_id for sf in iter_session_files(h, max_sessions=2)]  # doctest: +SKIP
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DFLT_HOME = Path.home() / ".claude"

#: How a nested transcript relates to its parent session. ``session`` is a top-level
#: transcript; ``subagent`` lives under ``<sid>/subagents/``; ``workflow`` under a
#: ``<sid>/wf_*/`` directory (the ``Workflow`` tool's runs).
KINDS = ("session", "subagent", "workflow")


@dataclass(frozen=True)
class Home:
    """One ``~/.claude``-shaped directory, named so records can say where they came from."""

    name: str
    path: Path

    @property
    def projects_dir(self) -> Path:
        return self.path / "projects"


def homes(home: str | Path | Home | Iterable[str | Path | Home] | None = None) -> list[Home]:
    """Resolve the ``home=`` seam to a list of :class:`Home`.

    ``None`` → the one default home (``$ASTERN_HOME`` or ``~/.claude``). A string or
    path → that one home. An iterable → several. A home's name is its directory
    name with the leading dot dropped (``claude``, ``claude-iq``).

    >>> homes('/tmp/.claude-x')[0].name
    'claude-x'
    """
    if home is None:
        env = os.environ.get("ASTERN_HOME")
        home = env if env else DFLT_HOME
    if isinstance(home, (str, Path, Home)):
        home = [home]
    out: list[Home] = []
    for h in home:
        if isinstance(h, Home):
            out.append(h)
        else:
            p = Path(h).expanduser()
            out.append(Home(name=p.name.lstrip("."), path=p))
    return out


@dataclass(frozen=True)
class SessionFile:
    """One transcript on disk, with the facts the ledger keys on (``size``, ``mtime``)."""

    path: Path
    home: str
    session_id: str
    project_slug: str
    kind: str
    parent_id: str | None
    size: int
    mtime: float

    @property
    def fingerprint(self) -> dict:
        return {"size": self.size, "mtime": self.mtime}


def _kind_and_parent(path: Path, project_dir: Path) -> tuple[str, str | None]:
    rel = path.relative_to(project_dir)
    if len(rel.parts) == 1:
        return "session", None
    parent = rel.parts[0]
    if "subagents" in rel.parts:
        return "subagent", parent
    if any(p.startswith("wf_") for p in rel.parts):
        return "workflow", parent
    return "subagent", parent


def _as_list(x) -> list[str]:
    if x is None:
        return []
    return [x] if isinstance(x, str) else list(x)


def iter_session_files(
    home: str | Path | Home | Iterable | None = None,
    *,
    since_days: float | None = None,
    projects: Iterable[str] | str | None = None,
    max_sessions: int | None = None,
    kinds: Iterable[str] = ("session",),
) -> Iterator[SessionFile]:
    """Yield transcript files across the given homes, newest first.

    ``since_days`` keeps files modified within that window; ``projects`` keeps only
    project dirs whose slug contains one of the substrings; ``kinds`` selects
    top-level sessions and/or their nested ``subagent`` / ``workflow`` transcripts.
    ``max_sessions`` caps the count after sorting, so it means "the N most recent".
    """
    cutoff = time.time() - since_days * 86400 if since_days else None
    keep = _as_list(projects)
    kinds = set(kinds)
    found: list[SessionFile] = []
    for h in homes(home):
        base = h.projects_dir
        if not base.is_dir():
            continue
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            if keep and not any(k in project_dir.name for k in keep):
                continue
            paths = project_dir.glob("*.jsonl") if kinds == {"session"} else project_dir.rglob("*.jsonl")
            for p in paths:
                kind, parent = _kind_and_parent(p, project_dir)
                if kind not in kinds:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if cutoff is not None and st.st_mtime < cutoff:
                    continue
                found.append(
                    SessionFile(
                        path=p,
                        home=h.name,
                        session_id=p.stem,
                        project_slug=project_dir.name,
                        kind=kind,
                        parent_id=parent,
                        size=st.st_size,
                        mtime=st.st_mtime,
                    )
                )
    found.sort(key=lambda sf: sf.mtime, reverse=True)
    for i, sf in enumerate(found):
        if max_sessions is not None and i >= max_sessions:
            break
        yield sf


def load_records(path: str | Path) -> list[dict]:
    """Parse one JSONL transcript, tolerating blank and malformed lines.

    Unknown record types are kept as-is: the format drifts between Claude Code
    versions and a parser that drops what it does not recognise loses data silently.
    """
    out: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
