# astern.provenance

From a line of code back to the session turn that wrote it.

The chain, and why each link is needed:

1. **git** — `git blame -L` names the *last* commit to touch the line, which after
   a CI `ruff format` pass is a bot; `git log -S` names the commit that
   *introduced* the text. Both are reported, because when they differ the second is
   almost always the one you wanted.
2. **the trailer** — a `Claude-Session: https://claude.ai/code/session_<id>` line in
   the commit message. That `<id>` is the *claude.ai* session id, which is **not**
   the local transcript’s `sessionId`: the transcript carries a `bridge-session`
   record (`{"sessionId": <uuid>, "bridgeSessionId": "cse_<id>"}`) that joins the
   two, and [`astern.turns.session_meta()`](astern.turns.md#astern.turns.session_meta) keeps it as `bridge_session_id`.
3. **the store** — the session’s turns *and its subagents’* turns are searched for the
   `Edit` / `Write` / `Bash` call whose `input_text` contains the line. The
   subagent half is not optional: an agent that delegates writes the code from the
   subagent transcript, and the parent’s turns never contain the line.

With no trailer (older work, another machine) step 2 has nothing to say, so step 3
runs over every session whose `cwd` is inside the repo, narrowed to a date window
around the commit.

```pycon
>>> normalize('  return  sum(x)   ')
'return sum(x)'
>>> loose("f('a', 12)")
"f('a', 0)"
>>> session_id_from_trailer('feat: x\n\nClaude-Session: https://claude.ai/code/session_01ABC')
'01ABC'
```

### Module Attributes

| [`WRITE_TOOLS`](#astern.provenance.WRITE_TOOLS)      | The tools whose `input_text` can carry a line of source, best first.             |
|-------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`PROMPT_CHARS`](#astern.provenance.PROMPT_CHARS)     | How much of the turn's prose to carry back, per the answer's contract.           |
| [`MIN_NEEDLE_CHARS`](#astern.provenance.MIN_NEEDLE_CHARS) | A line shorter than this matches too much to be evidence of anything.            |
| [`MIN_PATH_CHARS`](#astern.provenance.MIN_PATH_CHARS)   | The same floor for a *path* needle, which is specific at a length a line is not. |

### Functions

| [`blame_commit`](#astern.provenance.blame_commit)(repo, rel_path, line)               | The sha of the commit that last touched `line` (`''` when blame fails).                                           |
|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`bridge_key`](#astern.provenance.bridge_key)(raw)                                  | The comparable half of a bridge id: `cse_01AB` and `session_01AB` agree.                                          |
| [`changed_files`](#astern.provenance.changed_files)(repo, sha)                         | The paths one commit touched.                                                                                     |
| [`commit_record`](#astern.provenance.commit_record)(repo, sha)                         | One commit as `{sha, short_sha, subject, author, date, session_id}`.                                              |
| [`entire_available`](#astern.provenance.entire_available)(repo)                           | Is the `entire` binary on PATH *and* enabled in this repo?                                                        |
| [`entire_why`](#astern.provenance.entire_why)(repo, rel_path, line, \*[, timeout])  | `entire why <file>:<line> --json`, verbatim; `None` when unavailable.                                             |
| [`introducing_commit`](#astern.provenance.introducing_commit)(repo, rel_path, text)         | The oldest commit whose diff changed the count of `text` in `rel_path`.                                           |
| [`iter_hits`](#astern.provenance.iter_hits)(store, session_ids, text, \*[, ...])   | Every tool call in those sessions whose input text contains `text`.                                               |
| [`line_text`](#astern.provenance.line_text)(repo, rel_path, line)                  | Line `line` of the working-tree file (1-based), or `''`.                                                          |
| [`loose`](#astern.provenance.loose)(text)                                      | [`normalize()`](#astern.provenance.normalize), then flatten quote style and every integer literal. |
| [`normalize`](#astern.provenance.normalize)(text)                                  | Collapse every run of whitespace, so indentation and reflow stop mattering.                                       |
| [`rank_hits`](#astern.provenance.rank_hits)(hits)                                  | Best evidence first: an exact match, from a writing tool, earliest in time.                                       |
| [`repo_root`](#astern.provenance.repo_root)(path)                                  | The work tree containing `path` (a file or a directory), or `None`.                                               |
| [`session_id_from_trailer`](#astern.provenance.session_id_from_trailer)(message)                 | The claude.ai session id in a commit message's `Claude-Session:` trailer.                                         |
| [`sessions_for_bridge`](#astern.provenance.sessions_for_bridge)(store, bridge_id)            | Local session ids whose `bridge_session_id` is the trailer's claude.ai id.                                        |
| [`sessions_in_repo`](#astern.provenance.sessions_in_repo)(store, repo, \*[, around, ...]) | Session ids whose `cwd` is inside `repo`, optionally near a commit date.                                          |
| [`with_subagents`](#astern.provenance.with_subagents)(store, session_ids)               | `session_ids` plus every stored transcript nested under one of them.                                              |

### astern.provenance.MIN_NEEDLE_CHARS *= 8*

A line shorter than this matches too much to be evidence of anything.

### astern.provenance.MIN_PATH_CHARS *= 4*

The same floor for a *path* needle, which is specific at a length a line is not.

### astern.provenance.PROMPT_CHARS *= 300*

How much of the turn’s prose to carry back, per the answer’s contract.

### astern.provenance.WRITE_TOOLS *= ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')*

The tools whose `input_text` can carry a line of source, best first. A `Write`
or `Edit` *is* the line; a `Bash` heredoc merely might be, so it ranks lower.

### astern.provenance.blame_commit(repo, rel_path, line)

The sha of the commit that last touched `line` (`''` when blame fails).

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### astern.provenance.bridge_key(raw)

The comparable half of a bridge id: `cse_01AB` and `session_01AB` agree.

The transcript’s `bridge-session` record prefixes the id `cse_`; the commit
trailer’s URL prefixes the same id `session_`. Only the tail is the identity.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> bridge_key('cse_01AB'), bridge_key('session_01AB'), bridge_key('01AB')
('01AB', '01AB', '01AB')
```

### astern.provenance.changed_files(repo, sha)

The paths one commit touched.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### astern.provenance.commit_record(repo, sha)

One commit as `{sha, short_sha, subject, author, date, session_id}`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### astern.provenance.entire_available(repo)

Is the `entire` binary on PATH *and* enabled in this repo?

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### astern.provenance.entire_why(repo, rel_path, line, , timeout=60.0)

`entire why <file>:<line> --json`, verbatim; `None` when unavailable.

Generation-time provenance, and therefore the better answer when it exists —
Entire recorded the checkpoint as the line was written. It only speaks for work
done after the repo was enabled, which is why astern’s own chain runs regardless.

### astern.provenance.introducing_commit(repo, rel_path, text)

The oldest commit whose diff changed the count of `text` in `rel_path`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### astern.provenance.iter_hits(store, session_ids, text, , fields=('input_text',), min_chars=8)

Every tool call in those sessions whose input text contains `text`.

Two passes over each candidate: the whitespace-normalized line (`match='exact'`)
and then [`loose()`](#astern.provenance.loose) (`match='loose'`), so a reformatted or renumbered line is
still found, and the answer says which pass found it.

`fields` says which part of the recorded tool call to search. `input_text` is
the body a `Write` wrote or an `Edit` inserted, and is what a *line* is found
in; `digest` is the call’s short label (a path, a command), and is what a *file*
is found in — a `Write`’s body never contains its own path.

`min_chars` is the floor below which a needle matches too much to be evidence.
It is lower for a path than for a line: `mod.py` is short but still specific,
where a six-character *line* is not.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### astern.provenance.line_text(repo, rel_path, line)

Line `line` of the working-tree file (1-based), or `''`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### astern.provenance.loose(text)

[`normalize()`](#astern.provenance.normalize), then flatten quote style and every integer literal.

A formatter rewrites `'x'` to `"x"` and a refactor renumbers a constant
without either being a different line; this is the second pass that still finds it.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> loose('assert n == 42, "boom"')
"assert n == 0, 'boom'"
```

### astern.provenance.normalize(text)

Collapse every run of whitespace, so indentation and reflow stop mattering.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> normalize('a\t b\n  c ')
'a b c'
```

### astern.provenance.rank_hits(hits)

Best evidence first: an exact match, from a writing tool, earliest in time.

Earliest wins because the question is *who wrote this line*, and a later turn
that merely re-wrote the same file is the answer to a different question.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

```pycon
>>> a = {'match': 'loose', 'tool': 'Write', 'timestamp': '2026-01-01'}
>>> b = {'match': 'exact', 'tool': 'Bash', 'timestamp': '2026-01-02'}
>>> c = {'match': 'exact', 'tool': 'Write', 'timestamp': '2026-01-03'}
>>> [h['tool'] for h in rank_hits([a, b, c])]
['Write', 'Bash', 'Write']
```

### astern.provenance.repo_root(path)

The work tree containing `path` (a file or a directory), or `None`.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### astern.provenance.session_id_from_trailer(message)

The claude.ai session id in a commit message’s `Claude-Session:` trailer.

Accepts the URL form and a bare `session_<id>`; returns the id without prefix.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

```pycon
>>> session_id_from_trailer('no trailer here') is None
True
>>> session_id_from_trailer('x\n\nClaude-Session: session_01Z9')
'01Z9'
```

### astern.provenance.sessions_for_bridge(store, bridge_id)

Local session ids whose `bridge_session_id` is the trailer’s claude.ai id.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### astern.provenance.sessions_in_repo(store, repo, , around=None, window_days=14.0)

Session ids whose `cwd` is inside `repo`, optionally near a commit date.

A worktree session’s `cwd` is still under the repo root, so this keeps them;
a session that edited the repo from somewhere else is what the date window and
the whole-store scan are for.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### astern.provenance.with_subagents(store, session_ids)

`session_ids` plus every stored transcript nested under one of them.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]
