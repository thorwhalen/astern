# astern.tools

The SSOT of what astern does: plain functions, JSON-able in, JSON-able dict out.

Every surface — the `astern` CLI (`cw` over `_dispatch_funcs`), a future MCP
server (`py2mcp` over string refs to these names), a skill’s prose — wraps these
functions and nothing else. Nothing here prints or exits; nothing here knows what
called it.

The one-command test of v1: `astern sync && astern report friction`.

### Functions

| [`entire_enable`](#astern.tools.entire_enable)([repo, push_sessions, ...])          | Enable the Entire CLI's Claude Code hooks in `repo`, safe by default.                 |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| [`entire_status`](#astern.tools.entire_status)([repo])                              | Is Entire installed and enabled in `repo`, and is it safe? Read-only.                 |
| [`estimate`](#astern.tools.estimate)(\*[, store, lens, model, home])           | Fit the cost model on what has been judged, then price what has not.                  |
| [`index`](#astern.tools.index)([grain, store, embedder, refresh])           | Index the synced store into `ir` corpora, so `recall` can search it.                  |
| [`install_skills`](#astern.tools.install_skills)(\*[, target, only, force, dry_run]) | Link the skills astern ships into an agent host (default `~/.claude`).                |
| [`judge`](#astern.tools.judge)([lens, session_id, max_sessions, ...])       | Run an LLM lens over the synced sessions, newest first, under the ledger.             |
| [`lenses`](#astern.tools.lenses)()                                           | The registered lenses: name, kind (H = heuristic, L = LLM-judged), version, one line. |
| [`recall`](#astern.tools.recall)(query, \*[, grains, project, ...])          | What past sessions already thought, tried and decided about `query`.                  |
| [`report`](#astern.tools.report)(lens, \*[, store, project, ...])            | Cross-session report for one lens, over what has already been synced.                 |
| [`sessions`](#astern.tools.sessions)(\*[, store, project, limit])              | List synced sessions, newest first, with title, project, turns, and lens coverage.    |
| [`show`](#astern.tools.show)(session_id, \*[, store, turns])               | One session: its meta, ledger entry, and the last `turns` turns (prompt + summary).   |
| [`sync`](#astern.tools.sync)([home, since_days, projects, ...])            | Read new or changed transcripts into the store and run the heuristic lenses.          |
| [`why`](#astern.tools.why)(target, \*[, line, commit, store, repo, ...])  | Why does this line exist? — the commit, the session, and the turn that wrote it.      |

### astern.tools.entire_enable(repo='.', , push_sessions=False, telemetry=False, dry_run=False)

Enable the Entire CLI’s Claude Code hooks in `repo`, safe by default.

Runs `entire agent add claude-code`, then turns off Entire’s own (opt-in)
defaults unless told otherwise: `push_sessions=False` (the default here) stops
the `pre-push` hook from pushing `refs/entire/checkpoints/*` — verbatim
transcripts, absolute paths unscrubbed — to origin on every `git push`;
`telemetry=False` stops PostHog reporting. See [`astern.entire.entire_enable()`](astern.entire.md#astern.entire.entire_enable).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.entire_status(repo='.')

Is Entire installed and enabled in `repo`, and is it safe? Read-only.

See [`astern.entire.entire_status()`](astern.entire.md#astern.entire.entire_status) for the full field list, including
`risk` — set when checkpoints will be pushed on the next `git push`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.estimate(, store=None, lens='synopsis', model='haiku', home=None)

Fit the cost model on what has been judged, then price what has not.

Two prices, each saying which proxy it used: the *pending* one is exact about
its input (the views are built from the store), the *corpus* one multiplies file
bytes by the measured view-chars-per-byte ratio, because a session that was
never synced has no view to measure.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.index(grain='all', , store=None, embedder='default', refresh=False)

Index the synced store into `ir` corpora, so `recall` can search it.

Two corpora, one per grain: `session_synopses` (one record per session,
from the `synopsis` findings) and `session_turns` (one per turn).
Idempotent — `ir` embeds only what changed since the last run, so this is
the natural thing to run after every `astern sync`.

`embedder` is `ir`’s spec: the default is its local `all-MiniLM-L6-v2`
(offline, no API key, no per-query cost); `light` is ir’s numpy-only
hashing embedder (no model download, much weaker semantics). Changing it
re-embeds the corpus.

Needs the optional extra: `pip install "astern[recall]"`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.install_skills(, target=None, only=None, force=False, dry_run=False)

Link the skills astern ships into an agent host (default `~/.claude`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.judge(lens='synopsis', session_id=None, , max_sessions=None, since_days=None, projects=None, model='haiku', effort=None, max_chars=None, strict_schema=False, workers=1, store=None, dry_run=False, judge_fn=None)

Run an LLM lens over the synced sessions, newest first, under the ledger.

`workers` runs that many judge calls at once (each is its own `claude`
process; a call takes 30–150 s, so a 280-session batch is hours serial and
well under one hour at 8). Every session writes only its own store keys, and
the ledger is per session, so concurrent runs never contend.

Idempotent by construction: a session this lens version already covered is
`skip` and costs nothing; a session that was resumed is `incremental` and
pays for its new turns only. `dry_run` builds every view and prices the batch
from the fitted cost model without calling the judge once — run it before a
batch, not after.

`strict_schema` (default `False`, see [`astern.judge`](astern.judge.md#module-astern.judge)) passes the
schema to the CLI as `--json-schema`; a rejected answer then costs a CLI-side
retry (the whole conversation resent). The default embeds the schema in the
prompt instead and parses loosely, trading strict validation for a predictable,
single-pass bill.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.lenses()

The registered lenses: name, kind (H = heuristic, L = LLM-judged), version, one line.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.recall(query, , grains='all', project=None, since_days=None, k=8, mode=None, store=None)

What past sessions already thought, tried and decided about `query`.

Runs `ir`’s `discover` across the astern corpora (and, unfiltered, the
`skills` / `reports` corpora when this machine has them) and returns a
few high-precision hits, each with the `pointer` — an `astern show`
command — that fetches the full record. Reading those is the caller’s job:
a hit is an address, not an answer.

Needs `astern index` to have run at least once.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.report(lens, , store=None, project=None, since_days=None, top=20, fmt='md')

Cross-session report for one lens, over what has already been synced.

`fmt='md'` (default) puts a markdown report in `text`; `fmt='json'` puts
the same underlying aggregate dict there instead.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.sessions(, store=None, project=None, limit=50)

List synced sessions, newest first, with title, project, turns, and lens coverage.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.show(session_id, , store=None, turns=5)

One session: its meta, ledger entry, and the last `turns` turns (prompt + summary).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.sync(home=None, , since_days=None, projects=None, max_sessions=None, kinds='session', lenses='H', force=False, store=None)

Read new or changed transcripts into the store and run the heuristic lenses.

Idempotent: a transcript whose size and mtime the ledger already knows is not
re-read; a lens that already covered every turn is not re-run (`force` re-runs
the lenses, never the LLM ones — those go through [`judge()`](#astern.tools.judge)). `lenses` is
`'H'` (all heuristic lenses), `'none'`, or a comma-separated list of names.

`kinds` defaults to `'session'` — top-level transcripts only — on measurement,
not on principle. [`why()`](#astern.tools.why) genuinely needs `'session,subagent'` (a delegating
session’s own turns never contain the code its subagents wrote), but on this
machine’s corpus (290 top-level sessions, 4547 nested transcripts, 2.1 GB of
JSONL) that is 50.7 s and a 330 MB store — 442 MB on disk, because 36,000 small
JSON files round up hard — against 7.6 s and 87 MB for sessions alone. Too big to
impose on every `sync`, so [`why()`](#astern.tools.why) says when it needs it instead.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.tools.why(target, , line=None, commit=False, store=None, repo=None, window_days=14.0, max_hits=10, entire=True)

Why does this line exist? — the commit, the session, and the turn that wrote it.

`target` is `<file>:<line>` (a file plus `--line` works too) or, with
`--commit`, a commit-ish. A target that names no readable file and *does*
resolve as a revision is read as a commit without the flag, so
`astern why 589f17a` and `astern why HEAD --commit` are the same question:
the commit’s trailer names the session, and the hits are that session’s tool
calls against the files the commit touched.

(`target` is positional and therefore required, which is why `--commit` is a
flag over it rather than an option carrying the sha: argh’s grammar — the one
`cw` reproduces — makes any parameter with a default an *option*, and
`astern why -t astern/judge.py:115` is the wrong headline.)

Two commits are reported, never one: `blame` is the last hand to touch the line
(a CI `ruff format` pass owns a lot of them) and `introduced` is the commit
whose diff first contained the text. When they differ, `introduced` is the one
whose `Claude-Session:` trailer is worth following.

When the Entire CLI is installed and this repo is enabled, its generation-time
answer is included verbatim under `entire`; astern’s own retroactive chain runs
either way, because Entire only speaks for work done after it was enabled.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
