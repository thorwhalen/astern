# astern.entire

Enable and inspect the [Entire CLI](https://github.com/entireio/cli) safely, per repo.

`entire agent add claude-code` is one command, but two of its defaults are unsafe
for a public repo: it installs a `pre-push` git hook that, by default, pushes
`refs/entire/checkpoints/*` — verbatim Claude Code transcripts, absolute paths
unscrubbed — to origin on every ordinary `git push`, and it opts into PostHog
telemetry. Neither shows up in `git config --get-all remote.origin.push` or
`git push --dry-run`: the push happens from a git hook, not a refspec, so the usual
way of auditing what a push sends says nothing about it.

This module exists to make enabling Entire safe *by default*: [`entire_enable()`](#astern.entire.entire_enable)
runs the same `entire agent add claude-code` the docs tell you to run, then turns
both defaults off unless asked otherwise, and reports exactly what changed.
[`entire_status()`](#astern.entire.entire_status) answers what is already true of a repo — without invoking
`entire` itself — so it works offline and never mutates anything.

Measured against Entire CLI 0.10.5 (2026-09-07, in a throwaway clone, never a real
repo — see `tests/test_entire.py`):

- `entire agent add claude-code` writes 8 hook entries into `.claude/settings.json`
  (`PreToolUse`, two under `PostToolUse`, `SessionStart`, `SessionEnd`,
  `Stop`, `SubagentStop`, `UserPromptSubmit`) and five git hooks
  (`commit-msg`, `post-commit`, `post-rewrite`, `pre-push`,
  `prepare-commit-msg`) — the `pre-push` one runs
  `entire hooks git pre-push "$1"` unconditionally whenever `entire` is on PATH.
- `entire configure --skip-push-sessions` writes
  `strategy_options.push_sessions: false` into `.entire/settings.json`.
- `entire configure --telemetry=false` writes `telemetry: false` into the same
  file. (`--telemetry` is a bool flag; `entire configure --help` documents both
  defaulting to enabled/true when unset.)

```pycon
>>> _hook_slugs({'hooks': {'Stop': [{'matcher': ''}], 'PostToolUse': [{'matcher': 'Agent'}, {'matcher': 'TaskCreate'}]}})
['PostToolUse/Agent', 'PostToolUse/TaskCreate', 'Stop/*']
>>> _is_github_origin('git@github.com:thorwhalen/astern.git')
True
>>> _is_github_origin('https://gitlab.com/x/y.git')
False
```

### Functions

| [`entire_enable`](#astern.entire.entire_enable)([repo, push_sessions, ...])   | Enable Entire's Claude Code hooks in `repo`, safe by default.                    |
|----------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`entire_status`](#astern.entire.entire_status)([repo])                       | What is already true of `repo`'s Entire setup — reads only, never runs `entire`. |

### astern.entire.entire_enable(repo='.', , push_sessions=False, telemetry=False, dry_run=False)

Enable Entire’s Claude Code hooks in `repo`, safe by default.

Runs `entire agent add claude-code`, then — unless told otherwise —
`entire configure --skip-push-sessions` (so checkpoints stay local) and
`entire configure --telemetry=false` (so nothing phones PostHog). Pass
`push_sessions=True` or `telemetry=True` to keep either of Entire’s own
(opt-in) defaults; doing so on a github.com origin adds a plain-language entry
to `warnings` rather than silently proceeding.

`dry_run=True` reports the commands that would run and returns before running
anything. Idempotent either way — `entire agent add` and `entire configure`
are themselves idempotent, so re-running this changes nothing that already
matches.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.entire.entire_status(repo='.')

What is already true of `repo`’s Entire setup — reads only, never runs `entire`.

`enabled` means both halves are present: hooks in `.claude/settings.json` *and*
a `pre-push` git hook that mentions `entire` — either alone (a manually edited
settings file, a hook removed by hand) is not really enabled. `push_sessions` and
`telemetry` fall back to Entire’s own defaults (both `True`) when
`.entire/settings.json` doesn’t mention them. `remote_checkpoints` is `None`,
not `False`, when the remote can’t be reached — offline is not evidence of
nothing being pushed.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
