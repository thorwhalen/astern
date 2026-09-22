# astern.ledger

Idempotency: what has already been analyzed, so no token is spent twice.

One ledger entry per session. It records the source file’s fingerprint (size and
mtime) and, per lens, the lens version and the last turn index the lens has seen.
[`plan()`](#astern.ledger.plan) turns that into one of three actions:

- `skip`        — this lens version already covered every turn that exists.
- `incremental` — the session grew (it was resumed); analyze only the new turns,
  : from `from_index`. Only lenses that declare `incremental=True`
    get this; the rest fall back to `full`.
- `full`        — never analyzed, or analyzed by an older lens version.

A heuristic lens costs nothing, so for it `full` on any source change is fine.
The ledger earns its keep on the LLM lenses, where a resumed session must not pay
again for the turns it already paid for.

```pycon
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
```

### Functions

| `get_entry`(ledger, session_id)                                                                |                                                                            |
|------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`mark`](#astern.ledger.mark)(entry, \*, lens, version, through_index) | Record that `lens` (at `version`) has now seen turns `[0, through_index)`. |
| [`plan`](#astern.ledger.plan)(entry, \*, lens, version, n_turns, ...)  | Decide what a lens has to do for a session, given its ledger entry.        |
| [`source_changed`](#astern.ledger.source_changed)(entry, fingerprint)            | Has the transcript file changed since the ledger last saw it?              |
| `touch_source`(entry, \*, path, home, ...)                                                     |                                                                            |

### Classes

| [`Plan`](#astern.ledger.Plan)(action, from_index, reason)   |    |
|-------------------------------------------------------------------------------------|----|

### *class* astern.ledger.Plan(action, from_index, reason)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### astern.ledger.mark(entry, , lens, version, through_index, through_uuid='', usage=None)

Record that `lens` (at `version`) has now seen turns `[0, through_index)`.

`usage` accumulates across incremental runs so the entry always says what the
session has cost this lens in total.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### astern.ledger.plan(entry, , lens, version, n_turns, incremental)

Decide what a lens has to do for a session, given its ledger entry.

* **Return type:**
  [`Plan`](#astern.ledger.Plan)

### astern.ledger.source_changed(entry, fingerprint)

Has the transcript file changed since the ledger last saw it?

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)
