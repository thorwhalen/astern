"""Recall: what did past sessions already think, try and decide about X?

astern is the **record source**; ``ir`` owns indexing and search; the multi-hop
loop belongs to ``raglab``. This module is that seam, and nothing more: it
renders the store into JSON-able records (:func:`records`), registers them with
``ir`` as two named corpora and builds them (:func:`index`), and asks ``ir`` to
search them (:func:`recall`).

Two grains, deliberately separate corpora rather than one mixed pile:

- ``session_synopses`` — one record per session, from the ``synopsis`` lens's
  findings: goal, problems and their solutions, decisions, corrections, rendered
  as short prose. Small, LLM-distilled, and the best hit for *what was decided*.
- ``session_turns`` — one record per turn (prompt + the assistant's closing
  text). Free, fine-grained, and the best hit for *what was actually tried*;
  this is the shape ``ir``'s :class:`ir.ClaudeTurn` strategy already indexes.

A third grain, ``episodes`` (consecutive turns on one topic), is the natural
unit for "the thinking around X" and is deliberately **not** here — see
thorwhalen/astern#7.

``ir`` (and its ``ef`` / ``vd`` dependencies) is an optional extra:
``pip install "astern[recall]"``. Every import of it is lazy and inside
:func:`_ir`, so ``import astern`` stays as light as it was.

>>> from astern.store import MemoryStore
>>> store = MemoryStore()
>>> store.sessions['s1'] = {'session_id': 's1', 'title': 'Fix CI', 'project': 'astern'}
>>> store.turns['s1'] = [{'index': 0, 'uuid': 'u0', 'user_prompt': 'why is CI red?',
...                       'assistant_summary': 'a stale lockfile', 'tools': []}]
>>> [r['id'] for r in records('session_turns', store=store)]
['s1:u0']
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from astern.store import Store, mk_store

#: The two grains astern indexes, and the ir corpus name each becomes.
GRAINS = ("session_synopses", "session_turns")

# seam candidate: `episodes` — consecutive turns on one topic, split on
# embedding-distance jumps and file-set overlap (plus compact_boundary /
# away_summary). A third grain here, an H lens upstream. See astern#7.

#: The embedder every astern corpus is built with unless told otherwise.
#: ``ir``'s local ``all-MiniLM-L6-v2`` — offline after a one-time model
#: download, **no API key, no per-query cost**. ``embedder="light"`` selects
#: ir's numpy-only hashing embedder instead: zero download (what the tests use),
#: at the price of dense scores that carry almost no semantics — with ``light``
#: prefer ``mode="lexical"``.
DFLT_EMBEDDER = "default"

#: Retrieval mode for :func:`recall`: dense + BM25, fused. Session prose is
#: long-form, but the queries that matter carry identifiers ("json schema
#: retries", "ledger"), which is exactly where the lexical leg earns its keep.
DFLT_MODE = "hybrid"

#: How much of a hit's text a recall result carries. Enough to judge relevance;
#: the full record is one ``astern show`` away, which is what ``pointer`` says.
SNIPPET_CHARS = 600

#: Corpora outside astern that a recall reaches into when they are registered
#: *and* built — the skills you already have, the reports you already wrote.
COMPANION_CORPORA = ("skills", "reports")

#: Federated rank-fusion weight for the companion corpora. Below 1.0 because the
#: question asked here is *what did past sessions say*: a skill or a report is a
#: welcome answer, but it should not take half the slots from the sessions on the
#: strength of a rank it earned in a corpus of a different size.
COMPANION_WEIGHT = 0.5

#: Record fields lifted into ir's hard-filter metadata, per grain. This is the
#: JSON-friendly stand-in for a ``metadata_of`` callable (which a registry entry
#: could not carry) — see ``ir.CorpusSource.from_records``.
METADATA_KEYS = {
    "session_synopses": (
        "session_id",
        "title",
        "project",
        "cwd",
        "started_at",
        "ended_at",
        "timestamp",
        "outcome",
        "prs",
        "n_turns",
    ),
    "session_turns": (
        "turn_index",
        "n_errors",
        "n_tool_calls",
        "tools",
        "files",
        "title",
    ),
}

_MISSING_IR = (
    "astern recall needs `ir` (and its ef / vd dependencies): "
    'pip install "astern[recall]". '
    "ir owns the indexing and the search; astern only supplies the records."
)


def _ir():
    """Import ``ir``, or say what to install. The one place ``ir`` is imported."""
    try:
        import ir
    except ImportError as e:  # pragma: no cover - exercised by the message test
        raise ImportError(f"{_MISSING_IR} (import failed: {e})") from e
    return ir


# --------------------------------------------------------------------------- #
# Records — astern's half of the contract
# --------------------------------------------------------------------------- #


def _iso_cutoff(since_days: float | None) -> str | None:
    """The ISO timestamp ``since_days`` ago, in the shape transcripts use.

    >>> _iso_cutoff(None) is None
    True
    >>> _iso_cutoff(1).endswith('Z')
    True
    """
    if since_days is None:
        return None
    when = datetime.now(timezone.utc) - timedelta(days=float(since_days))
    return _normalize_ts(when.isoformat(timespec="seconds"))


def _matches(session: dict, *, project: str | None, cutoff: str | None) -> bool:
    """Whether a session passes the project / recency filters.

    ``project`` is a **substring** of the session's project, project slug or cwd —
    the same rule ``astern sessions --project`` uses, and the cwd is in it because
    a session about a project is often not *rooted* at it: a Claude group dir or a
    worktree gives the session a cwd basename that names neither.

    >>> s = {'project': 'agent-x', 'cwd': '/p/astern/.claude/worktrees/agent-x',
    ...      'ended_at': '2026-09-01T00:00:00Z'}
    >>> _matches(s, project='astern', cutoff=None)
    True
    >>> _matches(s, project='vd', cutoff=None)
    False
    >>> _matches(s, project=None, cutoff='2026-09-02T00:00:00Z')
    False
    """
    named = " ".join(
        str(session.get(k) or "") for k in ("project", "project_slug", "cwd")
    )
    if project and project not in named:
        return False
    if cutoff is not None:
        ended = _normalize_ts(session.get("ended_at") or "")
        if not ended or ended < _normalize_ts(cutoff):
            return False
    return True


def _normalize_ts(ts: str) -> str:
    """ISO timestamps in one comparable shape — the ``Z`` one transcripts write.

    Timestamps are compared as **strings** (that is what a ``vd`` metadata filter
    does too), so the two spellings UTC has must be reduced to one, or a stored
    ``…Z`` stamp would sort after every ``…+00:00`` cutoff and a date filter would
    quietly keep everything.

    >>> _normalize_ts('2026-09-01T10:00:00+00:00')
    '2026-09-01T10:00:00Z'
    >>> _normalize_ts('2026-09-01T10:00:00Z'), _normalize_ts('')
    ('2026-09-01T10:00:00Z', '')
    """
    if not ts:
        return ""
    return ts[: -len("+00:00")] + "Z" if ts.endswith("+00:00") else ts


def _sessions(
    store: Store, *, project: str | None, since_days: float | None
) -> Iterator[tuple[str, dict]]:
    """The synced sessions passing the filters, as ``(session_id, meta)``."""
    cutoff = _iso_cutoff(since_days)
    for sid in store.sessions:
        try:
            session = store.sessions[sid]
        except (KeyError, ValueError):  # a half-written file is not a reason to stop
            continue
        if _matches(session, project=project, cutoff=cutoff):
            yield sid, session


def _session_fields(sid: str, session: dict) -> dict:
    """The metadata every grain's records carry, whatever the grain."""
    return {
        "session_id": sid,
        "title": session.get("title") or "",
        "project": session.get("project") or "",
        "cwd": session.get("cwd") or "",
        "git_branch": session.get("git_branch") or "",
    }


