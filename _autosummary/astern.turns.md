# astern.turns

From raw transcript records to one record per user→assistant turn, plus session meta.

The turn record is the unit every lens works on. Its shape is the one `priv`’s
`claude_transcripts` established and `ir` indexes, extended with what the mining
lenses need and the indexer did not: the tool calls of the turn (name, a short
digest of the input, whether the result was an error), the `system` records that
fell inside the turn (`turn_duration`, `compact_boundary`), and API usage
de-duplicated by `message.id` (one API turn spans several `assistant` lines that
repeat the same `usage`; summing per line overcounts by an order of magnitude).

```pycon
>>> recs = [
...   {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/p/x", "timestamp": "t0",
...    "message": {"role": "user", "content": "fix the bug"}},
...   {"type": "assistant", "uuid": "a1", "sessionId": "s", "message": {"id": "m1",
...    "model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5},
...    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}}]}},
...   {"type": "user", "uuid": "u2", "sessionId": "s", "message": {"role": "user",
...    "content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}]}},
...   {"type": "assistant", "uuid": "a2", "sessionId": "s", "message": {"id": "m2",
...    "model": "claude-x", "usage": {"input_tokens": 20, "output_tokens": 7},
...    "content": [{"type": "text", "text": "Fixed."}]}},
... ]
>>> t = list(iter_turns(recs))[0]
>>> t["index"], t["user_prompt"], t["assistant_summary"], t["n_errors"]
(0, 'fix the bug', 'Fixed.', 1)
>>> t["tools"][0]["name"], t["tools"][0]["is_error"], t["usage"]["input_tokens"]
('Bash', True, 30)
>>> t["tools"][0]["input_text"], t["tools"][0]["input_chars"]
('pytest', 6)
```

### Module Attributes

| [`WRAPPER_TAGS`](#astern.turns.WRAPPER_TAGS)         | XML-ish wrapper tags the CLI logs around non-prose user lines — slash-command echoes, local-command stdout, injected reminders.   |
|-----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| [`KEPT_SYSTEM_SUBTYPES`](#astern.turns.KEPT_SYSTEM_SUBTYPES) | Which `system` subtypes are kept on the turn record (others are counted only).                                                    |
| [`META_TYPES`](#astern.turns.META_TYPES)           | Session-level records that are worth keeping as metadata, keyed by their type.                                                    |
| [`MAX_INPUT_TEXT_CHARS`](#astern.turns.MAX_INPUT_TEXT_CHARS) | How much of a tool's full input text (a Bash command, a Write's content) to keep verbatim on the turn record.                     |

### Functions

| [`clean_prompt`](#astern.turns.clean_prompt)(text)         | Strip CLI wrapper tags (and their content) from a user line → prose only.         |
|-----------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| `is_tool_result`(msg)                                                       |                                                                                   |
| [`is_user_prompt`](#astern.turns.is_user_prompt)(msg)        | A real human prompt: a `user` line that is neither meta nor a tool result.        |
| [`iter_turn_pairs`](#astern.turns.iter_turn_pairs)(records)   | Yield `(user_prompt_record, [records of the turn])` up to the next real prompt.   |
| [`iter_turns`](#astern.turns.iter_turns)(records)        | Yield one JSON-able record per turn.                                              |
| [`session_meta`](#astern.turns.session_meta)(records)      | Session-level facts from the non-conversational records (last occurrence wins).   |
| [`tool_digest`](#astern.turns.tool_digest)(name, inp)     | A short, comparable summary of a tool input: the command, the path, or the query. |
| [`tool_input_text`](#astern.turns.tool_input_text)(name, inp) | The full text of a tool input worth keeping verbatim: a command, a file body.     |

### astern.turns.KEPT_SYSTEM_SUBTYPES *= ('turn_duration', 'compact_boundary', 'away_summary', 'stop_hook_summary')*

Which `system` subtypes are kept on the turn record (others are counted only).

### astern.turns.MAX_INPUT_TEXT_CHARS *= 20000*

How much of a tool’s full input text (a Bash command, a Write’s content) to keep
verbatim on the turn record. Big enough for real scripts, capped so one pasted
blob can’t blow up the store.

### astern.turns.META_TYPES *= ('ai-title', 'custom-title', 'agent-name', 'pr-link', 'cost-state', 'permission-mode', 'worktree-state', 'frame-link', 'bridge-session')*

Session-level records that are worth keeping as metadata, keyed by their type.

### astern.turns.WRAPPER_TAGS *= ('command-name', 'command-message', 'command-args', 'local-command-stdout', 'local-command-stderr', 'local-command-caveat', 'bash-input', 'bash-stdout', 'bash-stderr', 'system-reminder', 'user-prompt-submit-hook', 'task-notification')*

XML-ish wrapper tags the CLI logs around non-prose user lines — slash-command
echoes, local-command stdout, injected reminders. Stripped from the *user prompt*
so it carries the human’s words, not tooling noise.

### astern.turns.clean_prompt(text)

Strip CLI wrapper tags (and their content) from a user line → prose only.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> clean_prompt('<system-reminder>x</system-reminder> hello  world')
'hello world'
```

### astern.turns.is_user_prompt(msg)

A real human prompt: a `user` line that is neither meta nor a tool result.

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### astern.turns.iter_turn_pairs(records)

Yield `(user_prompt_record, [records of the turn])` up to the next real prompt.

The turn’s records include assistant lines, the `tool_result` user lines that
continue the same turn, and `system` lines that fell inside it.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]]]

### astern.turns.iter_turns(records)

Yield one JSON-able record per turn. See the module docstring for the shape.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### astern.turns.session_meta(records)

Session-level facts from the non-conversational records (last occurrence wins).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> session_meta([{"type": "ai-title", "aiTitle": "Fix CI"}, {"type": "pr-link",
...   "prNumber": 3, "prUrl": "u", "prRepository": "o/r"}, {"type": "user", "sessionId": "s",
...   "cwd": "/p/x", "timestamp": "t", "version": "2.1", "message": {"content": "hi"}}])["title"]
'Fix CI'
```

### astern.turns.tool_digest(name, inp)

A short, comparable summary of a tool input: the command, the path, or the query.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> tool_digest('Bash', {'command': 'ls -la'})
'ls -la'
>>> tool_digest('Edit', {'file_path': '/a/b.py', 'old_string': 'x'})
'/a/b.py'
>>> tool_digest('SendMessage', {'to': 'a', 'message': 'hi'})
'{"message": "hi", "to": "a"}'
```

### astern.turns.tool_input_text(name, inp)

The full text of a tool input worth keeping verbatim: a command, a file body.

Unlike [`tool_digest()`](#astern.turns.tool_digest) (a short label for display) this is uncapped source
text — a rewrites lens needs the whole script, not its first 200 chars.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> tool_input_text('Bash', {'command': 'ls -la'})
'ls -la'
>>> tool_input_text('Write', {'file_path': '/tmp/x.py', 'content': 'print(1)'})
'print(1)'
```
