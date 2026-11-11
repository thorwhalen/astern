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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

try:  # optional speed-up (``pip install astern[fast]``); never required
    from orjson import loads as _orjson_loads
except ImportError:  # pragma: no cover - exercised by monkeypatching in the tests
    _orjson_loads = None

DFLT_HOME = Path.home() / ".claude"

#: The ``home=`` value meaning "every home this machine can see": each local
#: ``~/.claude*`` account home and each mirror of another machine's home (see
#: :func:`discover_homes`). ``$ASTERN_HOME``, when set, still pins the homes.
ALL_HOMES = "all"

#: Where mirrors of other machines' homes live when nothing says otherwise: ``xa``
#: rsyncs each configured SSH host's ``~/.claude`` into ``<cache_dir>/<host>/``.
DFLT_XA_CONFIG = Path("~/.config/xa/config.toml")
DFLT_MIRRORS_DIR = Path("~/.cache/xa/remotes")

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


def mirrors_dir() -> Path:
    """The directory holding mirrors of other machines' homes, one subdir per host.

    ``$ASTERN_MIRRORS`` wins; else ``xa``'s ``[settings] cache_dir`` (from
    ``$XA_CONFIG`` or ``~/.config/xa/config.toml``); else ``~/.cache/xa/remotes``.
    """
    env = os.environ.get("ASTERN_MIRRORS")
    if env:
        return Path(env).expanduser()
    cfg = Path(os.environ.get("XA_CONFIG") or DFLT_XA_CONFIG).expanduser()
    try:
        import tomllib  # py>=3.11

        cache_dir = tomllib.loads(cfg.read_text()).get("settings", {}).get("cache_dir")
    except (ImportError, OSError, ValueError):
        cache_dir = None
    return Path(cache_dir or DFLT_MIRRORS_DIR).expanduser()


def discover_homes(
    *, root: str | Path | None = None, mirrors: str | Path | None = None
) -> list[Home]:
    """Every home this machine can see: local accounts first, then mirrors.

    Local: each ``<root>/.claude*`` directory with a ``projects/`` subdir (``root``
    defaults to ``~``), named as :func:`homes` names them (``claude``,
    ``claude-iq``). Mirrors: each ``<mirrors>/<host>/`` with a ``projects/`` subdir
    (``mirrors`` defaults to :func:`mirrors_dir`), named after the host.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     for sub in ('.claude/projects', '.claude-iq/projects', 'm/host1/projects'):
    ...         Path(d, sub).mkdir(parents=True)
    ...     [h.name for h in discover_homes(root=d, mirrors=Path(d, 'm'))]
    ['claude', 'claude-iq', 'host1']
    """
    import re

    root = Path(root).expanduser() if root else Path.home()
    mdir = Path(mirrors).expanduser() if mirrors else mirrors_dir()
    account = re.compile(r"\.claude(-[A-Za-z0-9_]+)?")  # not .claude.bak, .claude.json
    local = sorted(
        p
        for p in root.glob(".claude*")
        if account.fullmatch(p.name) and (p / "projects").is_dir()
    )
    out: list[Home] = []
    taken: set[str] = set()
    real: set[Path] = set()

    def _add(name: str, path: Path) -> None:
        rp = path.resolve()
        if rp in real:  # a symlinked alias of a home already listed
            return
        real.add(rp)
        name = name if name not in taken else f"remote-{name}"
        taken.add(name)
        out.append(Home(name=name, path=path))

    for p in local:
        _add(p.name.lstrip("."), p)
    if mdir.is_dir():
        for p in sorted(mdir.iterdir()):
            if (p / "projects").is_dir():
                _add(p.name, p)
    return out


#: Skip a mirror refresh when the last one is younger than this (seconds), so a
#: burst of searches pays for one ``xa sync``, not one each.
DFLT_PULL_MIN_INTERVAL = 600.0


def _pull_stamp() -> Path:
    from astern.store import data_dir

    return data_dir() / "mirrors_pulled_at"


def pull_mirrors(
    *,
    force: bool = False,
    timeout: float = 120.0,
    min_interval: float = DFLT_PULL_MIN_INTERVAL,
) -> dict:
    """Refresh the mirrors of other machines' homes (``xa sync``), best effort.

    A no-op returning ``{"pulled": False, ...}`` when ``xa`` is not installed, so
    nothing here depends on it. Skipped when the last refresh is younger than
    ``min_interval`` seconds (``force`` overrides, and is passed on to ``xa``). Never
    prompts: stdin is closed, so an ssh asking for a password fails instead of
    waiting, and ``timeout`` bounds an unreachable host.
    """
    import shutil
    import subprocess

    xa = shutil.which("xa")
    if not xa:
        return {"pulled": False, "reason": "xa not on PATH"}
    stamp = _pull_stamp()
    try:
        age = time.time() - stamp.stat().st_mtime
    except OSError:
        age = None
    if not force and age is not None and age < min_interval:
        return {"pulled": False, "reason": f"refreshed {age:.0f}s ago"}
    argv = [xa, "sync"] + (["--force"] if force else [])
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"pulled": False, "reason": f"xa sync timed out after {timeout}s"}
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        pass
    return {
        "pulled": proc.returncode == 0,
        "output": (proc.stdout + proc.stderr).strip()[-2000:],
    }


