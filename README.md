# astern

`crowsnest` watches the sessions running right now; `astern` looks back at the wake they left — the transcripts Claude Code already keeps under `~/.claude/projects/`. It mines them for what problems recur, where agents get stuck, what one-off code keeps being rewritten, and which words you and your agents don't share, without spending a token twice.

## Start here

```bash
pip install astern

astern sync                        # read new or changed transcripts, run the free (heuristic) lenses
astern sessions                    # what's synced, newest first
astern show <sid-prefix>           # one session: meta, ledger state, last turns, findings
astern report friction             # (in progress) a lens's findings as a markdown report
astern judge                       # (in progress) run the LLM-judged lenses over what's synced
astern estimate                    # (in progress) token/cost estimate before a judge batch runs

pip install "astern[recall]"       # + ir, for searching what past sessions already know
astern index                       # index the store into ir corpora (idempotent)
astern recall "what did we decide about the ledger"    # ...and ask it
astern install-skills              # link the shipped skills into ~/.claude/skills
```

`sync`, `sessions` and `show` are shipped today. `report`, `judge` and `estimate` are being built out by other agents against the same store and ledger; once they land, the one-command loop is `astern sync && astern judge && astern report friction`.

## What it extracts

Each **lens** asks one question of a session and returns typed, evidence-backed findings — every finding points back at the turn it came from. Method: **H** = deterministic heuristic, zero tokens; **L** = LLM-judged, spends subscription tokens via the local `claude` CLI; **H→L** = a heuristic shortlist that only the survivors get judged on.

| Lens | Question | Method |
|---|---|---|
| `stats` | The shape of a session in numbers — the cost model's own features | H |
| `problems` | What problem was each session solving, and how was it solved? | L |
| `recurring` | Which problems recur, and would a skill or subagent pay for itself? | H over L |
| `rewrites` | What one-off code keeps being rewritten? | H→L |
| `friction` | Where do agents get stuck, and what would remove the obstacle? | H→L |
| `jargon-in` | Which of my phrasings point to an established term I don't use? | L |
| `jargon-out` | Which terms do agents use that I don't? | H→L |
| `corrections` | What did I correct or re-affirm, and which corrections repeat? | H→L |
| `prompt-quality` | Which prompt shapes lead to clean turns, which to flailing ones? | H→L |
| `cost` | Where do the tokens and hours go, by project / task / model? | H |
| `timeline` | What did I work on this week, and what landed? | H→L |
| `decisions` | Which decisions were made, and why? | L |
| `claims` | Where did an agent claim "done" and a later turn show otherwise? | H→L |
| `questions` | What do agents keep asking me, and what did I answer? | H→L |
| `tooling` | Which skills/subagents/MCP tools are used, unused, or missing? | H |
| `memory-candidates` | Which restated facts should become memory, and where does memory drift? | H→L |
| `evals` | Which (prompt, outcome) pairs make a regression test for a skill? | H |
| `hygiene` | Do agents read before they edit, and does that change over time? | H |
| `regression` | Did the harness or model change behaviour? | H |
| `adherence` | Which CLAUDE.md/skill rules are followed, ignored, or fought? | L |
| `subagents` | Which subagent / `Workflow` spawns paid for themselves? | H |
| `effect` | Did an adopted rule, skill or function reduce the pattern it targeted? | H |

Session handoffs (`Q`) are deliberately not a lens here — that's `openloops`, linked rather than re-derived. The full catalogue, with the signal each lens reads and the sink it feeds, is plan §2 (see "The seams" below).

## Recall — what past sessions already know

`astern recall "<query>"` answers *what did we already think, try and decide about X* without re-reading a transcript. astern is only the **record source** here: `ir` owns the indexing and the search, and the multi-hop search loop belongs to `raglab`. Install the extra (`pip install "astern[recall]"`), run `astern index` after a `sync`, and ask.

Two grains, indexed as two `ir` corpora because they answer different questions:

| Corpus | One record is | Best for |
|---|---|---|
| `session_synopses` | a whole session as the `synopsis` lens distilled it — goal, problems and solutions, decisions, corrections | **what was decided** |
| `session_turns` | one turn: the prompt and the assistant's closing text | **what was actually tried** |

