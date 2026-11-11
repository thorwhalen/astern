---
name: astern-recall
description: >-
  Find past Claude Code sessions, on every machine and account, and say what they
  decided, tried and produced. Use whenever the user refers to earlier work, however
  casually: "we did X", "remember when we", "that old session", "the session where",
  "last week/month we", "find the work on X", "where did we leave X", "what did we
  decide about X", "did we ever look at X", "why did we do X that way", "catch me up on
  project X", "what happened last time", "find that conversation", "it was on tw / iq /
  the server" — and proactively before re-deriving a decision on a project untouched
  for weeks. Searches both local accounts (~/.claude, ~/.claude-iq) and mirrors of the
  other machines (tw, iq) automatically: `astern grep` (regex over raw transcripts, no
  index, seconds) for names, identifiers and phrases, `astern recall` (ir hybrid
  search over the synced store) for paraphrases and decisions. Reads the hits with
  `astern show`; writes a briefing when asked. Read-only on the sessions.
metadata:
  audience: developers
---

# astern recall — find past sessions, wherever they ran

Transcripts are the only honest record of why a project is the way it is. `astern` reads them on **every home this machine can see**: each local account (`~/.claude`, `~/.claude-iq`) and a mirror of each other machine (`~/.cache/xa/remotes/<host>/`, one per SSH host in `~/.config/xa/config.toml`, refreshed with `xa sync` before every search). Every hit names its `home` (`claude`, `claude-iq`, `tw`, `iq`), so you can say where a session ran. You never need to be told which machine to search: the default is all of them.

**A hit is an address, not an answer.** Read the top hits, then answer or write a briefing.

## Prerequisites (check once, cheap)

```bash
astern grep --help >/dev/null && echo ok      # grep: no extra needed
astern sessions --limit 1                     # recall: needs `pip install "astern[recall]"`
```

## 1. Pick the route by the shape of the query

| The query has… | Route | Why |
|---|---|---|
| a rare token: a name, nickname, identifier, path, error string, URL, issue number, quoted phrase | `astern grep` first | exact, no index, sees a session that ended a minute ago |
| only ordinary words ("the session where we argued about storage") | `astern recall` (hybrid) first | paraphrase and vocabulary mismatch are what embeddings are for |
| a decision or rationale ("what did we decide…") | `recall --grains session_synopses`, then grep the names it surfaces | synopses carry decisions; grep confirms in the source |
| both | run both; grep's hits are the precise ones | different failure modes, cheap to combine |

## 2. Grep: write patterns that use the structure

```bash
astern grep 'acme[- ]?corp|\bac\b'                       # all homes, newest first
astern grep 'venture|joint ?venture' --project acme --since-days 60
astern grep 'oauth.*refresh' --roles all --sort hits        # + tool inputs (commands, file bodies)
astern grep 'cleanupPeriodDays' --kinds session,subagent    # + subagent transcripts
```

- It matches the **decoded text of the conversational fields**: `user` (the human prompt, reminders stripped), `assistant` (prose), `title` (custom/AI titles, agent names), and with `--roles all` also `tool` (tool inputs). Never the raw JSON: CLAUDE.md attachments and record keys don't produce hits, and `\b`, `^`, `$` work on the text (`^` is the start of a message, not of a line).
- Python regex, case-insensitive by default (`--case-sensitive` to turn it off).
- **Widen deliberately**: alternation of synonyms (`venture|partnership|joint ?venture`), stems (`deploy\w*`), separator tolerance (`acme[- _]?corp`), abbreviations with word boundaries (`\bac\b`). Before writing a pattern for a project or person, run `ir synonyms <term>` to get the alternation the synonym ledger already knows.
- Narrow with structure, not with longer patterns: `--project` (cwd substring), `--since-days`, `--home <dir>` for one home, `--kinds`. Use `--sort hits` when recency misleads.
- Each session row carries `home`, `cwd`, `title`, `started_at`, `ended_at`, the first snippets and the transcript `path`.
- `--offline` skips the mirror refresh (use it for a burst of greps after the first one).

## 3. Recall: the indexed, semantic route

```bash
astern sync && astern index          # idempotent: seconds when nothing moved; sync covers every home
astern recall "how do we keep the miner from re-judging a resumed session"
astern recall "vector store backend choice" --project vd --since-days 120 --k 12
```

Flags: `--project`, `--since-days`, `--k`, `--grains session_synopses|session_turns|subagent_turns` (default synopses + turns), `--mode dense|lexical|hybrid` (default hybrid). `subagent_turns` answers "what did an agent do", and its "prompt" is the parent's instruction, so it is excluded by default. Unfiltered, recall also reaches the `skills` and `reports` corpora.

## 4. Judge the miss, reformulate, stop

- **A miss is**: no hit, or hits whose snippets don't mention the thing. Not "fewer than k".
- Reformulate with the vocabulary the session would have used: the file it would have touched, the command it would have run, the repo or issue number, the error text. Switch route (grep ↔ recall) rather than repeating the same one.
- Stop after three rounds that bring no new candidate sessions, and say what you searched (patterns, routes, homes): "not found on claude, claude-iq, tw, iq" is a finding.
- **Synonym ledger**: when a miss was fixed by a word only this corpus uses (a project nickname, a renamed package, a person's handle, private jargon) and you verified the hit, record it: `ir synonyms-add "<term>, <alias>, ..." --note "<why>"`. Don't add general-English synonyms; you produce those yourself.

## 5. Read the hits that matter

```bash
astern show <session_id>              # synced: meta + last turns
astern show <session_id> --turns 40
```

A grep hit not yet synced: run `astern sync` (all homes) and then `show`, or read the `path` directly (bounded: `rg -n 'pattern' <path> | head`). Where a synopsis and the turns disagree, the turns win. To resume a session, give the user `cclaude --resume <id>` to run from its `cwd` on the machine its `home` names.

## 6. Answer, or write the briefing

For a quick question, answer with the session id (8 chars), its home and date. For a briefing, write `docs/briefings/<YYYY-MM-DD>-<slug>.md` in the repo the question is about: cite every claim (session id + date), separate **Decided** from **Tried, not settled**, list **Open questions** and **Not found** (with the queries and homes searched). No hard-wrapped prose.

## Notes

- Never paste transcript content into a public artifact (issue, PR, committed doc) without checking it for secrets and home paths.
- Coverage limits: a machine without an xa host entry is invisible; a machine's transcripts older than its `cleanupPeriodDays` are gone unless an earlier `astern sync` stored them (recall still finds those). claude.ai chats are not Claude Code transcripts and are not searched.
- `episodes` (consecutive turns on one topic) does not exist yet (thorwhalen/astern#7); reach long topics through `session_synopses` and read in full with `astern show`.