def _bullets(items: list, keys: tuple[str, ...], *, limit: int = 8) -> list[str]:
    """Render list-of-dict findings as ``a — b`` lines, skipping the empty ones.

    >>> _bullets([{'problem': 'p', 'solution': 's'}, {'problem': ''}],
    ...          ('problem', 'solution'))
    ['p — s']
    """
    out = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        parts = [str(item.get(k) or "").strip() for k in keys]
        parts = [p for p in parts if p]
        if parts:
            out.append(" — ".join(parts))
    return out


def synopsis_text(evidence: dict) -> str:
    """The prose a synopsis finding becomes: goal, outcome, problems, decisions.

    Only the fields that answer *what was decided and what went wrong* — the
    session's vocabulary and skill candidates are for the reports, not for
    recall, and padding the embedded text with them dilutes the signal.

    >>> print(synopsis_text({'goal': 'unred the CI', 'outcome': 'done',
    ...                      'problems': [{'problem': 'stale lockfile',
    ...                                    'solution': 'regenerated it'}],
    ...                      'notable_decisions': ['pin numpy below 2']}))
    Goal: unred the CI
    Outcome: done
    Problems and solutions:
    - stale lockfile — regenerated it
    Decisions:
    - pin numpy below 2
    """
    lines = []
    if evidence.get("goal"):
        lines.append(f"Goal: {evidence['goal']}")
    if evidence.get("outcome"):
        lines.append(f"Outcome: {evidence['outcome']}")
    sections = (
        ("Problems and solutions", "problems", ("problem", "solution")),
        ("Friction", "friction", ("what", "cause_guess")),
        ("Corrections", "corrections", ("what_user_said", "rule_candidate")),
    )
    for heading, key, fields in sections:
        got = _bullets(list(evidence.get(key) or []), fields)
        if got:
            lines.append(f"{heading}:")
            lines += [f"- {line}" for line in got]
    decisions = [str(d).strip() for d in (evidence.get("notable_decisions") or [])]
    decisions = [d for d in decisions if d]
    if decisions:
        lines.append("Decisions:")
        lines += [f"- {d}" for d in decisions]
    return "\n".join(lines)


