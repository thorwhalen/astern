"""The SSOT of what astern does: plain functions, JSON-able in, JSON-able dict out.

Every surface — the ``astern`` CLI (``cw`` over ``_dispatch_funcs``), a future MCP
server (``py2mcp`` over string refs to these names), a skill's prose — wraps these
functions and nothing else. Nothing here prints or exits; nothing here knows what
called it.

The one-command test of v1: ``astern sync && astern report friction``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from astern import estimate as _estimate
from astern import ledger as _ledger
from astern import provenance as _prov
from astern import report as _report
from astern import views as _views
from astern.lenses import LENSES, load_builtin_lenses
from astern.sources import SessionFile, homes, iter_session_files, load_records
from astern.store import Store, mk_store
from astern.turns import iter_turns, session_meta


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_record(sf: SessionFile, records: list[dict]) -> dict:
    meta = session_meta(records)
    # A nested transcript's records carry the *parent's* ``sessionId`` -- only the file
    # name and the ``agentId`` field identify the subagent. Trusting the record here
    # would file every subagent under its parent's id, and a provenance hit would then
    # name the wrong transcript. For anything nested, the file stem is the identity.
    meta["session_id"] = (
        sf.session_id if sf.kind != "session" else (meta["session_id"] or sf.session_id)
    )
    meta["home"] = sf.home
    meta["kind"] = sf.kind
    meta["parent_id"] = sf.parent_id
    meta["project_slug"] = sf.project_slug
    meta["source"] = {"path": str(sf.path), "size": sf.size, "mtime": sf.mtime}
    meta["synced_at"] = _now()
    return meta


def _run_lens(
    store: Store,
    lens_name: str,
    session: dict,
    turns: list[dict],
    *,
    force: bool = False,
    **ctx,
) -> dict:
    """Run one lens on one session under the ledger's rules; return what happened."""
    lens_ = LENSES[lens_name]
    sid = session["session_id"]
    entry = _ledger.get_entry(store.ledger, sid)
    p = _ledger.plan(
        entry,
        lens=lens_name,
        version=lens_.version,
        n_turns=len(turns),
        incremental=lens_.incremental,
    )
    if force and p.action == "skip":
        p = _ledger.Plan("full", 0, "forced")
    if p.action == "skip":
        return {"lens": lens_name, "action": "skip", "reason": p.reason}
    key = f"{lens_name}/{sid}"
    prior = (
        list(store.findings[key])
        if (p.action == "incremental" and key in store.findings)
        else []
    )
    scope = turns[p.from_index :] if p.action == "incremental" else turns
    out = lens_(session, scope, from_index=p.from_index, prior=prior, store=store, **ctx)
    usage = {}
    for f in out:
        for k, v in (f.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    errors = [f["error"] for f in out if f.get("error")]
    if errors:
        # A judge that failed did not read these turns, so the ledger must not say it
        # did: marking them covered is how a batch silently skips what it could not
        # read. Previous findings are left exactly as they were.
        return {
            "lens": lens_name,
            "action": p.action,
            "reason": p.reason,
            "n_findings": 0,
            "usage": usage,
            "failed": True,
            "errors": errors[:3],
        }
    store.findings[key] = (prior + out) if p.action == "incremental" else out
    _ledger.mark(
        entry,
        lens=lens_name,
        version=lens_.version,
        through_index=len(turns),
        through_uuid=turns[-1]["uuid"] if turns else "",
        usage=usage,
    )
    store.ledger[sid] = entry
    return {
        "lens": lens_name,
        "action": p.action,
        "reason": p.reason,
        "n_findings": len(out),
        "usage": usage,
    }


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

    ``kinds`` defaults to ``'session'`` — top-level transcripts only — on measurement,
    not on principle. :func:`why` genuinely needs ``'session,subagent'`` (a delegating
    session's own turns never contain the code its subagents wrote), but on this
    machine's corpus (290 top-level sessions, 4547 nested transcripts, 2.1 GB of
    JSONL) that is 50.7 s and a 330 MB store — 442 MB on disk, because 36,000 small
    JSON files round up hard — against 7.6 s and 87 MB for sessions alone. Too big to
    impose on every ``sync``, so :func:`why` says when it needs it instead.
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
    for sf in iter_session_files(
        home,
        since_days=since_days,
        projects=projects,
        max_sessions=max_sessions,
        kinds=kinds.split(","),
    ):
        seen += 1
        entry = _ledger.get_entry(store.ledger, sf.session_id)
        if (
            not force
            and not _ledger.source_changed(entry, sf.fingerprint)
            and sf.session_id in store.turns
        ):
            skipped += 1
            session, turns = store.sessions[sf.session_id], store.turns[sf.session_id]
        else:
            records = load_records(sf.path)
            session = _session_record(sf, records)
            turns = list(iter_turns(records))
            store.sessions[sf.session_id] = session
            store.turns[sf.session_id] = turns
            _ledger.touch_source(
                entry,
                path=str(sf.path),
                home=sf.home,
                fingerprint=sf.fingerprint,
                n_turns=len(turns),
                last_uuid=turns[-1]["uuid"] if turns else "",
            )
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
    return {
        "homes": [h.name for h in homes(home)],
        "store": str(store.root),
        "seen": seen,
        "read": read,
        "unchanged": skipped,
        "lenses": per_lens,
        "session_ids": session_ids,
    }


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


def sessions(
    *, store: str | Store | None = None, project: str | None = None, limit: int = 50
) -> dict:
    """List synced sessions, newest first, with title, project, turns, and lens coverage."""
    store = mk_store(store)
    rows = []
    for sid in store.sessions:
        s = store.sessions[sid]
        if (
            project
            and project not in (s.get("project") or "")
            and project not in (s.get("project_slug") or "")
        ):
            continue
        entry = _ledger.get_entry(store.ledger, sid)
        rows.append(
            {
                "session_id": sid,
                "title": s.get("title", ""),
                "project": s.get("project", ""),
                "home": s.get("home", ""),
                "kind": s.get("kind", ""),
                "n_turns": entry.get("n_turns", 0),
                "ended_at": s.get("ended_at", ""),
                "lenses": {
                    k: v.get("through_index")
                    for k, v in (entry.get("lenses") or {}).items()
                },
            }
        )
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
        "turns": [
            {
                k: t[k]
                for k in (
                    "index",
                    "timestamp",
                    "user_prompt",
                    "assistant_summary",
                    "n_tool_calls",
                    "n_errors",
                )
            }
            for t in all_turns[-turns:]
        ],
        "findings": {
            k.split("/", 1)[0]: len(store.findings[k])
            for k in store.findings
            if k.endswith("/" + sid)
        },
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
    return {
        "lenses": [
            {
                "name": l.name,
                "kind": l.kind,
                "version": l.version,
                "incremental": l.incremental,
                "doc": l.doc,
            }
            for l in LENSES.values()
        ]
    }


def _parse_ts(iso: str) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _findings_by_session(
    store: Store, lens_name: str, *, project: str | None, since_days: float | None
) -> dict:
    cutoff = (
        datetime.now(timezone.utc).timestamp() - since_days * 86400
        if since_days is not None
        else None
    )
    out: dict[str, list[dict]] = {}
    for sid in store.sessions:
        s = store.sessions[sid]
        if (
            project
            and project not in (s.get("project") or "")
            and project not in (s.get("project_slug") or "")
        ):
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


def _synced(
    store: Store,
    *,
    session_id: str | None,
    since_days: float | None,
    projects: str | None,
    max_sessions: int | None,
) -> list[tuple[str, dict]]:
    """The synced sessions a judging run should consider, newest first."""
    if session_id:
        sid = _resolve(store, session_id)
        return [(sid, store.sessions[sid])]
    keep = [p.strip() for p in (projects or "").split(",") if p.strip()]
    cutoff = (
        (datetime.now(timezone.utc).timestamp() - since_days * 86400)
        if since_days
        else None
    )
    rows = []
    for sid in store.sessions:
        s = store.sessions[sid]
        if keep and not any(
            k in (s.get("project") or "") or k in (s.get("project_slug") or "")
            for k in keep
        ):
            continue
        when = _iso(s.get("ended_at", ""))
        if cutoff is not None and (when is None or when.timestamp() < cutoff):
            continue
        rows.append((sid, s))
    rows.sort(key=lambda r: r[1].get("ended_at") or "", reverse=True)
    return rows[:max_sessions] if max_sessions else rows


def _judgments(store: Store, lens_name: str) -> list[dict]:
    """Every stored judge call for one lens — the cost model's observations."""
    out = []
    for k in store.judgments:
        if k.startswith(lens_name + "/"):
            try:
                out.append(store.judgments[k])
            except (KeyError, ValueError):
                continue
    return out


def judge(
    lens: str = "synopsis",
    session_id: str | None = None,
    *,
    max_sessions: int | None = None,
    since_days: float | None = None,
    projects: str | None = None,
    model: str = "haiku",
    effort: str | None = None,
    max_chars: int | None = None,
    strict_schema: bool = False,
    workers: int = 1,
    store: str | Store | None = None,
    dry_run: bool = False,
    judge_fn=None,
) -> dict:
    """Run an LLM lens over the synced sessions, newest first, under the ledger.

    ``workers`` runs that many judge calls at once (each is its own ``claude``
    process; a call takes 30–150 s, so a 280-session batch is hours serial and
    well under one hour at 8). Every session writes only its own store keys, and
    the ledger is per session, so concurrent runs never contend.

    Idempotent by construction: a session this lens version already covered is
    ``skip`` and costs nothing; a session that was resumed is ``incremental`` and
    pays for its new turns only. ``dry_run`` builds every view and prices the batch
    from the fitted cost model without calling the judge once — run it before a
    batch, not after.

    ``strict_schema`` (default ``False``, see :mod:`astern.judge`) passes the
    schema to the CLI as ``--json-schema``; a rejected answer then costs a CLI-side
    retry (the whole conversation resent). The default embeds the schema in the
    prompt instead and parses loosely, trading strict validation for a predictable,
    single-pass bill.
    """
    store = mk_store(store)
    load_builtin_lenses()
    max_sessions = int(max_sessions) if max_sessions is not None else None
    since_days = float(since_days) if since_days is not None else None
    max_chars = int(max_chars) if max_chars is not None else None
    lens_ = LENSES[lens]
    if lens_.kind != "L":
        raise ValueError(
            f"{lens!r} is a {lens_.kind} lens; judge() runs L lenses "
            f"(use sync for heuristics)"
        )
    if judge_fn is None and not dry_run:
        judge_fn = _mk_claude_judge(
            model=model, effort=effort, strict_schema=strict_schema
        )
    cost_model = _estimate.fit(_judgments(store, lens)) if dry_run else None
    workers = max(1, int(workers))
    rows: list[dict] = []
    pending: list[tuple[dict, dict, list[dict], int]] = []
    for sid, session in _synced(
        store,
        session_id=session_id,
        since_days=since_days,
        projects=projects,
        max_sessions=max_sessions,
    ):
        turns = list(store.turns[sid]) if sid in store.turns else []
        entry = _ledger.get_entry(store.ledger, sid)
        p = _ledger.plan(
            entry,
            lens=lens,
            version=lens_.version,
            n_turns=len(turns),
            incremental=lens_.incremental,
        )
        row = {
            "session_id": sid,
            "title": session.get("title", ""),
            "project": session.get("project", ""),
            "n_turns": len(turns),
            "action": p.action,
            "reason": p.reason,
        }
        if p.action == "skip":
            rows.append(row)
            continue
        scope = turns[p.from_index :] if p.action == "incremental" else turns
        if dry_run:
            prior = _prior_of(store, lens, sid) if p.action == "incremental" else None
            view = _views.session_view(
                session,
                scope,
                prior=prior,
                **({"max_chars": max_chars} if max_chars else {}),
            )
            feats = _estimate.features(session, scope, view)
            row.update(
                {
                    "view_chars": len(view),
                    "features": feats,
                    "predicted": _estimate.predict(cost_model, feats),
                }
            )
            rows.append(row)
            continue
        rows.append(row)
        pending.append((row, session, turns, p.from_index))
    _judge_pending(
        store, lens, pending, judge_fn=judge_fn, max_chars=max_chars, workers=workers
    )
    return {
        "store": str(store.root),
        "lens": lens,
        "model": model,
        "dry_run": dry_run,
        "strict_schema": strict_schema,
        **_totals(rows, dry_run=dry_run),
        "sessions": rows,
    }


def _judge_one(
    store: Store,
    lens: str,
    row: dict,
    session: dict,
    turns: list[dict],
    from_index: int,
    *,
    judge_fn,
    max_chars,
) -> dict:
    t0 = time.time()
    res = _run_lens(store, lens, session, turns, judge=judge_fn, max_chars=max_chars)
    rec = _last_judgment(store, lens, session["session_id"], from_index)
    row.update(
        {
            "seconds": round(time.time() - t0, 1),
            "usage": res.get("usage") or {},
            "input_tokens": rec.get("input_tokens"),
            "output_tokens": rec.get("output_tokens"),
            "num_turns": rec.get("num_turns"),
            "n_findings": res.get("n_findings", 0),
            "failed": bool(res.get("failed")),
            "errors": res.get("errors"),
            "view_chars": (rec.get("features") or {}).get("view_chars"),
            "cost_usd": rec.get("cost_usd"),
            "model": rec.get("model"),
            "outcome": _outcome(store, lens, session["session_id"]),
        }
    )
    return row


def _judge_pending(
    store: Store, lens: str, pending: list, *, judge_fn, max_chars, workers: int
) -> None:
    """Run the judge over ``pending`` (row, session, turns, from_index) items, in place."""
    if not pending:
        return
    if workers <= 1:
        for row, session, turns, from_index in pending:
            _judge_one(
                store,
                lens,
                row,
                session,
                turns,
                from_index,
                judge_fn=judge_fn,
                max_chars=max_chars,
            )
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _judge_one,
                store,
                lens,
                row,
                session,
                turns,
                from_index,
                judge_fn=judge_fn,
                max_chars=max_chars,
            )
            for row, session, turns, from_index in pending
        ]
        for f in futures:
            f.result()


