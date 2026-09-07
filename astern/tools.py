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

from astern import estimate as _estimate
from astern import ledger as _ledger
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
    meta["session_id"] = meta["session_id"] or sf.session_id
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
    store: str | Store | None = None,
    dry_run: bool = False,
    judge_fn=None,
) -> dict:
    """Run an LLM lens over the synced sessions, newest first, under the ledger.

    Idempotent by construction: a session this lens version already covered is
    ``skip`` and costs nothing; a session that was resumed is ``incremental`` and
    pays for its new turns only. ``dry_run`` builds every view and prices the batch
    from the fitted cost model without calling the judge once — run it before a
    batch, not after.
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
        judge_fn = _mk_claude_judge(model=model, effort=effort)
    cost_model = _estimate.fit(_judgments(store, lens)) if dry_run else None
    rows: list[dict] = []
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
        t0 = time.time()
        res = _run_lens(store, lens, session, turns, judge=judge_fn, max_chars=max_chars)
        rec = _last_judgment(store, lens, sid, p.from_index)
        row.update(
            {
                "seconds": round(time.time() - t0, 1),
                "usage": res.get("usage") or {},
                "n_findings": res.get("n_findings", 0),
                "failed": bool(res.get("failed")),
                "errors": res.get("errors"),
                "view_chars": (rec.get("features") or {}).get("view_chars"),
                "cost_usd": rec.get("cost_usd"),
                "model": rec.get("model"),
                "outcome": _outcome(store, lens, sid),
            }
        )
        rows.append(row)
    return {
        "store": str(store.root),
        "lens": lens,
        "model": model,
        "dry_run": dry_run,
        **_totals(rows, dry_run=dry_run),
        "sessions": rows,
    }


def _mk_claude_judge(*, model: str, effort: str | None):
    from astern.judge import claude_judge

    def judge_fn(prompt: str, **kw):
        return claude_judge(prompt, model=model, effort=effort, **kw)

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


_dispatch_funcs = [sync, sessions, show, lenses, report, judge, estimate]