def _synopsis_records(store: Store, sid: str, session: dict) -> Iterator[dict]:
    """One record per session, merging every ``synopsis`` finding it has.

    A resumed session is judged incrementally, so its findings list holds
    several synopses; they are extensions of one another, and the record joins
    them rather than picking one — dropping the later ones would index a
    session by what it looked like halfway through.
    """
    key = f"synopsis/{sid}"
    if key not in store.findings:
        return
    try:
        found = list(store.findings[key])
    except (KeyError, ValueError):
        return
    texts, outcome = [], ""
    for f in found:
        if f.get("kind") != "synopsis" or not isinstance(f.get("evidence"), dict):
            continue
        text = synopsis_text(f["evidence"])
        if text:
            texts.append(text)
            outcome = f["evidence"].get("outcome") or outcome
    if not texts:
        return
    ended = session.get("ended_at") or ""
    yield {
        "id": sid,
        "text": "\n\n".join(texts),
        **_session_fields(sid, session),
        "started_at": session.get("started_at") or "",
        "ended_at": ended,
        "timestamp": ended,
        "outcome": outcome,
        "prs": [
            f"{p.get('repo') or ''}#{p.get('number')}"
            for p in (session.get("prs") or [])
            if isinstance(p, dict)
        ],
        "n_turns": len(store.turns[sid]) if sid in store.turns else 0,
        "pointer": f"astern show {sid}",
    }


def _files_touched(turn: dict) -> list[str]:
    """The files an Edit/Write/NotebookEdit touched in this turn, deduped.

    >>> _files_touched({'tools': [{'name': 'Edit', 'digest': '/a/b.py'},
    ...                           {'name': 'Bash', 'digest': 'ls'}]})
    ['/a/b.py']
    """
    out: list[str] = []
    for t in turn.get("tools") or []:
        if t.get("name") in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
            digest = str(t.get("digest") or "").strip()
            if digest and digest not in out:
                out.append(digest)
    return out