def homes(
    home: str | Path | Home | Iterable[str | Path | Home] | None = None,
) -> list[Home]:
    """Resolve the ``home=`` seam to a list of :class:`Home`.

    ``None`` → the one default home (``$ASTERN_HOME`` or ``~/.claude``).
    ``"all"`` (:data:`ALL_HOMES`) → ``$ASTERN_HOME`` if set, else every home
    :func:`discover_homes` finds (both accounts, plus mirrors of other machines).
    A string or path → that one home. An iterable → several. A home's name is its
    directory name with the leading dot dropped (``claude``, ``claude-iq``).

    >>> homes('/tmp/.claude-x')[0].name
    'claude-x'
    """
    env = os.environ.get("ASTERN_HOME")
    if isinstance(home, str) and home == ALL_HOMES:
        if not env:
            return discover_homes()
        home = None
    if home is None:
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
    A transcript present in several homes is yielded once, from its newest copy.
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
            paths = (
                project_dir.glob("*.jsonl")
                if kinds == {"session"}
                else project_dir.rglob("*.jsonl")
            )
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
    found.sort(key=lambda sf: (sf.mtime, sf.size), reverse=True)
    # The same transcript can live in two homes (copied between accounts, or a
    # mirror of a machine that also synced it): keep only the newest copy, so the
    # store is never overwritten by an older one and a session is listed once.
    seen: set[tuple] = set()
    unique: list[SessionFile] = []
    for sf in found:
        key = (sf.kind, sf.parent_id, sf.session_id)
        if key not in seen:
            seen.add(key)
            unique.append(sf)
    found = unique
    for i, sf in enumerate(found):
        if max_sessions is not None and i >= max_sessions:
            break
        yield sf


#: orjson turns an integer beyond the 64-bit range into a float where ``json.loads``
#: returns the exact int. 19 digits is the shortest run that can overflow, so a line
#: holding one (in a number or, harmlessly, in a string) is left to ``json.loads``.
#: The check maps every ASCII digit to ``1`` in one C-level pass and looks for 19 of
#: them in a row -- several times cheaper than a ``\d{19}`` regex, which costs more
#: than the orjson decode it guards.
_DIGITS_TO_ONES = bytes.maketrans(b"0123456789", b"1" * 10)
_LONG_DIGIT_RUN = b"1" * 19


def fast_loads(line: str):
    """Decode one JSON line: orjson where it provably agrees with ``json.loads``.

    Falls back to ``json.loads`` when orjson is not installed, when the line has a run
    of 19+ digits (orjson would silently turn a huge int into a float), and when orjson
    rejects the line (``NaN``/``Infinity``/``-Infinity``, a lone surrogate escape or an
    overflowing exponent are valid to ``json.loads`` only). A line neither decoder
    accepts raises ``ValueError``, as ``json.loads`` does.

    >>> fast_loads('{"a": [1, 2.5, null]}')
    {'a': [1, 2.5, None]}
    >>> fast_loads('{"n": 18446744073709551616}')["n"] == 2**64
    True
    >>> fast_loads('[NaN]')[0] != fast_loads('[NaN]')[0]
    True
    """
    if _orjson_loads is not None:
        raw = line.encode(
            "utf-8", "surrogatepass"
        )  # lone surrogates: orjson rejects them
        if _LONG_DIGIT_RUN not in raw.translate(_DIGITS_TO_ONES):
            try:
                return _orjson_loads(raw)
            except ValueError:
                pass
    return json.loads(line)


def load_records(
    path: str | Path, *, loads: Callable[[str], object] | None = None
) -> list[dict]:
    """Parse one JSONL transcript, tolerating blank and malformed lines.

    Unknown record types are kept as-is: the format drifts between Claude Code
    versions and a parser that drops what it does not recognise loses data silently.

    ``loads`` decodes one stripped, non-empty line (default: :func:`fast_loads`);
    a line it rejects with ``ValueError`` is skipped, as is any non-object value.

    >>> import json, tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p = Path(d) / "t.jsonl"
    ...     _ = p.write_text('{"type": "x"}\\n\\noops\\n[1]\\n')
    ...     load_records(p, loads=json.loads)
    [{'type': 'x'}]
    """
    loads = loads or fast_loads
    out: list[dict] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    # split on "\n" only: str.splitlines() also breaks at U+2028/U+2029/\x85/\x0c,
    # which JSON writers leave raw inside strings, and would drop that record.
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out
