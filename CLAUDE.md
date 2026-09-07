# astern — dev notes for the agent working on this repo

`astern` mines past Claude Code sessions (the JSONL under `~/.claude/projects/`) so future sessions burn fewer tokens on the same problems. The full plan and prior-art research live in the `ai` group docs (`$PP/t/priv/data/groups/ai/docs/session-mining-plan.md` and `session-mining-research.md`). Read the plan's §2 (lens catalogue) and §3 (seam table) before adding a lens.

## Shape

```
sources.py   homes + session files + raw records         (home= seam; default ~/.claude)
turns.py     raw records → one dict per user→assistant turn, + session_meta()
store.py     dol JsonFiles under ~/.local/share/astern/{sessions,turns,findings,ledger,judgments}   (store= seam)
ledger.py    idempotency: per session × lens → skip | incremental | full
lenses/      registry (LENSES, @lens, finding()) + one module per lens
judge.py     the LLM seam: claude_judge() = headless `claude -p --bare --output-format json`; replay_judge() for tests
recall.py    the record source for search: store → ir corpora (`session_synopses`, `session_turns`), then ir's discover  (embedder= seam)
skills.py    the skills the package ships (astern/data/skills/) and the installer that links them into ~/.claude
tools.py     SSOT of verbs (sync, sessions, show, lenses, index, recall, …) — plain functions, JSON in / dict out; __main__ is cw over _dispatch_funcs
```

## Invariants (each one is load-bearing)

- **Heuristics first, zero tokens.** A `kind='H'` lens must never call a judge. LLM lenses are `kind='L'`, take `judge=` from `ctx`, and attach the judge's `usage` to every finding they return, because that is how the ledger and the cost model learn what a session cost.
- **Never spend a token twice.** Every lens run goes through `ledger.plan()`. An `L` lens should declare `incremental=True` and, when given `from_index` and `prior`, extend its previous result from the new turns only.
- **Evidence or it did not happen.** Every finding points back at turns (`finding(..., turn=turn)` fills `turn_index`/`turn_uuid`); a claim about a pattern without the turns behind it is worthless.
- **Report, never repair.** Sinks emit candidates (rules, skills, functions, memories) for a human to accept. Nothing writes into CLAUDE.md, skills, or memory.
- **The judge does not grow the corpus.** `claude_judge` runs with `--no-session-persistence`; a miner that writes sessions into `~/.claude/projects` mines itself next week.
- **Anything leaving the machine passes `openloops.egress.scrub`** (home paths rewritten, credentials raise). Reports that stay in the store are exempt.
- **Tolerant parsing.** Unknown record types are kept; every derived record can say which Claude Code `version` wrote its source.
- **Nothing in `tools.py` prints or exits.** The CLI is `cw`; MCP would be `py2mcp` string refs to the same functions.

## Conventions

- Python ≥ 3.10, keyword-only args from the 2nd or 3rd position, small helpers (`_prefixed` when module-private, inner when single-caller). Functional over OOP; dataclasses for data.
- Every module has a docstring with a doctest that runs (`pytest --doctest-modules astern`). Tests in `tests/`, on fixture transcripts under `tests/fixtures/` (small, synthetic, secret-free — never copy a real transcript into the repo).
- Dependencies: `openloops`, `dol`, `cw` only. `ir` is an optional extra (`astern[recall]`) for clustering *and* for `index`/`recall` — import it lazily, never at module scope; `aix` is not a dependency (the judge is the `claude` CLI).
- **astern is the record source, not a search engine.** Indexing and retrieval belong to `ir` (add what is missing *there*, as `from_records` was); the multi-hop search loop belongs to `raglab`. `astern.recall` is the seam between them and holds no ranking logic of its own.
- Commit messages: Conventional Commits, no AI attribution.
- `$ASTERN_DATA_DIR` overrides the store for experiments; `$ASTERN_HOME` overrides the transcript home.

## Trying it on real data

```bash
export ASTERN_DATA_DIR=/tmp/astern_scratch     # keep experiments out of the real store
astern sync --max-sessions 5                  # the 5 most recent sessions
astern sessions; astern show <sid-prefix>
```

The real store is `~/.local/share/astern/`; the real corpus on this machine is ~290 top-level sessions and ~4,500 subagent/workflow transcripts (~600 MB), retained for ten years, thinking blocks not persisted.
