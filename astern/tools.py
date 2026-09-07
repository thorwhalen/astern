"""The SSOT of what astern does: plain functions, JSON-able in, JSON-able dict out.

Every surface — the ``astern`` CLI (``cw`` over ``_dispatch_funcs``), a future MCP
server (``py2mcp`` over string refs to these names), a skill's prose — wraps these
functions and nothing else. Nothing here prints or exits; nothing here knows what
called it.

The one-command test of v1: ``astern sync && astern report friction``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from astern import ledger as _ledger
from astern import report as _report
from astern.lenses import LENSES, load_builtin_lenses
from astern.sources import SessionFile, homes, iter_session_files, load_records
from astern.store import Store, mk_store
from astern.turns import iter_turns, session_meta


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_record(sf: SessionFile, records: list[dict]) -> dict:
    meta = session_meta(records)
    meta["session_id"] = meta["session_id"] or sf.session_id
    meta["home"] = sf.home
    meta["kind"] = sf.kind
    meta["parent_id"] = sf.parent_id
    meta["project_slug"] = sf.project_slug
    meta["source"] = {"path": str(sf.path), "size": sf.size, "mtime": sf.mtime}
    meta["synced_at"] = _now()
    return meta


def _run_lens(store: Store, lens_name: str, session: dict, turns: list[dict], *, force: bool = False,
              **ctx) -> dict:
    """Run one lens on one session under the ledger's rules; return what happened."""
    lens_ = LENSES[lens_name]
    sid = session["session_id"]
    entry = _ledger.get_entry(store.ledger, sid)
    p = _ledger.plan(entry, lens=lens_name, version=lens_.version, n_turns=len(turns),
                     incremental=lens_.incremental)
    if force and p.action == "skip":
        p = _ledger.Plan("full", 0, "forced")
    if p.action == "skip":
        return {"lens": lens_name, "action": "skip", "reason": p.reason}
    key = f"{lens_name}/{sid}"
    prior = list(store.findings[key]) if (p.action == "incremental" and key in store.findings) else []
    scope = turns[p.from_index:] if p.action == "incremental" else turns
    out = lens_(session, scope, from_index=p.from_index, prior=prior, store=store, **ctx)
    usage = {}
    for f in out:
        for k, v in (f.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    store.findings[key] = (prior + out) if p.action == "incremental" else out
    _ledger.mark(entry, lens=lens_name, version=lens_.version, through_index=len(turns),
                 through_uuid=turns[-1]["uuid"] if turns else "", usage=usage)
    store.ledger[sid] = entry
    return {"lens": lens_name, "action": p.action, "reason": p.reason, "n_findings": len(out),
            "usage": usage}


def sync(
    home: str | None = None,
    *,
    since_days: float | None = None,
    projects: str | None = None,
    max_sessions: int | None = None,
    kinds: str = "session",
    lenses: str = "H",
    force: bool = False,
    store: str | Store | None = None,
) -> dict:
    """Read new or changed transcripts into the store and run the heuristic lenses.

    Idempotent: a transcript whose size and mtime the ledger already knows is not
    re-read; a lens that already covered every turn is not re-run (``force`` re-runs
    the lenses, never the LLM ones — those go through :func:`judge`). ``lenses`` is
    ``'H'`` (all heuristic lenses), ``'none'``, or a comma-separated list of names.
    """
    store = mk_store(store)
    load_builtin_lenses()
    # CLI adapters hand Optional[int] through as str; coerce here, once, for every surface.
    max_sessions = int(max_sessions) if max_sessions is not None else None
    since_days = float(since_days) if since_days is not None else None
    names = _lens_names(lenses, kind="H")
    seen = read = skipped = 0
    per_lens: dict[str, dict] = {}
    session_ids: list[str] = []
    for sf in iter_session_files(home, since_days=since_days, projects=projects,
                                 max_sessions=max_sessions, kinds=kinds.split(",")):
        seen += 1
        entry = _ledger.get_entry(store.ledger, sf.session_id)
        if not force and not _ledger.source_changed(entry, sf.fingerprint) and sf.session_id in store.turns:
            skipped += 1
            session, turns = store.sessions[sf.session_id], store.turns[sf.session_id]
        else:
            records = load_records(sf.path)
            session = _session_record(sf, records)
            turns = list(iter_turns(records))
            store.sessions[sf.session_id] = session
            store.turns[sf.session_id] = turns
            _ledger.touch_source(entry, path=str(sf.path), home=sf.home, fingerprint=sf.fingerprint,
                                 n_turns=len(turns), last_uuid=turns[-1]["uuid"] if turns else "")
            store.ledger[sf.session_id] = entry
            read += 1
        session_ids.append(sf.session_id)
        for name in names:
            r = _run_lens(store, name, session, turns, force=force)
            agg = per_lens.setdefault(name, {"ran": 0, "skipped": 0, "findings": 0})
            if r["action"] == "skip":
                agg["skipped"] += 1
            else:
                agg["ran"] += 1
                agg["findings"] += r["n_findings"]
    return {"homes": [h.name for h in homes(home)], "store": str(store.root), "seen": seen, "read": read,
            "unchanged": skipped, "lenses": per_lens, "session_ids": session_ids}


def _lens_names(spec: str | Iterable[str], *, kind: str | None) -> list[str]:
    load_builtin_lenses()
    if isinstance(spec, str):
        if spec.lower() == "none":
            return []
        if spec.upper() in ("H", "L"):
            return [n for n, l in LENSES.items() if l.kind == spec.upper()]
        if spec.lower() == "all":
            return list(LENSES)
        spec = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [s for s in spec if s not in LENSES]
    if unknown:
        raise KeyError(f"unknown lens(es) {unknown}; known: {sorted(LENSES)}")
    return list(spec)


def sessions(*, store: str | Store | None = None, project: str | None = None, limit: int = 50) -> dict:
    """List synced sessions, newest first, with title, project, turns, and lens coverage."""
    store = mk_store(store)
    rows = []
    for sid in store.sessions:
        s = store.sessions[sid]
        if project and project not in (s.get("project") or "") and project not in (s.get("project_slug") or ""):
            continue
        entry = _ledger.get_entry(store.ledger, sid)
        rows.append({"session_id": sid, "title": s.get("title", ""), "project": s.get("project", ""),
                     "home": s.get("home", ""), "kind": s.get("kind", ""), "n_turns": entry.get("n_turns", 0),
                     "ended_at": s.get("ended_at", ""),
                     "lenses": {k: v.get("through_index") for k, v in (entry.get("lenses") or {}).items()}})
    rows.sort(key=lambda r: r["ended_at"], reverse=True)
    return {"n": len(rows), "sessions": rows[:limit]}


def show(session_id: str, *, store: str | Store | None = None, turns: int = 5) -> dict:
    """One session: its meta, ledger entry, and the last ``turns`` turns (prompt + summary)."""
    store = mk_store(store)
    sid = _resolve(store, session_id)
    all_turns = store.turns[sid]
    return {
        "session": store.sessions[sid],
        "ledger": _ledger.get_entry(store.ledger, sid),
        "turns": [{k: t[k] for k in ("index", "timestamp", "user_prompt", "assistant_summary",
                                     "n_tool_calls", "n_errors")} for t in all_turns[-turns:]],
        "findings": {k.split("/", 1)[0]: len(store.findings[k]) for k in store.findings
                     if k.endswith("/" + sid)},
    }


def _resolve(store: Store, prefix: str) -> str:
    if prefix in store.sessions:
        return prefix
    matches = [s for s in store.sessions if s.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"{prefix!r} matches {len(matches)} synced sessions")


def lenses() -> dict:
    """The registered lenses: name, kind (H = heuristic, L = LLM-judged), version, one line."""
    load_builtin_lenses()
    return {"lenses": [{"name": l.name, "kind": l.kind, "version": l.version, "incremental": l.incremental,
                        "doc": l.doc} for l in LENSES.values()]}


def _parse_ts(iso: str) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _findings_by_session(store: Store, lens_name: str, *, project: str | None,
                         since_days: float | None) -> dict:
    cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400 if since_days is not None else None
    out: dict[str, list[dict]] = {}
    for sid in store.sessions:
        s = store.sessions[sid]
        if project and project not in (s.get("project") or "") and project not in (s.get("project_slug") or ""):
            continue
        if cutoff is not None:
            ts = _parse_ts(s.get("ended_at") or "")
            if ts is not None and ts < cutoff:
                continue
        key = f"{lens_name}/{sid}"
        if key in store.findings:
            out[sid] = list(store.findings[key])
    return out


def report(
    lens: str,
    *,
    store: str | Store | None = None,
    project: str | None = None,
    since_days: float | None = None,
    top: int = 20,
    fmt: str = "md",
) -> dict:
    """Cross-session report for one lens, over what has already been synced.

    ``fmt='md'`` (default) puts a markdown report in ``text``; ``fmt='json'`` puts
    the same underlying aggregate dict there instead.
    """
    store = mk_store(store)
    load_builtin_lenses()
    top = int(top)
    since_days = float(since_days) if since_days is not None else None
    if lens not in LENSES:
        raise KeyError(f"unknown lens {lens!r}; known: {sorted(LENSES)}")
    fbs = _findings_by_session(store, lens, project=project, since_days=since_days)
    if fmt == "json":
        text = _report.aggregate(lens, fbs, store=store, top=top)
    else:
        text = _report.render(lens, fbs, store=store, top=top)
    return {"lens": lens, "n_sessions": len(fbs), "text": text}


_dispatch_funcs = [sync, sessions, show, lenses, report]
