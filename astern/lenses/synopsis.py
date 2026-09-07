"""``synopsis``: one judge call per session — what it was for, and what it cost.

The plan's lens A, widened. One structured judgment over the compressed view from
:mod:`astern.views` answers, in a single call, the questions that all need the same
reading of the session: the goal and outcome, the problems and their solutions, the
friction, the corrections, the vocabulary gap in both directions, and the skills and
functions the session suggests. Splitting these into six lenses would mean six
readings of the same text, which is exactly the token waste astern exists to stop.

Two invariants live here rather than in the prompt:

- **A failed judge call is not a covered session.** An error returns a single
  ``judge_error`` finding carrying ``error``, and :func:`astern.tools._run_lens`
  refuses to advance the ledger past a finding that carries one. Marking a session
  covered because the call failed is how a batch quietly skips the sessions it
  could not read.
- **Every call is recorded whole**, under ``judgments/synopsis/<sid>/<from_index>``,
  with the free features next to the usage the judge reported. That pairing is the
  only data :mod:`astern.estimate` has, and it exists only if it is written at the
  moment of the call.

>>> from astern.judge import replay_judge
>>> from astern.store import MemoryStore
>>> turns = [{'index': 0, 'uuid': 'u0', 'user_prompt': 'why is CI red?',
...           'assistant_full': 'A stale lockfile.', 'tools': []}]
>>> store = MemoryStore()
>>> answer = '{"goal": "unred the CI", "outcome": "done", "problems": [], "friction": [],'\\
...          ' "corrections": [], "user_terms": [], "agent_terms": [],'\\
...          ' "skill_candidates": [], "reusable_code": [], "notable_decisions": []}'
>>> out = synopsis({'session_id': 'sid1'}, turns, judge=replay_judge({}, default=answer),
...                store=store)
>>> out[0]['kind'], out[0]['evidence']['goal'], out[0]['usage']['output_tokens'] > 0
('synopsis', 'unred the CI', True)
>>> sorted(store.judgments)
['synopsis/sid1/0']
"""

from __future__ import annotations

from astern.estimate import features as _features
from astern.lenses import finding, lens
from astern.views import session_view, view_stats

LENS_NAME = "synopsis"

#: What the judge is, in one line. Replacing the default system prompt drops ~4k
#: tokens of Claude Code preamble from every call and, because nothing is then
#: prompt-cached, puts the whole cost in ``input_tokens`` where a model can see it.
SYSTEM = (
    "You analyse transcripts of coding sessions between a developer and an AI agent. "
    "You answer only with one JSON object matching the given schema. No prose, no "
    "markdown, no code fences. Unknown or absent: use an empty list or \"unclear\"."
)

_STR = {"type": "string"}


def _objects(props: dict) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": props}}


#: The synopsis schema. Every list field is optional (defaults to empty) and
#: ``outcome`` is a free string, not an enum: in loose (non-strict) mode nothing
#: enforces this on the CLI side anyway, and even in strict mode a schema this
#: forgiving means a slightly-off answer is a slightly-off finding, not a failed
#: call. :func:`_coerce_synopsis` does the real tolerance work in Python — filling
#: missing keys with defaults and dropping list items that are not dict-shaped —
#: so this schema is documentation for the model, not a gate on the judgment.
SCHEMA = {
    "type": "object",
    "properties": {
        "goal": _STR,
        "outcome": _STR,
        "problems": _objects({"problem": _STR, "solution": _STR, "category": _STR}),
        "friction": _objects({"what": _STR, "cause_guess": _STR,
                              "turn_indices": {"type": "array", "items": {"type": "integer"}}}),
        "corrections": _objects({"what_user_said": _STR, "rule_candidate": _STR}),
        "user_terms": _objects({"phrase": _STR, "established_term": _STR, "definition": _STR}),
        "agent_terms": _objects({"term": _STR, "definition": _STR}),
        "skill_candidates": _objects({"name": _STR, "why": _STR, "recurrence_hint": _STR}),
        "reusable_code": _objects({"what": _STR, "suggested_function": _STR}),
        "notable_decisions": {"type": "array", "items": _STR},
    },
}

#: outcome, normalized. Anything the model writes that is not one of these maps to
#: 'unclear' rather than failing the finding.
OUTCOMES = ("done", "partly", "abandoned", "unclear")

#: field -> {sub-key: 'str' | 'int_list'}, the shape :func:`_coerce_item` fills.
_LIST_SPECS: dict[str, dict[str, str]] = {
    "problems": {"problem": "str", "solution": "str", "category": "str"},
    "friction": {"what": "str", "cause_guess": "str", "turn_indices": "int_list"},
    "corrections": {"what_user_said": "str", "rule_candidate": "str"},
    "user_terms": {"phrase": "str", "established_term": "str", "definition": "str"},
    "agent_terms": {"term": "str", "definition": "str"},
    "skill_candidates": {"name": "str", "why": "str", "recurrence_hint": "str"},
    "reusable_code": {"what": "str", "suggested_function": "str"},
}

PROMPT = """\
Read the session below and return the JSON object the schema describes.

Rules:
- goal: one sentence, what the developer was trying to achieve.
- outcome: done | partly | abandoned | unclear.
- problems: concrete obstacles that were hit and how each was resolved (category is
  a short slug such as "ci", "packaging", "api-misuse", "env").
- friction: where the agent got stuck or wasted turns; turn_indices are the turn
  numbers shown in the view.
- corrections: places the developer redirected or contradicted the agent;
  rule_candidate is the durable instruction that would have prevented it, or "".
- user_terms: the developer's own phrasings that have an established name elsewhere.
- agent_terms: terms the agent used that the developer did not.
- skill_candidates / reusable_code: only where the session shows the same work being
  done by hand that a skill or a function would have done.
- Be specific and short. Empty lists are correct answers. Invent nothing.
{prior_note}
JSON only.

---
{view}
"""


