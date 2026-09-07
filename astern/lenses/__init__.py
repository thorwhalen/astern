"""Lenses: one question asked of a session, answered as a list of findings.

A lens is a plain function ``(session: dict, turns: list[dict], **ctx) -> list[dict]``
registered with :func:`lens`. ``kind`` is ``'H'`` (heuristic, zero tokens) or ``'L'``
(LLM-judged; receives ``judge=`` in ``ctx`` and must return findings *and* record the
judge's usage on them). ``incremental=True`` means the lens can be given only the
turns from ``ctx['from_index']`` onward plus its own previous findings (``ctx['prior']``)
and extend rather than redo — the ledger only offers that to lenses that say so.

A finding is a JSON-able dict made by :func:`finding`. It always carries evidence
that points back into the transcript (session, turn index and uuid, tool-use id),
because a claim about a pattern is worth nothing without the turns it came from.

>>> @lens('demo', version=1)
... def demo(session, turns, **ctx):
...     return [finding('demo', session, kind='hello', evidence={'n': len(turns)})]
>>> LENSES['demo'].kind, demo({'session_id': 's'}, [])[0]['evidence']
('H', {'n': 0})
>>> _ = LENSES.pop('demo')
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from typing import Callable

FINDING_VERSION = 1


@dataclass(frozen=True)
class Lens:
    name: str
    version: int
    kind: str  # 'H' | 'L'
    fn: Callable[..., list[dict]]
    incremental: bool
    doc: str

    def __call__(self, session: dict, turns: list[dict], **ctx) -> list[dict]:
        return self.fn(session, turns, **ctx)


LENSES: dict[str, Lens] = {}


def lens(name: str, *, version: int = 1, kind: str = "H", incremental: bool = False):
    """Register a lens function under ``name``. Bump ``version`` when its output changes."""

    def deco(fn):
        LENSES[name] = Lens(name=name, version=version, kind=kind, fn=fn, incremental=incremental,
                            doc=(fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
        return fn

    return deco


def finding(lens_name: str, session: dict, *, kind: str, evidence: dict, turn: dict | None = None,
            label: str | None = None, text: str = "", **extra) -> dict:
    """Make one finding. ``turn`` (a turn record) fills the evidence pointer."""
    ev = dict(evidence)
    if turn is not None:
        ev.setdefault("turn_index", turn.get("index"))
        ev.setdefault("turn_uuid", turn.get("uuid"))
        ev.setdefault("at", turn.get("timestamp"))
    out = {
        "lens": lens_name,
        "lens_version": LENSES[lens_name].version if lens_name in LENSES else None,
        "finding_version": FINDING_VERSION,
        "session_id": session.get("session_id", ""),
        "project": session.get("project", ""),
        "home": session.get("home", ""),
        "kind": kind,
        "label": label,
        "text": text,
        "evidence": ev,
        "found_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out.update(extra)
    return out


#: Built-in lens modules, imported on demand so that registering is one import away
#: and a broken optional lens cannot take the registry down with it.
BUILTIN_MODULES = ("stats", "friction", "commands", "timeline", "cost", "hygiene", "tooling")


def load_builtin_lenses(modules=BUILTIN_MODULES) -> dict[str, Lens]:
    for m in modules:
        import_module(f"{__name__}.{m}")
    return LENSES


def lenses_of_kind(kind: str | None = None) -> list[Lens]:
    load_builtin_lenses()
    return [lens_ for lens_ in LENSES.values() if kind is None or lens_.kind == kind]
