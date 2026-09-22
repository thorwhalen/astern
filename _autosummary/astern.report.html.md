# astern.report

Cross-session reports: one aggregator + markdown renderer per lens.

Every heuristic lens produces findings *per session*; this module is where the
fleet-wide answer comes from — what recurs, what’s expensive, what’s newest. Each
lens has its own reporter function (`kind` → shape → question, so the aggregate
worth showing differs per lens); a lens with no dedicated reporter falls back to
`_generic_report()`, a plain count of finding kinds, so a new lens is never
reportless.

[`aggregate()`](#astern.report.aggregate) returns the JSON-able dict; [`render()`](#astern.report.render) formats it as markdown.
Both take the same `findings_by_session` shape astern.tools builds:
`{session_id: [finding, ...]}`.

```pycon
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
```

### Functions

| [`aggregate`](#astern.report.aggregate)(lens_name, findings_by_session, \*, ...)   | The JSON-able cross-session aggregate for one lens.                                                               |
|-------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`render`](#astern.report.render)(lens_name, findings_by_session, \*, store)    | The markdown report for one lens, built from [`aggregate()`](#astern.report.aggregate)'s data. |

### astern.report.aggregate(lens_name, findings_by_session, , store, top=20)

The JSON-able cross-session aggregate for one lens.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.report.render(lens_name, findings_by_session, , store, top=20)

The markdown report for one lens, built from [`aggregate()`](#astern.report.aggregate)’s data.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