def _mk_claude_judge(*, model: str, effort: str | None, strict_schema: bool = False):
    from astern.judge import claude_judge

    def judge_fn(prompt: str, **kw):
        return claude_judge(
            prompt, model=model, effort=effort, strict_schema=strict_schema, **kw
        )

    return judge_fn


def _prior_of(store: Store, lens_name: str, sid: str) -> dict | None:
    key = f"{lens_name}/{sid}"
    if key not in store.findings:
        return None
    from astern.lenses.synopsis import _prior_synopsis

    return _prior_synopsis(list(store.findings[key]))


def _last_judgment(store: Store, lens_name: str, sid: str, from_index: int) -> dict:
    key = f"{lens_name}/{sid}/{from_index}"
    try:
        return dict(store.judgments[key])
    except (KeyError, ValueError):
        return {}


def _outcome(store: Store, lens_name: str, sid: str) -> str:
    key = f"{lens_name}/{sid}"
    if key not in store.findings:
        return ""
    for f in reversed(list(store.findings[key])):
        if f.get("kind") == "synopsis":
            return str((f.get("evidence") or {}).get("outcome") or "")
    return ""


def _totals(rows: list[dict], *, dry_run: bool) -> dict:
    acted = [r for r in rows if r["action"] != "skip"]
    tot = {
        "n_sessions": len(rows),
        "n_skipped": len(rows) - len(acted),
        "n_run": len(acted),
        "view_chars": sum(int(r.get("view_chars") or 0) for r in acted),
    }
    if dry_run:
        pred = [r.get("predicted") or {} for r in acted]
        tot["predicted"] = {
            "input_tokens": sum(int(p.get("input_tokens") or 0) for p in pred),
            "output_tokens": sum(int(p.get("output_tokens") or 0) for p in pred),
            "total_tokens": sum(int(p.get("total_tokens") or 0) for p in pred),
            "low": sum(int(p.get("low") or 0) for p in pred),
            "high": sum(int(p.get("high") or 0) for p in pred),
            "cost_usd": round(sum(float(p.get("cost_usd") or 0.0) for p in pred), 4),
        }
        return tot
    usage: dict = {}
    for r in acted:
        for k, v in (r.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    tot["usage"] = usage
    tot["cost_usd"] = round(sum(float(r.get("cost_usd") or 0.0) for r in acted), 4)
    tot["seconds"] = round(sum(float(r.get("seconds") or 0.0) for r in acted), 1)
    tot["n_failed"] = sum(1 for r in acted if r.get("failed"))
    return tot


def estimate(
    *,
    store: str | Store | None = None,
    lens: str = "synopsis",
    model: str = "haiku",
    home: str | None = None,
) -> dict:
    """Fit the cost model on what has been judged, then price what has not.

    Two prices, each saying which proxy it used: the *pending* one is exact about
    its input (the views are built from the store), the *corpus* one multiplies file
    bytes by the measured view-chars-per-byte ratio, because a session that was
    never synced has no view to measure.
    """
    store = mk_store(store)
    load_builtin_lenses()
    records = _judgments(store, lens)
    cost_model = _estimate.fit(records)
    lens_ = LENSES[lens]
    pending, synced_ids = [], set()
    for sid, session in _synced(
        store, session_id=None, since_days=None, projects=None, max_sessions=None
    ):
        synced_ids.add(sid)
        turns = list(store.turns[sid]) if sid in store.turns else []
        p = _ledger.plan(
            _ledger.get_entry(store.ledger, sid),
            lens=lens,
            version=lens_.version,
            n_turns=len(turns),
            incremental=lens_.incremental,
        )
        if p.action == "skip":
            continue
        scope = turns[p.from_index :] if p.action == "incremental" else turns
        view = _views.session_view(
            session,
            scope,
            prior=_prior_of(store, lens, sid) if p.action == "incremental" else None,
        )
        pending.append(
            _estimate.predict(cost_model, _estimate.features(session, scope, view))
        )
    unsynced_bytes, n_unsynced, n_corpus = 0, 0, 0
    for sf in iter_session_files(home):
        n_corpus += 1
        if sf.session_id not in synced_ids:
            n_unsynced += 1
            unsynced_bytes += sf.size
    per_byte = cost_model.get("view_chars_per_byte") or 0.0
    corpus = _estimate.predict(
        cost_model, {"view_chars": 0, "bytes": unsynced_bytes / max(1, n_unsynced)}
    )
    return {
        "store": str(store.root),
        "lens": lens,
        "model": model,
        "judged": {
            "n": cost_model["n"],
            "method": cost_model["method"],
            "models": cost_model.get("models", []),
        },
        "cost_model": cost_model,
        "pending": {
            "n": len(pending),
            "proxy": "view (built from the store)",
            **_sum_predictions(pending),
        },
        "corpus": {
            "n_sessions": n_corpus,
            "n_unsynced": n_unsynced,
            "bytes": unsynced_bytes,
            "proxy": f"bytes × {per_byte:.4f} view chars/byte"
            if per_byte
            else "bytes (no ratio measured yet)",
            **_sum_predictions([corpus] * n_unsynced),
        },
    }


def _sum_predictions(preds: list[dict]) -> dict:
    return {
        "input_tokens": sum(int(p.get("input_tokens") or 0) for p in preds),
        "output_tokens": sum(int(p.get("output_tokens") or 0) for p in preds),
        "total_tokens": sum(int(p.get("total_tokens") or 0) for p in preds),
        "low": sum(int(p.get("low") or 0) for p in preds),
        "high": sum(int(p.get("high") or 0) for p in preds),
        "cost_usd": round(sum(float(p.get("cost_usd") or 0.0) for p in preds), 4),
    }


def _split_target(target: str, line: int | None) -> tuple[str, int | None]:
    """``'a/b.py:42'`` → ``('a/b.py', 42)``; an explicit ``line`` always wins.

    >>> _split_target('a/b.py:42', None)
    ('a/b.py', 42)
    >>> _split_target('a/b.py', '7')
    ('a/b.py', 7)
    """
    if line is not None:
        return target, int(line)
    head, sep, tail = target.rpartition(":")
    return (head, int(tail)) if sep and tail.isdigit() else (target, None)


def _rel_to_repo(root: str, path: str) -> str:
    """``path`` as the repo-relative path git wants, whether or not it was absolute."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve() if (Path.cwd() / p).exists() else p
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(Path(root).resolve()))
        except ValueError:
            return str(p)
    return str(p)


def _candidates(
    store: Store,
    *,
    bridge_id: str | None,
    root: str,
    when,
    window_days: float,
    notes: list[str],
) -> list[str]:
    """The session ids to search, and the notes explaining how they were chosen."""
    base: list[str] = []
    if bridge_id:
        base = _prov.sessions_for_bridge(store, bridge_id)
        if not base:
            notes.append(
                f"the trailer names claude.ai session {bridge_id!r}, but no synced "
                "transcript carries that bridge id (another machine or account, or "
                "not synced yet) — falling back to a repo-wide scan"
            )
    if not base:
        base = _prov.sessions_in_repo(store, root, around=when, window_days=window_days)
        notes.append(
            f"scanned {len(base)} session(s) whose cwd is inside the repo"
            + (f", within {window_days:g} days of the commit" if when else "")
        )
        return base
    with_subs = _prov.with_subagents(store, base)
    if len(with_subs) == len(base):
        notes.append(
            "no subagent transcripts for this session are in the store; a session "
            "that delegated wrote the line from a subagent transcript, so re-run "
            "`astern sync --kinds session,subagent`"
        )
    return with_subs


def why(
    target: str,
    *,
    line: int | None = None,
    commit: bool = False,
    store: str | Store | None = None,
    repo: str | None = None,
    window_days: float = 14.0,
    max_hits: int = 10,
    entire: bool = True,
) -> dict:
    """Why does this line exist? — the commit, the session, and the turn that wrote it.

    ``target`` is ``<file>:<line>`` (a file plus ``--line`` works too) or, with
    ``--commit``, a commit-ish. A target that names no readable file and *does*
    resolve as a revision is read as a commit without the flag, so
    ``astern why 589f17a`` and ``astern why HEAD --commit`` are the same question:
    the commit's trailer names the session, and the hits are that session's tool
    calls against the files the commit touched.

    (``target`` is positional and therefore required, which is why ``--commit`` is a
    flag over it rather than an option carrying the sha: argh's grammar — the one
    ``cw`` reproduces — makes any parameter with a default an *option*, and
    ``astern why -t astern/judge.py:115`` is the wrong headline.)

    Two commits are reported, never one: ``blame`` is the last hand to touch the line
    (a CI ``ruff format`` pass owns a lot of them) and ``introduced`` is the commit
    whose diff first contained the text. When they differ, ``introduced`` is the one
    whose ``Claude-Session:`` trailer is worth following.

    When the Entire CLI is installed and this repo is enabled, its generation-time
    answer is included verbatim under ``entire``; astern's own retroactive chain runs
    either way, because Entire only speaks for work done after it was enabled.
    """
    store = mk_store(store)
    max_hits = int(max_hits)
    window_days = float(window_days)
    notes: list[str] = []
    raw, line_no = _split_target(target, line)
    root = repo or _prov.repo_root(raw) or str(Path.cwd())
    rel = _rel_to_repo(root, raw)
    as_commit = bool(commit) or (line_no is None and not (Path(root) / rel).is_file())
    text, blame, introduced, sha = "", None, None, None
    if as_commit:
        sha, rel = raw, None
        introduced = _prov.commit_record(root, sha)
        if introduced is None:
            raise ValueError(
                f"{target!r} is neither a readable file in {root} nor a commit there"
            )
    else:
        if line_no is None:
            raise ValueError(f"{target!r} needs a line: <file>:<line>, or --line N")
        text = _prov.line_text(root, rel, line_no)
        if not text:
            notes.append(f"{rel}:{line_no} is not readable in the working tree")
        blame = _prov.commit_record(root, _prov.blame_commit(root, rel, line_no))
        introduced = _prov.commit_record(root, _prov.introducing_commit(root, rel, text))
        if blame and introduced and blame["sha"] != introduced["sha"]:
            notes.append(
                f"blame says {blame['short_sha']} ({blame['subject'][:60]}) but the "
                f"text was introduced by {introduced['short_sha']}"
            )
    carrier = introduced or blame
    bridge_id = (carrier or {}).get("session_id")
    if not bridge_id:
        notes.append("no Claude-Session trailer on the commit")
    when = _iso((carrier or {}).get("date") or "")
    sids = _candidates(
        store,
        bridge_id=bridge_id,
        root=root,
        when=when,
        window_days=window_days,
        notes=notes,
    )
    hits: list[dict] = []
    if text:
        hits.extend(_prov.iter_hits(store, sids, text))
    else:
        # No line to match, so match the commit's files against each call's digest.
        for path in _prov.changed_files(root, sha):
            hits.extend(
                _prov.iter_hits(
                    store,
                    sids,
                    path,
                    fields=("digest",),
                    min_chars=_prov.MIN_PATH_CHARS,
                )
            )
    if not hits and text:
        notes.append(
            "no tool call in those transcripts contains the line — it may predate "
            "the corpus, or have been written on another machine"
        )
    return {
        "repo": root,
        "file": rel,
        "line": line_no,
        "text": text,
        "commits": {"blame": blame, "introduced": introduced},
        "session_id": bridge_id,
        "searched": len(sids),
        "hits": _prov.rank_hits(hits)[:max_hits],
        "entire": (
            _prov.entire_why(root, rel, line_no) if entire and rel and line_no else None
        ),
        "notes": notes,
    }


_dispatch_funcs = [sync, sessions, show, lenses, report, judge, estimate, why]

#: Per-parameter ``add_argument`` overrides for the CLI (``cw``'s ``config=`` seam).
_dispatch_config: dict = {}