def _turn_records(
    store: Store, sid: str, session: dict, *, include_full: bool
) -> Iterator[dict]:
    """One record per turn that carries prose; the shape ``ir.ClaudeTurn`` indexes."""
    if sid not in store.turns:
        return
    try:
        turns = list(store.turns[sid])
    except (KeyError, ValueError):
        return
    fields = _session_fields(sid, session)
    for turn in turns:
        user = (turn.get("user_prompt") or "").strip()
        summary = (turn.get("assistant_summary") or "").strip()
        if not user and not summary:
            continue  # a turn with no prose is a tool-call artifact, not a memory
        tools = [t.get("name", "") for t in (turn.get("tools") or []) if t.get("name")]
        record = {
            "id": f"{sid}:{turn.get('uuid') or turn.get('index')}",
            **fields,
            "session_title": fields["title"],
            "user_prompt": user,
            "assistant_summary": summary,
            "turn_index": turn.get("index"),
            "timestamp": turn.get("timestamp") or "",
            "git_branch": turn.get("git_branch") or fields["git_branch"],
            "model": (turn.get("models") or [""])[0],
            "tools": sorted(set(tools)),
            "n_tool_calls": int(turn.get("n_tool_calls") or 0),
            "n_errors": int(turn.get("n_errors") or 0),
            "files": _files_touched(turn),
            "has_tool_use": bool(tools),
            "pointer": f"astern show {sid} --turns {(turn.get('index') or 0) + 1}",
        }
        if include_full:
            record["assistant_full"] = (turn.get("assistant_full") or "").strip()
        yield record


def records(
    grain: str,
    *,
    store: str | Store | None = None,
    project: str | None = None,
    since_days: float | None = None,
    include_full: bool = False,
) -> Iterator[dict]:
    """Yield the JSON-able records of one ``grain``, in the shape ``ir`` indexes.

    Ids are stable, because they are what the index keys on: ``<sid>`` for a
    synopsis, ``<sid>:<turn_uuid>`` for a turn. ``project`` / ``since_days``
    narrow the *source*; the same filters exist at query time in :func:`recall`,
    where they are hard metadata filters and cost nothing to change.

    >>> from astern.store import MemoryStore
    >>> store = MemoryStore()
    >>> store.sessions['s1'] = {'session_id': 's1', 'project': 'astern'}
    >>> store.findings['synopsis/s1'] = [
    ...     {'kind': 'synopsis', 'evidence': {'goal': 'ship recall', 'outcome': 'done'}}]
    >>> rec = next(records('session_synopses', store=store))
    >>> rec['id'], rec['outcome'], rec['text'].splitlines()[0]
    ('s1', 'done', 'Goal: ship recall')
    """
    if grain not in GRAINS:
        raise ValueError(f"unknown grain {grain!r}; known: {GRAINS}")
    store = mk_store(store)
    for sid, session in _sessions(store, project=project, since_days=since_days):
        if grain == "session_synopses":
            yield from _synopsis_records(store, sid, session)
        else:
            yield from _turn_records(store, sid, session, include_full=include_full)


def synopsis_records() -> list[dict]:
    """Every session-synopsis record in the default store — ``ir``'s fetcher.

    A registry entry is JSON, so ``ir`` holds a *reference* to this function
    (``"astern.recall:synopsis_records"``) rather than an object: that is what
    lets ``ir build session_synopses``, in a process that knows nothing about
    this one, rebuild the corpus. ``$ASTERN_DATA_DIR`` therefore selects the
    store here exactly as it does for every other astern verb.
    """
    return list(records("session_synopses"))


def turn_records() -> list[dict]:
    """Every turn record in the default store — ``ir``'s fetcher (see above)."""
    return list(records("session_turns"))


#: grain -> the registry entry ``ir`` persists for it. The fetchers are named,
#: never passed: an entry that held a callable could not be written to disk.
CORPUS_SPECS: dict[str, dict] = {
    "session_synopses": {
        "kind": "records",
        "fetcher": "astern.recall:synopsis_records",
        "strategy": {"name": "Chunked", "params": {"text_key": "text"}},
        "metadata_keys": list(METADATA_KEYS["session_synopses"]),
    },
    "session_turns": {
        "kind": "records",
        "fetcher": "astern.recall:turn_records",
        "strategy": {"name": "ClaudeTurn", "params": {"include_full": False}},
        "metadata_keys": list(METADATA_KEYS["session_turns"]),
    },
}


# --------------------------------------------------------------------------- #
# index / recall — ir's half, driven through its public API
# --------------------------------------------------------------------------- #


