---
name: astern-recall
description: >-
  Search past Claude Code sessions for what was already thought, tried and
  decided about a topic, and write the answer up as a briefing. Use when the
  user asks "what did we decide about X", "what did I already try for X", "did
  we ever look at X", "why did we do X that way", "catch me up on project X",
  "what happened last time I worked on X", or "before I start on X, what do past
  sessions say" — and use it proactively at the start of work on a project the
  user has not touched in weeks, before re-deriving a decision that a past
  session already made. Runs astern's mined session store (sync → index →
  recall) through the `ir` retrieval substrate; reads the few hits that matter
  with `astern show`; writes `docs/briefings/<date>-<slug>.md` in the repo the
  question is about. Read-only with respect to the sessions themselves.
metadata:
  audience: developers
---

# astern recall — what past sessions already know

Transcripts are the only honest record of why a project is the way it is, and nobody re-reads them. `astern` mines them into a store; this skill turns that store into an answer.

**What it is not**: a chat log search. A hit is an *address*, not an answer — the procedure below ends with you reading the top hits and writing a briefing, because the value is in the synthesis, not in the ranked list.

## Prerequisites (check once, cheap)

```bash
astern sessions --limit 3     # is anything synced at all?
```

If that errors on an import, the retrieval extra is missing: `pip install "astern[recall]"` (it pulls `ir`, `ef`, `vd`). No API key is needed — the default embedder is local and offline.

## Procedure

### 1. Sync and index (both idempotent — run them, don't ask)

```bash
astern sync                   # read new/changed transcripts into the store
astern index                  # build the ir corpora (only what changed is embedded)
```

`sync` reads only transcripts whose size/mtime changed; `index` embeds only records whose content hash changed. Running both costs seconds when nothing moved. Do not skip `index` after a `sync`: an index built yesterday answers confidently about a corpus that no longer matches.

### 2. Recall

```bash
astern recall "how do we keep the miner from re-judging a resumed session"
astern recall "what did we decide about the judge and json schema retries" --project astern
astern recall "vector store backend choice" --project vd --since-days 120 --k 12
```

Flags: `--project` (matched against the session's project/cwd), `--since-days`, `--k` (how many hits), `--grains session_synopses|session_turns` (default both), `--mode dense|lexical|hybrid` (default hybrid).

Two grains answer different questions, and it is worth naming which you want:

| Grain | One record is | Best for |
|---|---|---|
| `session_synopses` | a whole session, LLM-distilled: goal, problems and their solutions, decisions, corrections | **what was decided**, what the session was for |
| `session_turns` | one turn: the prompt and the assistant's closing text | **what was actually tried**, the specific error, the exact command |

Unfiltered, recall also reaches the `skills` and `reports` corpora when this machine has them — often the fastest route to an existing skill or a written-up report. A `--project` or `--since-days` filter deliberately excludes those two (they carry no project or timestamp metadata); the result's `notes` says so.

If `abstained` is true or the hits look unrelated, **reformulate rather than reporting nothing**: try the vocabulary a session would have used (an error message, a file name, a command) and `--mode lexical` for identifier-heavy queries. Two or three query shapes is normal; that loop is your job, not the tool's.

### 3. Read the hits that matter

Every hit carries a `pointer` — run it:

```bash
astern show <session_id>              # the session's meta + its last turns
astern show <session_id> --turns 40   # more of the turn history
```

Read the top 3–6. A synopsis hit tells you a decision was made; the turns tell you what it actually was and whether it stuck. Where they disagree, the turns win — a synopsis is a summary, and later turns may have reversed it.

### 4. Write the briefing

Write to **the repo the question is about** (not astern's), at `docs/briefings/<YYYY-MM-DD>-<slug>.md`. Create the directory if it does not exist. Then tell the user the path.

Rules for the file:

- **No hard-wrapped prose.** One line per paragraph or list item — newlines only where markdown is structural.
- **Cite every claim**: the session id (first 8 chars is enough) and the date. A briefing whose claims cannot be traced back is a rumour.
- **Separate what was DECIDED from what was merely TRIED.** This is the whole point. A decision is something the user stated or ratified; an experiment an agent ran and abandoned is not a decision, however confidently the transcript reads.
- **Say what you did not find.** An absence ("no session discusses the S3 backend") is a finding, and it is what stops the reader from assuming you looked and hid it.

Shape:

```markdown
# <Topic> — what past sessions say

_Briefing written <date> from N sessions between <first> and <last>. Sources: astern session ids, cited inline._

## Decided

- <the decision>, stated in `a1b2c3d4` (2026-07-14) and unchanged since.

## Tried, not settled

- <the approach>, attempted in `e5f6a7b8` (2026-08-02); abandoned after <what happened>.

## Open questions

- <what nobody answered>

## Not found

- <what you searched for and did not find, with the queries you used>
```

## Notes

- **Never paste real transcript content into a public artifact** (an issue, a PR, a committed doc that leaves the machine) without checking it for secrets and absolute home paths. A briefing is a local doc; keep it that way unless the user says otherwise.
- The store is per-machine. A second Claude home (`~/.claude-*`) is just another source to `astern sync`; a session that lives only on another machine is invisible here, and "not found" means "not found on this machine".
- `episodes` — a third grain, consecutive turns on one topic — does not exist yet (thorwhalen/astern#7). Until it does, a topic that spans many turns is best reached through `session_synopses` first, then read in full with `astern show`.
