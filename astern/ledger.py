"""Idempotency: what has already been analyzed, so no token is spent twice.

One ledger entry per session. It records the source file's fingerprint (size and
mtime) and, per lens, the lens version and the last turn index the lens has seen.
:func:`plan` turns that into one of three actions:

- ``skip``        — this lens version already covered every turn that exists.
- ``incremental`` — the session grew (it was resumed); analyze only the new turns,
                    from ``from_index``. Only lenses that declare ``incremental=True``
                    get this; the rest fall back to ``full``.
- ``full``        — never analyzed, or analyzed by an older lens version.

A heuristic lens costs nothing, so for it ``full`` on any source change is fine.
The ledger earns its keep on the LLM lenses, where a resumed session must not pay
again for the turns it already paid for.

>>> e = {}
>>> plan(e, lens='problems', version=1, n_turns=10, incremental=True).action
'full'
>>> mark(e, lens='problems', version=1, through_index=10, through_uuid='u10')
>>> plan(e, lens='problems', version=1, n_turns=10, incremental=True).action
'skip'
>>> p = plan(e, lens='problems', version=1, n_turns=14, incremental=True); p.action, p.from_index
('incremental', 10)
>>> plan(e, lens='problems', version=2, n_turns=14, incremental=True).action
'full'
>>> plan(e, lens='problems', version=1, n_turns=14, incremental=False).action
'full'
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import MutableMapping


@dataclass(frozen=True)
class Plan:
    action: str  # 'skip' | 'incremental' | 'full'
    from_index: int  # first turn index to analyze (0 for full)
    reason: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plan(entry: dict, *, lens: str, version: int, n_turns: int, incremental: bool) -> Plan:
    """Decide what a lens has to do for a session, given its ledger entry."""
    state = (entry.get("lenses") or {}).get(lens)
    if not state:
        return Plan("full", 0, "never analyzed")
    if state.get("version") != version:
        return Plan("full", 0, f"lens version {state.get('version')} → {version}")
    through = int(state.get("through_index", 0))
    if through >= n_turns:
        return Plan("skip", n_turns, "all turns covered")
    if incremental:
        return Plan("incremental", through, f"{n_turns - through} new turns")
    return Plan("full", 0, f"{n_turns - through} new turns, lens is not incremental")


def mark(entry: dict, *, lens: str, version: int, through_index: int, through_uuid: str = "",
         usage: dict | None = None) -> None:
    """Record that ``lens`` (at ``version``) has now seen turns ``[0, through_index)``.

    ``usage`` accumulates across incremental runs so the entry always says what the
    session has cost this lens in total.
    """
    lenses = entry.setdefault("lenses", {})
    prev = lenses.get(lens) or {}
    total = dict(prev.get("usage") or {}) if prev.get("version") == version else {}
    for k, v in (usage or {}).items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    lenses[lens] = {"version": version, "through_index": through_index, "through_uuid": through_uuid,
                    "at": _now(), "runs": (prev.get("runs", 0) if prev.get("version") == version else 0) + 1,
                    "usage": total}


def source_changed(entry: dict, fingerprint: dict) -> bool:
    """Has the transcript file changed since the ledger last saw it?"""
    return (entry.get("source") or {}).get("fingerprint") != fingerprint


def touch_source(entry: dict, *, path: str, home: str, fingerprint: dict, n_turns: int,
                 last_uuid: str) -> None:
    entry["source"] = {"path": path, "home": home, "fingerprint": fingerprint, "synced_at": _now()}
    entry["n_turns"] = n_turns
    entry["last_turn_uuid"] = last_uuid


def get_entry(ledger: MutableMapping, session_id: str) -> dict:
    try:
        return dict(ledger[session_id])
    except KeyError:
        return {}
