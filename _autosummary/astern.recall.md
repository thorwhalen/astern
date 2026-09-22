# astern.recall

Recall: what did past sessions already think, try and decide about X?

astern is the **record source**; `ir` owns indexing and search; the multi-hop
loop belongs to `raglab`. This module is that seam, and nothing more: it
renders the store into JSON-able records ([`records()`](#astern.recall.records)), registers them with
`ir` as named corpora and builds them ([`index()`](#astern.recall.index)), and asks `ir` to
search them ([`recall()`](#astern.recall.recall)).

Three grains, deliberately separate corpora rather than one mixed pile:

- `session_synopses` — one record per session, from the `synopsis` lens’s
  findings: goal, problems and their solutions, decisions, corrections, rendered
  as short prose. Small, LLM-distilled, and the best hit for *what was decided*.
- `session_turns` — one record per turn (prompt + the assistant’s closing
  text) of a top-level session (`kind == "session"`). Free, fine-grained, and
  the best hit for *what was actually tried*; this is the shape `ir`’s
  `ir.ClaudeTurn` strategy already indexes.
- `subagent_turns` — the same shape as `session_turns`, but for the
  transcripts a session delegates to (`kind in ("subagent", "workflow")`).
  A subagent’s “user prompt” is the *parent’s* task instruction, not a human
  asking a question, so unfiltered it duplicates the parent’s own content and
  drowns it out — indexing it as a **separate** corpus keeps that duplication
  from ever reaching `session_turns` while still making it searchable on
  purpose. It answers *what did an agent do*, which is a different question
  from *what did we decide*; [`recall()`](#astern.recall.recall) therefore excludes it by default
  and includes it only when asked (`grains` names it, or
  `include_subagents=True`).

A fourth grain, `episodes` (consecutive turns on one topic), is the natural
unit for “the thinking around X” and is deliberately **not** here — see
thorwhalen/astern#7.

`ir` (and its `ef` / `vd` dependencies) is an optional extra:
`pip install "astern[recall]"`. Every import of it is lazy and inside
`_ir()`, so `import astern` stays as light as it was.

```pycon
>>> from astern.store import MemoryStore
>>> store = MemoryStore()
>>> store.sessions['s1'] = {'session_id': 's1', 'title': 'Fix CI', 'project': 'astern'}
>>> store.turns['s1'] = [{'index': 0, 'uuid': 'u0', 'user_prompt': 'why is CI red?',
...                       'assistant_summary': 'a stale lockfile', 'tools': []}]
>>> [r['id'] for r in records('session_turns', store=store)]
['s1:u0']
```

### Module Attributes

| [`GRAINS`](#astern.recall.GRAINS)             | The grains astern indexes, and the ir corpus name each becomes.                                                                                                                                                                            |
|---------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`SESSION_KINDS`](#astern.recall.SESSION_KINDS)      | A session's own `kind` field (see `astern.sources.KINDS`) selects which grain its turns feed.                                                                                                                                              |
| [`DFLT_RECALL_GRAINS`](#astern.recall.DFLT_RECALL_GRAINS) | The grains [`recall()`](#astern.recall.recall) searches when `grains="all"` (the default) and `include_subagents` is not set — the two that answer "what was decided" / "what was actually tried" by *this* session. |
| [`DFLT_EMBEDDER`](#astern.recall.DFLT_EMBEDDER)      | The embedder every astern corpus is built with unless told otherwise.                                                                                                                                                                      |
| [`DFLT_MODE`](#astern.recall.DFLT_MODE)          | dense + BM25, fused.                                                                                                                                                                                                                       |
| [`SNIPPET_CHARS`](#astern.recall.SNIPPET_CHARS)      | How much of a hit's text a recall result carries.                                                                                                                                                                                          |
| [`COMPANION_CORPORA`](#astern.recall.COMPANION_CORPORA)  | Corpora outside astern that a recall reaches into when they are registered *and* built — the skills you already have, the reports you already wrote.                                                                                       |
| [`COMPANION_WEIGHT`](#astern.recall.COMPANION_WEIGHT)   | Federated rank-fusion weight for the companion corpora.                                                                                                                                                                                    |
| [`METADATA_KEYS`](#astern.recall.METADATA_KEYS)      | Record fields lifted into ir's hard-filter metadata, per grain.                                                                                                                                                                            |
| [`CORPUS_SPECS`](#astern.recall.CORPUS_SPECS)       | grain -> the registry entry `ir` persists for it.                                                                                                                                                                                          |

### Functions

| [`index`](#astern.recall.index)([grain, store, embedder, refresh])   | Register the astern corpora with `ir` and build them.                              |
|---------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`recall`](#astern.recall.recall)(query, \*[, grains, project, ...])  | What past sessions already thought about `query` — `ir` federated over the grains. |
| [`records`](#astern.recall.records)(grain, \*[, store, project, ...])  | Yield the JSON-able records of one `grain`, in the shape `ir` indexes.             |
| [`subagent_turn_records`](#astern.recall.subagent_turn_records)()                    | Every subagent/workflow turn record in the default store — `ir`'s fetcher.         |
| [`synopsis_records`](#astern.recall.synopsis_records)()                         | Every session-synopsis record in the default store — `ir`'s fetcher.               |
| [`synopsis_text`](#astern.recall.synopsis_text)(evidence)                    | The prose a synopsis finding becomes: goal, outcome, problems, decisions.          |
| [`turn_records`](#astern.recall.turn_records)()                             | Every top-level-session turn record in the default store — `ir`'s fetcher.         |

### astern.recall.COMPANION_CORPORA *= ('skills', 'reports')*

Corpora outside astern that a recall reaches into when they are registered
*and* built — the skills you already have, the reports you already wrote.

### astern.recall.COMPANION_WEIGHT *= 0.5*

Federated rank-fusion weight for the companion corpora. Below 1.0 because the
question asked here is *what did past sessions say*: a skill or a report is a
welcome answer, but it should not take half the slots from the sessions on the
strength of a rank it earned in a corpus of a different size.

### astern.recall.CORPUS_SPECS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)]* *= {'session_synopses': {'fetcher': 'astern.recall:synopsis_records', 'kind': 'records', 'metadata_keys': ['session_id', 'title', 'project', 'cwd', 'started_at', 'ended_at', 'timestamp', 'outcome', 'prs', 'n_turns'], 'strategy': {'name': 'Chunked', 'params': {'text_key': 'text'}}}, 'session_turns': {'fetcher': 'astern.recall:turn_records', 'kind': 'records', 'metadata_keys': ['turn_index', 'n_errors', 'n_tool_calls', 'tools', 'files', 'title'], 'strategy': {'name': 'ClaudeTurn', 'params': {'include_full': False}}}, 'subagent_turns': {'fetcher': 'astern.recall:subagent_turn_records', 'kind': 'records', 'metadata_keys': ['turn_index', 'n_errors', 'n_tool_calls', 'tools', 'files', 'title', 'kind', 'parent_id', 'parent_title', 'parent_project'], 'strategy': {'name': 'ClaudeTurn', 'params': {'include_full': False}}}}*

grain -> the registry entry `ir` persists for it. The fetchers are named,
never passed: an entry that held a callable could not be written to disk.

### astern.recall.DFLT_EMBEDDER *= 'default'*

The embedder every astern corpus is built with unless told otherwise.
`ir`’s local `all-MiniLM-L6-v2` — offline after a one-time model
download, **no API key, no per-query cost**. `embedder="light"` selects
ir’s numpy-only hashing embedder instead: zero download (what the tests use),
at the price of dense scores that carry almost no semantics — with `light`
prefer `mode="lexical"`.

### astern.recall.DFLT_MODE *= 'hybrid'*

dense + BM25, fused. Session prose is
long-form, but the queries that matter carry identifiers (“json schema
retries”, “ledger”), which is exactly where the lexical leg earns its keep.

* **Type:**
  Retrieval mode for [`recall()`](#astern.recall.recall)

### astern.recall.DFLT_RECALL_GRAINS *= ('session_synopses', 'session_turns')*

The grains [`recall()`](#astern.recall.recall) searches when `grains="all"` (the default) and
`include_subagents` is not set — the two that answer “what was decided” /
“what was actually tried” by *this* session. `subagent_turns` joins only
when named explicitly or `include_subagents=True`.

### astern.recall.GRAINS *= ('session_synopses', 'session_turns', 'subagent_turns')*

The grains astern indexes, and the ir corpus name each becomes.

### astern.recall.METADATA_KEYS *= {'session_synopses': ('session_id', 'title', 'project', 'cwd', 'started_at', 'ended_at', 'timestamp', 'outcome', 'prs', 'n_turns'), 'session_turns': ('turn_index', 'n_errors', 'n_tool_calls', 'tools', 'files', 'title'), 'subagent_turns': ('turn_index', 'n_errors', 'n_tool_calls', 'tools', 'files', 'title', 'kind', 'parent_id', 'parent_title', 'parent_project')}*

Record fields lifted into ir’s hard-filter metadata, per grain. This is the
JSON-friendly stand-in for a `metadata_of` callable (which a registry entry
could not carry) — see `ir.CorpusSource.from_records`.

### astern.recall.SESSION_KINDS *= frozenset({'session'})*

A session’s own `kind` field (see `astern.sources.KINDS`) selects which
grain its turns feed. Missing/empty defaults to `"session"` — older stores,
and every synthetic test fixture, never set it.

### astern.recall.SNIPPET_CHARS *= 600*

How much of a hit’s text a recall result carries. Enough to judge relevance;
the full record is one `astern show` away, which is what `pointer` says.

### astern.recall.index(grain='all', , store=None, embedder='default', refresh=False)

Register the astern corpora with `ir` and build them. Idempotent.

Registration writes `ir`’s corpora config (`~/.config/ir/corpora.json`);
the build embeds only what changed, because `ir` keys every artifact on a
content hash — so re-running after a `sync` costs the new records only.
`refresh=True` re-registers first, which is how an embedder change takes
effect (`ir` then re-embeds everything, its ledger having pinned the old
embedder id).

`store=` is honoured by pointing `$ASTERN_DATA_DIR` at it for the build,
because the fetcher `ir` calls back is a *name*, resolved in a process
that never saw this call’s arguments.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.recall.recall(query, , grains='all', project=None, since_days=None, k=8, mode=None, store=None, companions=True, include_subagents=False)

What past sessions already thought about `query` — `ir` federated over the grains.

Searches `session_synopses` and `session_turns` (whichever are built) by
default, and — when no project/date filter is set — the `skills` and
`reports` corpora if this machine has them. A filter excludes those two
deliberately rather than silently: they carry no `session_id` or
`timestamp` metadata, so a hard filter would drop every one of their hits
without saying so.

`subagent_turns` (what a delegated agent did, not what was decided) is
excluded by default — a subagent’s “user prompt” is the parent’s task
instruction, and unfiltered it duplicates the parent’s own turns and drowns
them out. Name it in `grains` or pass `include_subagents=True` to search
it too.

Returns hits with a `pointer` — the command that fetches the full record —
because the point is to read the few that matter, not to paste prose here.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.recall.records(grain, , store=None, project=None, since_days=None, include_full=False)

Yield the JSON-able records of one `grain`, in the shape `ir` indexes.

Ids are stable, because they are what the index keys on: `<sid>` for a
synopsis, `<sid>:<turn_uuid>` for a turn. `project` / `since_days`
narrow the *source*; the same filters exist at query time in [`recall()`](#astern.recall.recall),
where they are hard metadata filters and cost nothing to change.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

```pycon
>>> from astern.store import MemoryStore
>>> store = MemoryStore()
>>> store.sessions['s1'] = {'session_id': 's1', 'project': 'astern'}
>>> store.findings['synopsis/s1'] = [
...     {'kind': 'synopsis', 'evidence': {'goal': 'ship recall', 'outcome': 'done'}}]
>>> rec = next(records('session_synopses', store=store))
>>> rec['id'], rec['outcome'], rec['text'].splitlines()[0]
('s1', 'done', 'Goal: ship recall')
```

### astern.recall.subagent_turn_records()

Every subagent/workflow turn record in the default store — `ir`’s fetcher.

Kept as its own corpus (never merged into [`turn_records()`](#astern.recall.turn_records)) so a
subagent’s turns — whose “user prompt” is the parent session’s task
instruction, not a human asking a question — never dilute `session_turns`.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### astern.recall.synopsis_records()

Every session-synopsis record in the default store — `ir`’s fetcher.

A registry entry is JSON, so `ir` holds a *reference* to this function
(`"astern.recall:synopsis_records"`) rather than an object: that is what
lets `ir build session_synopses`, in a process that knows nothing about
this one, rebuild the corpus. `$ASTERN_DATA_DIR` therefore selects the
store here exactly as it does for every other astern verb.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### astern.recall.synopsis_text(evidence)

The prose a synopsis finding becomes: goal, outcome, problems, decisions.

Only the fields that answer *what was decided and what went wrong* — the
session’s vocabulary and skill candidates are for the reports, not for
recall, and padding the embedded text with them dilutes the signal.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
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
```

### astern.recall.turn_records()

Every top-level-session turn record in the default store — `ir`’s fetcher.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