@lens(LENS_NAME, version=1, kind="L", incremental=True)
def synopsis(session: dict, turns: list[dict], *, judge=None, from_index: int = 0,
             prior: list[dict] | None = None, store=None, max_chars: int | None = None,
             **ctx) -> list[dict]:
    """What this session was for, what went wrong, and what it suggests building."""
    if judge is None:
        raise ValueError("the synopsis lens needs judge= (see astern.judge)")
    prior_data = _prior_synopsis(prior)
    view = session_view(session, turns, prior=prior_data,
                        **({"max_chars": int(max_chars)} if max_chars else {}))
    note = ("- The session already has a synopsis (above); cover ONLY the new turns and "
            "report what they add.\n" if prior_data else "")
    prompt = PROMPT.format(view=view, prior_note=note)
    judgment = judge(prompt, schema=SCHEMA, system=SYSTEM)
    feats = _features(session, turns, view)
    feats.update(view_stats(view))
    feats["from_index"] = from_index
    _record(store, session, from_index, judgment, feats)
    common = {"usage": dict(judgment.usage), "cost_usd": judgment.cost_usd,
              "model": judgment.model, "view_chars": len(view),
              "prompt_chars": judgment.prompt_chars, "from_index": from_index}
    if judgment.error or not isinstance(judgment.data, dict):
        return [finding(LENS_NAME, session, kind="judge_error",
                        evidence={"from_index": from_index, "view_chars": len(view),
                                  "text": (judgment.text or "")[:300]},
                        text=judgment.error or "judge returned no JSON object",
                        error=judgment.error or "judge returned no JSON object",
                        **{**common, "usage": {}})]
    data = _coerce_synopsis(judgment.data)
    return [finding(LENS_NAME, session, kind="synopsis", evidence=data,
                    label=str(data.get("outcome") or ""),
                    text=str(data.get("goal") or ""),
                    turn=turns[0] if turns else None, **common)]


def _coerce_outcome(value: object) -> str:
    """Normalize the judge's ``outcome`` to one of :data:`OUTCOMES`; anything else is unclear.

    >>> _coerce_outcome('Done'), _coerce_outcome('kinda'), _coerce_outcome(None)
    ('done', 'unclear', 'unclear')
    """
    v = str(value or "").strip().lower()
    return v if v in OUTCOMES else "unclear"


def _coerce_item(item: object, spec: dict[str, str]) -> dict | None:
    """One list item coerced to ``spec``'s shape; ``None`` if it is not usable at all.

    A non-dict item (the model answered with a bare string, say) carries nothing worth
    keeping and is dropped rather than failing the whole call. A dict item keeps its
    fields, missing ones defaulted, so a partially-filled item is still evidence.

    >>> _coerce_item({'problem': 'p', 'solution': 's'}, {'problem': 'str', 'solution': 'str',
    ...                                                  'category': 'str'})
    {'problem': 'p', 'solution': 's', 'category': ''}
    >>> _coerce_item('oops', {'problem': 'str'}) is None
    True
    """
    if not isinstance(item, dict):
        return None
    out: dict = {}
    for key, kind in spec.items():
        v = item.get(key)
        if kind == "int_list":
            out[key] = [int(x) for x in v if isinstance(x, (int, float))] if isinstance(v, list) else []
        else:
            out[key] = str(v) if v is not None else ""
    return out


def _coerce_synopsis(data: dict) -> dict:
    """Fill missing keys with defaults and drop malformed list items — never fail the call.

    >>> out = _coerce_synopsis({'goal': 'x', 'outcome': 'weird',
    ...                        'problems': [{'problem': 'p', 'solution': 's', 'category': 'c'},
    ...                                     'not a dict']})
    >>> out['outcome'], out['problems']
    ('unclear', [{'problem': 'p', 'solution': 's', 'category': 'c'}])
    >>> _coerce_synopsis({})['friction']
    []
    """
    out: dict = {
        "goal": str(data.get("goal") or ""),
        "outcome": _coerce_outcome(data.get("outcome")),
        "notable_decisions": [str(x) for x in (data.get("notable_decisions") or [])
                              if isinstance(x, (str, int, float))],
    }
    for field, spec in _LIST_SPECS.items():
        items = data.get(field)
        coerced = [_coerce_item(i, spec) for i in items] if isinstance(items, list) else []
        out[field] = [i for i in coerced if i is not None]
    return out


def _prior_synopsis(prior: list[dict] | None) -> dict | None:
    """The last real synopsis among a lens's previous findings, if any.

    >>> _prior_synopsis([{'kind': 'judge_error'}, {'kind': 'synopsis', 'evidence': {'goal': 'g'}}])
    {'goal': 'g'}
    >>> _prior_synopsis([]) is None
    True
    """
    for f in reversed(list(prior or [])):
        if f.get("kind") == "synopsis" and isinstance(f.get("evidence"), dict):
            return f["evidence"]
    return None


def _record(store, session: dict, from_index: int, judgment, feats: dict) -> None:
    """Store the raw call next to the free features — the cost model's only data."""
    if store is None or not hasattr(store, "judgments"):
        return
    rec = judgment.as_record()
    rec["features"] = feats
    rec["session_id"] = session.get("session_id", "")
    rec["lens"] = LENS_NAME
    store.judgments[f"{LENS_NAME}/{session.get('session_id', '')}/{from_index}"] = rec