def _grains(grain: str) -> list[str]:
    """``'all'`` or a comma-separated list -> grain names.

    >>> _grains('all')
    ['session_synopses', 'session_turns']
    >>> _grains('session_turns')
    ['session_turns']
    """
    if grain in ("all", "*", ""):
        return list(GRAINS)
    names = [g.strip() for g in str(grain).split(",") if g.strip()]
    unknown = [g for g in names if g not in GRAINS]
    if unknown:
        raise ValueError(f"unknown grain(s) {unknown}; known: {GRAINS}")
    return names


def index(
    grain: str = "all",
    *,
    store: str | Store | None = None,
    embedder: str = DFLT_EMBEDDER,
    refresh: bool = False,
) -> dict:
    """Register the astern corpora with ``ir`` and build them. Idempotent.

    Registration writes ``ir``'s corpora config (``~/.config/ir/corpora.json``);
    the build embeds only what changed, because ``ir`` keys every artifact on a
    content hash — so re-running after a ``sync`` costs the new records only.
    ``refresh=True`` re-registers first, which is how an embedder change takes
    effect (``ir`` then re-embeds everything, its ledger having pinned the old
    embedder id).

    ``store=`` is honoured by pointing ``$ASTERN_DATA_DIR`` at it for the build,
    because the fetcher ``ir`` calls back is a *name*, resolved in a process
    that never saw this call's arguments.
    """
    ir = _ir()
    names = _grains(grain)
    out: dict[str, Any] = {"embedder": embedder, "corpora": {}}
    with _store_env(store):
        for name in names:
            spec = dict(CORPUS_SPECS[name])
            kind = spec.pop("kind")
            entry = ir.registry.get(name)
            if refresh or entry is None or entry.get("embedder") != embedder:
                ir.register(name, kind, embedder=embedder, **spec)
            corpus = ir.build_corpus(name)
            out["corpora"][name] = {
                "n_records": len(corpus),
                "n_artifacts": _n_artifacts(corpus),
                "embedder_id": corpus.embedder_id,
                "path": str(ir.config.corpus_dir(name)),
            }
    out["config"] = str(ir.config.registry_path())
    return out


def _n_artifacts(corpus) -> int:
    """How many source records the corpus holds (records are per *surface*)."""
    try:
        return sum(1 for _ in corpus.store.ledger_items())
    except (AttributeError, TypeError):  # pragma: no cover - store variants
        return 0


class _store_env:
    """Point ``$ASTERN_DATA_DIR`` at ``store`` for the duration of a build.

    The fetcher ir calls is named, not passed, so a non-default store can only
    reach it through the environment — the same seam ``astern sync`` uses.
    """

    def __init__(self, store: str | Store | None):
        self.root = None if store is None else str(mk_store(store).root)
        self.previous: str | None = None

    def __enter__(self):
        if self.root is not None:
            self.previous = os.environ.get("ASTERN_DATA_DIR")
            os.environ["ASTERN_DATA_DIR"] = self.root
        return self

    def __exit__(self, *exc):
        if self.root is not None:
            if self.previous is None:
                os.environ.pop("ASTERN_DATA_DIR", None)
            else:
                os.environ["ASTERN_DATA_DIR"] = self.previous
        return False


def _built_corpora(ir, names: list[str]) -> tuple[list[str], list[str]]:
    """Split ``names`` into the corpora that are built and those that are not."""
    built, missing = [], []
    for name in names:
        try:
            corpus = ir.open_corpus(name)
            (built if len(corpus) else missing).append(name)
        except Exception:  # noqa: BLE001 - an unopenable corpus is simply not built
            missing.append(name)
    return built, missing


