"""Cross-session reports: one aggregator + markdown renderer per lens.

Every heuristic lens produces findings *per session*; this module is where the
fleet-wide answer comes from — what recurs, what's expensive, what's newest. Each
lens has its own reporter function (``kind`` → shape → question, so the aggregate
worth showing differs per lens); a lens with no dedicated reporter falls back to
:func:`_generic_report`, a plain count of finding kinds, so a new lens is never
reportless.

:func:`aggregate` returns the JSON-able dict; :func:`render` formats it as markdown.
Both take the same ``findings_by_session`` shape astern.tools builds:
``{session_id: [finding, ...]}``.

>>> from astern.store import MemoryStore
>>> store = MemoryStore()
>>> store.sessions['s1'] = {'title': 'Fix CI', 'project': 'p'}
>>> findings = {'s1': [{'kind': 'session_stats', 'evidence': {'n_turns': 3,
...   'n_tool_calls': 5, 'n_errors': 1, 'duration_ms': 2000}}]}
>>> text = render('stats', findings, store=store)
>>> 'Fix CI' in text
True
>>> aggregate('stats', findings, store=store)['sessions'][0]['n_turns']
3
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable

from astern.store import Store


def _title(store: Store, sid: str) -> str:
    try:
        s = store.sessions[sid]
    except KeyError:
        return sid[:8]
    return s.get("title") or s.get("project") or sid[:8]


def _kinds_of(findings: list[dict], kind: str) -> list[dict]:
    return [f for f in findings if f.get("kind") == kind]


def _friction_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    kind_counts: Counter = Counter()
    tool_errors: Counter = Counter()
    retry_attempts: Counter = Counter()  # key -> attempts summed, for ranking
    retry_after_error: Counter = Counter()  # key -> n findings that followed an error
    retry_identical_input: Counter = Counter()  # key -> n findings that did not
    n_retry_after_error = 0
    n_retry_identical_input = 0
    rows = []
    for sid, findings in fbs.items():
        per_kind: Counter = Counter()
        for f in findings:
            kind = f.get("kind", "")
            kind_counts[kind] += 1
            per_kind[kind] += 1
            ev = f.get("evidence") or {}
            if kind == "tool_error":
                tool_errors[ev.get("tool", "")] += 1
            elif kind == "retry":
                key = f"{ev.get('tool', '')}: {ev.get('digest', '')}"
                retry_attempts[key] += ev.get("attempts", 1)
                if ev.get("after_error"):
                    retry_after_error[key] += 1
                    n_retry_after_error += 1
                else:
                    retry_identical_input[key] += 1
                    n_retry_identical_input += 1
        rows.append(
            {
                "session_id": sid,
                "title": _title(store, sid),
                "n_findings": len(findings),
                "kinds": dict(per_kind),
            }
        )
    rows.sort(key=lambda r: r["n_findings"], reverse=True)
    agg = {
        "kind_counts": dict(kind_counts),
        "top_error_tools": tool_errors.most_common(top),
        "top_retry_digests": [
            {
                "key": key,
                "attempts": n,
                "after_error": retry_after_error.get(key, 0),
                "identical_input": retry_identical_input.get(key, 0),
            }
            for key, n in retry_attempts.most_common(top)
        ],
        "retry_after_error_count": n_retry_after_error,
        "retry_identical_input_count": n_retry_identical_input,
        "sessions": rows[:top],
    }
    lines = [f"# Friction report ({len(fbs)} sessions)", "", "## Counts by kind"]
    lines += [f"- **{k}**: {v}" for k, v in kind_counts.most_common()]
    lines += ["", "## Top tools by error count"]
    lines += [f"- {tool}: {n}" for tool, n in tool_errors.most_common(top)] or ["- none"]
    lines += ["", "## Top retried digests"]
    lines += [
        f"- {key} — {n} attempts "
        f"({retry_after_error.get(key, 0)} after error, "
        f"{retry_identical_input.get(key, 0)} identical-input)"
        for key, n in retry_attempts.most_common(top)
    ] or ["- none"]
    lines += [
        (
            f"- retries after error: {n_retry_after_error} · "
            f"identical-input repeats: {n_retry_identical_input}"
        )
    ]
    lines += [
        "",
        "## Sessions ranked by friction",
        "",
        "| session | title | findings | top kind |",
        "|---|---|---|---|",
    ]
    for r in rows[:top]:
        top_kind = max(r["kinds"], key=r["kinds"].get) if r["kinds"] else ""
        lines.append(
            f"| `{r['session_id'][:8]}` | {r['title']} | {r['n_findings']} | {top_kind} |"
        )
    return agg, "\n".join(lines)


def _rewrites_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    groups: dict[str, dict] = {}
    n_scripts = 0
    for sid, findings in fbs.items():
        for f in _kinds_of(findings, "inline_script"):
            n_scripts += 1
            ev = f.get("evidence") or {}
            fp = ev.get("fingerprint", "")
            g = groups.setdefault(
                fp,
                {
                    "first_line": ev.get("first_line", ""),
                    "language": ev.get("language", ""),
                    "sessions": set(),
                    "n": 0,
                },
            )
            g["sessions"].add(sid)
            g["n"] += 1
    dup = {fp: g for fp, g in groups.items() if len(g["sessions"]) >= 2}
    ranked = sorted(dup.items(), key=lambda kv: len(kv[1]["sessions"]), reverse=True)
    agg = {
        "n_scripts": n_scripts,
        "n_fingerprints": len(groups),
        "n_repeated_fingerprints": len(dup),
        "groups": [
            {
                "fingerprint": fp,
                "n_sessions": len(g["sessions"]),
                "n_occurrences": g["n"],
                "language": g["language"],
                "example_first_line": g["first_line"],
                "sessions": sorted(g["sessions"]),
            }
            for fp, g in ranked[:top]
        ],
    }
    lines = [
        (
            f"# Rewrites report ({len(fbs)} sessions, {n_scripts} scripts, "
            f"{len(dup)} rewritten ≥2x)"
        ),
        "",
        "| fingerprint | sessions | occurrences | language | example |",
        "|---|---|---|---|---|",
    ]
    for fp, g in ranked[:top]:
        lines.append(
            f"| `{fp}` | {len(g['sessions'])} | {g['n']} | {g['language']} | "
            f"`{g['first_line'][:60]}` |"
        )
    if not ranked:
        lines.append("| - | - | - | - | no fingerprint repeated across sessions yet |")
    return agg, "\n".join(lines)


def _timeline_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    rows = []
    for sid, findings in fbs.items():
        for f in _kinds_of(findings, "session_timeline"):
            ev = f.get("evidence") or {}
            rows.append(
                {
                    "session_id": sid,
                    "date": ev.get("ended_at", ""),
                    "project": ev.get("project", ""),
                    "title": ev.get("title", ""),
                    "prs": ev.get("prs") or [],
                    "n_files": len(ev.get("files_touched") or []),
                }
            )
    rows.sort(key=lambda r: r["date"], reverse=True)
    agg = {"rows": rows[:top]}
    lines = [
        f"# Timeline ({len(rows)} sessions)",
        "",
        "| date | session | project | title | PRs | files |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows[:top]:
        prs = ", ".join(f"#{p.get('number')}" for p in r["prs"] if p.get("number")) or "-"
        lines.append(
            f"| {r['date']} | `{r['session_id'][:8]}` | {r['project']} | {r['title']} | "
            f"{prs} | {r['n_files']} |"
        )
    return agg, "\n".join(lines)


def _cost_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    rows = []
    total_usd = 0.0
    total_tokens = 0
    for sid, findings in fbs.items():
        for f in _kinds_of(findings, "session_cost"):
            ev = f.get("evidence") or {}
            cs = ev.get("cost_state") or {}
            usd = cs.get("totalCostUSD")
            usage_by_model = ev.get("usage_by_model") or {}
            tokens = sum(
                v
                for u in usage_by_model.values()
                for k, v in u.items()
                if k != "api_calls"
            )
            rows.append(
                {
                    "session_id": sid,
                    "title": _title(store, sid),
                    "usd": usd,
                    "tokens": tokens,
                }
            )
            if isinstance(usd, (int, float)):
                total_usd += usd
            total_tokens += tokens
    rows.sort(key=lambda r: r["usd"] or 0, reverse=True)
    agg = {
        "total_usd": round(total_usd, 4),
        "total_tokens": total_tokens,
        "sessions": rows[:top],
    }
    lines = [
        f"# Cost report ({len(fbs)} sessions)",
        "",
        f"Total: ${total_usd:.4f}, {total_tokens:,} tokens",
        "",
        "| session | title | USD | tokens |",
        "|---|---|---|---|",
    ]
    for r in rows[:top]:
        usd = f"${r['usd']:.4f}" if isinstance(r["usd"], (int, float)) else "-"
        lines.append(
            f"| `{r['session_id'][:8]}` | {r['title']} | {usd} | {r['tokens']:,} |"
        )
    return agg, "\n".join(lines)


def _hygiene_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    rows = []
    for sid, findings in fbs.items():
        for f in _kinds_of(findings, "hygiene"):
            rows.append(
                {
                    "session_id": sid,
                    "title": _title(store, sid),
                    **(f.get("evidence") or {}),
                }
            )

    def _median(key: str):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return statistics.median(vals) if vals else None

    medians = {
        k: _median(k)
        for k in (
            "read_edit_ratio",
            "write_share",
            "n_unread_edits",
            "tool_calls_per_turn",
        )
    }
    rows.sort(key=lambda r: r.get("n_unread_edits", 0), reverse=True)
    agg = {"medians": medians, "sessions": rows[:top]}
    med_line = (
        ", ".join(f"{k}={v:.2f}" for k, v in medians.items() if v is not None) or "n/a"
    )
    lines = [
        f"# Hygiene report ({len(fbs)} sessions)",
        "",
        f"Fleet medians: {med_line}",
        "",
        "| session | title | read:edit | write share | unread edits | calls/turn | version |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows[:top]:
        re_ratio = (
            f"{r['read_edit_ratio']:.2f}"
            if isinstance(r.get("read_edit_ratio"), (int, float))
            else "-"
        )
        ws = (
            f"{r['write_share']:.2f}"
            if isinstance(r.get("write_share"), (int, float))
            else "-"
        )
        cpt = r.get("tool_calls_per_turn", 0) or 0
        lines.append(
            f"| `{r['session_id'][:8]}` | {r['title']} | {re_ratio} | {ws} | "
            f"{r.get('n_unread_edits', 0)} | {cpt:.1f} | {r.get('version', '')} |"
        )
    return agg, "\n".join(lines)


def _tooling_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    tools: Counter = Counter()
    mcp: Counter = Counter()
    skills: Counter = Counter()
    subagents: Counter = Counter()
    for findings in fbs.values():
        for f in _kinds_of(findings, "tooling"):
            ev = f.get("evidence") or {}
            tools.update(ev.get("tools") or {})
            mcp.update(ev.get("mcp_tools") or {})
            skills.update(ev.get("skills") or {})
            subagents.update(ev.get("subagents") or {})
    agg = {
        "top_tools": tools.most_common(top),
        "top_mcp_tools": mcp.most_common(top),
        "top_skills": skills.most_common(top),
        "top_subagents": subagents.most_common(top),
    }
    lines = [f"# Tooling report ({len(fbs)} sessions)"]
    for title, counter in (
        ("Top tools", tools),
        ("Top MCP tools", mcp),
        ("Top skills", skills),
        ("Top subagents", subagents),
    ):
        lines += ["", f"## {title}"]
        lines += [f"- {name}: {n}" for name, n in counter.most_common(top)] or ["- none"]
    return agg, "\n".join(lines)


def _stats_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    rows = []
    for sid, findings in fbs.items():
        for f in _kinds_of(findings, "session_stats"):
            ev = f.get("evidence") or {}
            rows.append(
                {
                    "session_id": sid,
                    "title": _title(store, sid),
                    "n_turns": ev.get("n_turns", 0),
                    "n_tool_calls": ev.get("n_tool_calls", 0),
                    "n_errors": ev.get("n_errors", 0),
                    "duration_ms": ev.get("duration_ms", 0),
                }
            )
    rows.sort(key=lambda r: r["n_turns"], reverse=True)
    agg = {"sessions": rows[:top]}
    lines = [
        f"# Stats report ({len(fbs)} sessions)",
        "",
        "| session | title | turns | tool calls | errors | duration (s) |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows[:top]:
        lines.append(
            f"| `{r['session_id'][:8]}` | {r['title']} | {r['n_turns']} | {r['n_tool_calls']} | "
            f"{r['n_errors']} | {r['duration_ms'] / 1000:.0f} |"
        )
    return agg, "\n".join(lines)


def _generic_report(fbs: dict, store: Store, top: int) -> tuple[dict, str]:
    kind_counts: Counter = Counter()
    for findings in fbs.values():
        for f in findings:
            kind_counts[f.get("kind", "")] += 1
    agg = {"kind_counts": dict(kind_counts), "n_sessions": len(fbs)}
    lines = [f"# {len(fbs)} sessions", ""]
    lines += [f"- **{k}**: {v}" for k, v in kind_counts.most_common()] or [
        "- no findings"
    ]
    return agg, "\n".join(lines)


#: One reporter per lens; a lens without an entry gets :func:`_generic_report`.
_REPORTERS: dict[str, Callable[[dict, Store, int], tuple[dict, str]]] = {
    "friction": _friction_report,
    "rewrites": _rewrites_report,
    "timeline": _timeline_report,
    "cost": _cost_report,
    "hygiene": _hygiene_report,
    "tooling": _tooling_report,
    "stats": _stats_report,
}


def _build(
    lens_name: str, findings_by_session: dict, *, store: Store, top: int
) -> tuple[dict, str]:
    reporter = _REPORTERS.get(lens_name, _generic_report)
    return reporter(findings_by_session, store, top)


def aggregate(
    lens_name: str, findings_by_session: dict, *, store: Store, top: int = 20
) -> dict:
    """The JSON-able cross-session aggregate for one lens."""
    agg, _ = _build(lens_name, findings_by_session, store=store, top=top)
    return agg


def render(
    lens_name: str, findings_by_session: dict, *, store: Store, top: int = 20
) -> str:
    """The markdown report for one lens, built from :func:`aggregate`'s data."""
    _, text = _build(lens_name, findings_by_session, store=store, top=top)
    return text
