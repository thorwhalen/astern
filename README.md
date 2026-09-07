# astern

`crowsnest` watches the sessions running right now; `astern` looks back at the wake they left — the transcripts Claude Code already keeps under `~/.claude/projects/`. It mines them for what problems recur, where agents get stuck, what one-off code keeps being rewritten, and which words you and your agents don't share, without spending a token twice.

## Start here

```bash
pip install astern

astern sync                        # read new or changed transcripts, run the free (heuristic) lenses
astern sessions                    # what's synced, newest first
astern show <sid-prefix>           # one session: meta, ledger state, last turns, findings
astern why <file>:<line>           # which session turn wrote this line (see below)
astern report friction             # (in progress) a lens's findings as a markdown report
astern judge                       # (in progress) run the LLM-judged lenses over what's synced
astern estimate                    # (in progress) token/cost estimate before a judge batch runs
```

`sync`, `sessions`, `show` and `why` are shipped today. `report`, `judge` and `estimate` are being built out by other agents against the same store and ledger; once they land, the one-command loop is `astern sync && astern judge && astern report friction`.

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

## Never spending a token twice

Every lens run goes through the **ledger**: one entry per session, keyed on the transcript file's fingerprint (size + mtime) and, per lens, its version and the last turn index it has seen. From that, `ledger.plan()` picks one of three actions:

- **skip** — this lens version already covered every turn that exists in the transcript.
- **incremental** — the session grew (it was resumed); only lenses that declare `incremental=True` get handed just the new turns (`from_index` onward) plus their own prior findings, and extend rather than redo.
- **full** — never analyzed, or analyzed by an older lens version; everything is reprocessed.

A heuristic lens (`kind='H'`) costs nothing, so paying for `full` on every source change is fine. The ledger earns its keep on the LLM lenses (`kind='L'`): a resumed session must not pay again for turns it already paid for, and `astern sync` never re-reads a transcript whose size and mtime it already has on file.

## Tracing a line back to the session

```bash
astern sync --kinds session,subagent            # why needs the subagent transcripts
astern why astern/judge.py:115                  # a line
astern why 589f17a --commit                     # or a whole commit
```

`astern why` answers *which session turn wrote this line*, after the fact, from transcripts you already have. The chain has three links, and each one exists because the link before it is not enough:

1. **git.** `git blame -L` names the *last* commit to touch the line — which, after a CI `ruff format` pass, is the bot. `git log -S` names the commit whose diff first contained the text. Both are reported; when they differ, the second is the one you wanted.
2. **The commit trailer.** `Claude-Session: https://claude.ai/code/session_<id>` names the **claude.ai** session, which is *not* the local transcript's `sessionId`. The join lives in the transcript itself, as a `bridge-session` record (`{"sessionId": "<uuid>", "bridgeSessionId": "cse_<id>"}`), which `astern sync` keeps as `bridge_session_id`. (The live registry under `~/.claude/sessions/<pid>.json` carries the same pair, but dies with the process, so it cannot answer about last month.)
3. **The store.** The session's turns **and its subagents'** are searched for the `Edit` / `Write` / `Bash` call whose input contains the line, whitespace-normalized first and then with quotes and integers flattened, so a reformatted line is still found. Each hit reports session, subagent, turn index, the prompt that led to it, and the assistant's prose around it.

The subagent half is not a refinement — it is usually the whole answer. A session that delegates never has the code in its own turns, so `sync` must have been run with `--kinds session,subagent`; `why` says so in `notes` when it wasn't. That is not the default because on a real corpus (290 sessions, 4547 nested transcripts) it is 50.7 s and a 330 MB store against 7.6 s and 87 MB for sessions alone.

With no trailer (older work, another machine, another account) step 2 has nothing to say and step 3 runs over every session whose `cwd` is inside the repo, inside a date window around the commit. If that still finds nothing, the usual reason is the *other home*: pass `--home ~/.claude-iq` to `sync`, because a second account's transcripts are a different corpus.

### astern and Entire

[Entire CLI](https://github.com/entireio/cli) (MIT) answers the same question from the other end: it hooks into Claude Code as the work happens and writes a **checkpoint** — transcript, prompt, token usage, file attribution — into a git ref, so `entire why file:line` is exact and travels with the repo. astern is the retroactive reader for everything Entire did not record: work done before it was enabled, in repos where it never was, and on the second account. When `entire` is installed and the repo is enabled, `astern why` runs it first and includes its answer verbatim under `entire`; astern's own chain runs regardless.

Two rules if you adopt it:

- **Entire's refs are never pushed.** Checkpoints hold the verbatim transcript, and its redaction is best-effort for secrets and does nothing at all about absolute paths — the `cwd` of every record is in there. Entire pushes them by default: it installs a `pre-push` hook that pushes `refs/entire/checkpoints/*` to the elected remote alongside your own push (no `remote.*.push` refspec is involved, so `git config --get-all remote.origin.push` shows nothing and `git push --dry-run` is silent about it). Turn it off per repo with `entire configure --skip-push-sessions`, which writes `strategy_options.push_sessions: false` into `.entire/settings.json`.
- **The Claude Code hook is per repo, and the user's own call.** `entire agent add claude-code` writes eight hooks into the **repository's** `.claude/settings.json` and leaves `~/.claude/settings.json` alone. There is no global install; enabling it for every repo is a decision to make once, deliberately, not a side effect.

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
