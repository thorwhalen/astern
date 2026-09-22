# astern.views

The compressed markdown a judge reads instead of a transcript.

A session file here is a median 1.2 MB and 88% of it is tool I/O. A judge does not
need the tool I/O: it needs what was asked, what was done, and whether it worked.
[`session_view()`](#astern.views.session_view) renders that — a header, then one block per turn carrying the
human’s prompt, a one-line tool summary and the assistant’s closing text — and
guarantees a ceiling on its own size, which is what makes the cost of a batch
predictable at all.

Shrinking happens in a fixed order, and the view says what it did, because a judge
that is silently handed half a session will confidently synopsise half a session:

1. per-turn budgets are scaled down proportionally (never below `MIN_*`),
2. then middle turns are dropped, keeping the first and the last ones — the goal is
   usually stated at the start and the outcome shown at the end,
3. and an explicit `turns … omitted` line goes into the view either way.

When `prior` is given — the synopsis a previous run produced for the same session
— the view opens with a short “Previously in this session” recap and then covers
only the new turns. That is the incremental path: a resumed session pays for its
new turns only.

```pycon
>>> turns = [{'index': 0, 'timestamp': '2026-09-01T10:00:00Z', 'user_prompt': 'fix the build',
...           'assistant_full': 'Fixed it.', 'tools': [
...              {'name': 'Bash', 'digest': 'uv build', 'is_error': True},
...              {'name': 'Bash', 'digest': 'uv build', 'is_error': False}]}]
>>> v = session_view({'session_id': 'abc123', 'title': 'Build fix'}, turns)
>>> print(v)
# Session abc123 — Build fix

turns: 1 (showing 1)

### turn 0 (2026-09-01T10:00:00Z)
user: fix the build
tools: Bash×2, 1 error: Bash "uv build"
assistant: Fixed it.
>>> view_stats(v)['n_turns_shown'], view_stats(v)['n_turns_dropped']
(1, 0)
```

### Module Attributes

| [`DFLT_MAX_CHARS`](#astern.views.DFLT_MAX_CHARS)   | under the plan's ≤8k-per-synopsis budget with room for the schema and the system prompt.   |
|-------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| [`MIN_PROMPT_CHARS`](#astern.views.MIN_PROMPT_CHARS) | Floors for the proportional-shrink step.                                                   |

### Functions

| [`session_view`](#astern.views.session_view)(session, turns, \*[, max_chars, ...])   | A judge-sized markdown view of `turns`, never longer than `max_chars`.   |
|-------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [`tool_summary`](#astern.views.tool_summary)(turn, \*[, max_errors, digest_chars])   | One line for a turn's tool calls: the mix, then the errors that matter.  |
| [`view_stats`](#astern.views.view_stats)(text)                                     | Size and coverage of a rendered view — the cost model's cheap regressor. |

### astern.views.DFLT_MAX_CHARS *= 24000*

under
the plan’s ≤8k-per-synopsis budget with room for the schema and the system prompt.

* **Type:**
  A judge call is ~4 chars per token, so a 24k-char view is ~6k input tokens

### astern.views.MIN_PROMPT_CHARS *= 160*

Floors for the proportional-shrink step. Below these a turn stops being evidence
and becomes noise, so the next step (dropping whole turns) is the honest one.

### astern.views.session_view(session, turns, , max_chars=24000, prompt_chars=1200, reply_chars=1500, prior=None)

A judge-sized markdown view of `turns`, never longer than `max_chars`.

`prior` is the previous synopsis dict for the same session; when given, the
view recaps it and `turns` should be the new turns only.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> ts = [{'index': i, 'user_prompt': 'x' * 400, 'assistant_full': 'y' * 400,
...        'tools': []} for i in range(20)]
>>> v = session_view({'session_id': 's'}, ts, max_chars=2000)
>>> len(v) <= 2000, view_stats(v)['n_turns_dropped'] > 0
(True, True)
>>> 'Previously in this session' in session_view({'session_id': 's'}, ts[:1],
...                                              prior={'goal': 'ship it'})
True
```

### astern.views.tool_summary(turn, , max_errors=3, digest_chars=70)

One line for a turn’s tool calls: the mix, then the errors that matter.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> tool_summary({'tools': [{'name': 'Bash', 'digest': 'ls', 'is_error': False},
...                         {'name': 'Bash', 'digest': 'uv build', 'is_error': True},
...                         {'name': 'Edit', 'digest': '/a.py', 'is_error': False}]})
'Bash×2 Edit, 1 error: Bash "uv build"'
>>> tool_summary({'tools': []})
''
```

### astern.views.view_stats(text)

Size and coverage of a rendered view — the cost model’s cheap regressor.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> view_stats('# Session s\n\n### turn 0\nuser: hi\n')
{'chars': 33, 'tokens_est': 8, 'n_turns_shown': 1, 'n_turns_dropped': 0}
```