def _filter(
    store: Store, *, project: str | None, since_days: float | None
) -> dict | None:
    """The ``vd`` metadata filter for a recall, or ``None``.

    A ``vd`` filter has equality and ranges, not substrings — so ``project`` is
    resolved **through the store** into the session ids it names, and the index
    is filtered on those. That is not a workaround: the store is the SSOT of
    which sessions a project has, and it knows what a bare ``project`` field
    cannot — that a session run from a Claude group dir or a git worktree
    carries neither the project's name nor its slug, only its cwd.

    >>> from astern.store import MemoryStore
    >>> store = MemoryStore()
    >>> store.sessions['s1'] = {'cwd': '/p/astern/.claude/worktrees/w', 'project': 'w'}
    >>> _filter(store, project='astern', since_days=None)
    {'session_id': {'$in': ['s1']}}
    >>> _filter(store, project=None, since_days=None) is None
    True
    """
    clauses: dict[str, Any] = {}
    if project:
        ids = [sid for sid, _ in _sessions(store, project=project, since_days=None)]
        clauses["session_id"] = {"$in": sorted(ids)}
    cutoff = _iso_cutoff(since_days)
    if cutoff:
        clauses["timestamp"] = {"$gte": cutoff}
    return clauses or None


def _hit(disclosure) -> dict:
    """One ir ``Disclosure`` as a recall hit — small, cited, and followable."""
    meta = dict(disclosure.metadata or {})
    sid = meta.get("session_id") or ""
    text = disclosure.summary or ""
    pointer = meta.get("pointer") or disclosure.pointer
    if not pointer and sid:
        pointer = f"astern show {sid}"
    # A session hit is named by its session's title (often empty — sessions are
    # not always titled); only a non-session hit falls back to the artifact name,
    # where the name is a skill or a file path and means something.
    title = meta.get("title") or meta.get("session_title") or ""
    return {
        "grain": disclosure.source or "",
        "score": round(float(disclosure.score), 4),
        "session_id": sid,
        "title": title or ("" if sid else disclosure.name),
        "project": meta.get("project") or "",
        "timestamp": meta.get("timestamp") or meta.get("ended_at") or "",
        "turn_index": meta.get("turn_index"),
        "text": text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else ""),
        "pointer": pointer or "",
    }


def recall(
    query: str,
    *,
    grains: str = "all",
    project: str | None = None,
    since_days: float | None = None,
    k: int = 8,
    mode: str | None = None,
    store: str | Store | None = None,
    companions: bool = True,
) -> dict:
    """What past sessions already thought about ``query`` — ``ir`` federated over the grains.

    Searches ``session_synopses`` and ``session_turns`` (whichever are built),
    and — when no project/date filter is set — the ``skills`` and ``reports``
    corpora if this machine has them. A filter excludes those two deliberately
    rather than silently: they carry no ``session_id`` or ``timestamp`` metadata,
    so a hard filter would drop every one of their hits without saying so.

    Returns hits with a ``pointer`` — the command that fetches the full record —
    because the point is to read the few that matter, not to paste prose here.
    """
    ir = _ir()
    names = _grains(grains)
    filter_ = _filter(mk_store(store), project=project, since_days=since_days)
    notes: list[str] = []
    if project and not (filter_ or {}).get("session_id", {}).get("$in"):
        notes.append(
            f"no synced session matches project={project!r} — `astern sessions "
            f"--project {project}` shows what the store knows"
        )
    with _store_env(store):
        built, missing = _built_corpora(ir, names)
        if missing:
            notes.append(
                f"not built: {', '.join(missing)} — run `astern index` "
                f"(after `astern sync`) to build them"
            )
        if companions and filter_ is None:
            extra, _ = _built_corpora(ir, list(COMPANION_CORPORA))
            built += extra
        elif companions and filter_ is not None:
            notes.append(
                f"{'/'.join(COMPANION_CORPORA)} skipped: they carry no project or "
                f"timestamp metadata, so a filtered search cannot include them"
            )
        if not built:
            return {
                "query": query,
                "hits": [],
                "abstained": True,
                "notes": notes or ["no astern corpus is built"],
            }
        result = ir.discover(
            built,
            query,
            k=int(k),
            mode=mode or DFLT_MODE,
            filter=filter_,
            max_k=int(k),
            merge_weights={
                name: (COMPANION_WEIGHT if name in COMPANION_CORPORA else 1.0)
                for name in built
            },
        )
    return {
        "query": query,
        "corpora": built,
        "mode": mode or DFLT_MODE,
        "filter": filter_,
        "hits": [_hit(d) for d in result.results],
        "abstained": bool(result.abstained),
        "n_retrieved": result.n_retrieved,
        "notes": notes + ([result.reason] if result.reason else []),
    }