Unfiltered, a recall also reaches the `skills` and `reports` corpora when this machine has them (weighted below the sessions, so they inform rather than crowd out). `--project X` and `--since-days N` are hard metadata filters; a project is resolved *through the store* into its session ids, because a session about a project often runs from a group dir or a worktree whose name says nothing about it.

Embedding is local and offline: ir's `all-MiniLM-L6-v2` by default — **no API key, no per-query cost** — with `--embedder light` (numpy-only hashing) for a build with no model download. Each hit carries a `pointer`, the `astern show` command that fetches the full record: a hit is an address, not an answer. The shipped `astern-recall` skill drives the whole loop, ending in a briefing written to the repo the question was about.

`episodes` — a third grain, consecutive turns on one topic — is deliberately not built yet (issue #7).

## Never spending a token twice

Every lens run goes through the **ledger**: one entry per session, keyed on the transcript file's fingerprint (size + mtime) and, per lens, its version and the last turn index it has seen. From that, `ledger.plan()` picks one of three actions:

- **skip** — this lens version already covered every turn that exists in the transcript.
- **incremental** — the session grew (it was resumed); only lenses that declare `incremental=True` get handed just the new turns (`from_index` onward) plus their own prior findings, and extend rather than redo.
- **full** — never analyzed, or analyzed by an older lens version; everything is reprocessed.

A heuristic lens (`kind='H'`) costs nothing, so paying for `full` on every source change is fine. The ledger earns its keep on the LLM lenses (`kind='L'`): a resumed session must not pay again for turns it already paid for, and `astern sync` never re-reads a transcript whose size and mtime it already has on file.

## Where things live

The store is `dol`-backed JSON files under `~/.local/share/astern/` (`sessions`, `turns`, `findings`, `ledger`, `judgments`) — override with `$ASTERN_DATA_DIR` to keep an experiment out of the real store. The transcripts it reads come from `~/.claude` by default — override with `$ASTERN_HOME` to point at a second account or a synced copy of another machine's home. Both are seams: any `MutableMapping` serves as a store, and `homes()` takes a single dir, an iterable of dirs, or `None`.

## The seams

Five things in astern are deliberately swappable, each with a strong out-of-the-box default and a named replacement:

| Seam | v1 default | Replacement |
|---|---|---|
| `home=` — which `~/.claude`-shaped dirs to read | the one `~/.claude` | a second account, or the server's home over a sync |
| `store=` — where records and findings live | `dol` JSON files under `~/.local/share/astern/` | any `MutableMapping` — S3, SQLite, a dict for tests |
| `judge=` — the LLM callable for `L` lenses | the local `claude` CLI, headless, no session persisted | `aix.prompt_func` for API billing; a recorded-replay judge for tests |
| `similar=` — how findings group across sessions | normalized-string near match | `ir` corpus + embeddings, for real semantic clustering |
| `turns=` — the transcript-to-turns fetcher | astern's own `openloops`-backed iterator | `priv.claude_transcripts.turn_pair_records` by injection |
| `embedder=` — how `astern index` embeds | `ir`'s local `all-MiniLM-L6-v2` (offline, no key) | `light` (numpy-only), or any embedder spec `ir` understands |

What's deliberately **not** a seam: the lens registry (a module-level dict), report templates, the finding dict shape, and the `openloops.egress` scrub step on every sink — none of these are meant to vary.

## Not in astern

- **Live sessions** — that's `crowsnest`'s axis (*this minute*), not astern's (*the wake*).
- **Obligation tracking** — what's owed, blocked, or open — that's `openloops`.
- **Transcript viewers, full-text search, cost dashboards** — `claude-code-log`, `cc-transcript`, `episodic-memory` and `ccusage` already do these well; install them alongside astern rather than rebuilding them here.
- **Writing into `CLAUDE.md`, skills, or memory.** Every lens emits *candidates* — mined skill/rule/function/memory suggestions — for a human to accept through `skill`, `opsward`, `coact`, or `ge.memory`. Nothing here writes automatically; that's the same "report, never repair" invariant `priv upkeep` carries.

## Development

```bash
pip install -e ".[dev]"
pytest -q                            # the test suite
pytest --doctest-modules astern      # every module's own doctest
```
