# astern.judge

The `judge=` seam: an LLM call that returns text or JSON *and says what it cost*.

The default judge is the local `claude` CLI run headless (`claude -p`). That is a
deliberate choice, not a shortcut: astern is used from Claude Code, by someone on a
Claude subscription, so the subscription is the budget and the CLI is the only
client that draws on it. `--output-format json` reports the usage of every call,
which is exactly the dependent variable the cost model in [`astern.estimate`](astern.estimate.md#module-astern.estimate)
needs. `--no-session-persistence` keeps the judge’s own calls out of
`~/.claude/projects` (verified by counting transcripts before and after), so the
miner does not grow the corpus it mines.

Five things about the flags, each measured against the real CLI on 2026-09-07 and
each load-bearing:

- **Not** `--bare`. Its own help says Anthropic auth is *strictly*
  `ANTHROPIC_API_KEY` or an `apiKeyHelper` — “OAuth and keychain are never
  read” — so on a subscription every `--bare` call comes back
  `"Not logged in · Please run /login"`. [`SANDBOX_FLAGS`](#astern.judge.SANDBOX_FLAGS) uses
  `--safe-mode` instead, which disables the same customizations (CLAUDE.md,
  skills, hooks, plugins, MCP) and leaves authentication alone.
- `--tools ""` really does empty the tool set: same prompt, same system prompt,
  1,109 input tokens with it against 14,925 without. (Asking the model what tools
  it has is not a test — it confidently lists six it does not have.)
- A failed call **exits 0**. The “not logged in” answer above came back on
  `returncode == 0` with `is_error: true` in the JSON, so the return code alone
  is not a success check.
- `--json-schema` is honoured and the parsed object lands under
  `structured_output`; `result` still holds the text. Passing our own
  `--system-prompt` also removes the ~4k-token default preamble from every call
  and moves the whole input into `input_tokens` (nothing is prompt-cached), which
  is what makes the regression in [`astern.estimate`](astern.estimate.md#module-astern.estimate) a straight line.
- **\`\`–json-schema\`\` is the retry driver, not a safety net.** When the model’s
  structured answer fails the CLI’s own schema validation, the CLI retries by
  re-sending the *whole* conversation — measured at a 40% retry rate on plain
  haiku calls and 100% on `--effort low` ones, multiplying input tokens 3-4x for
  work a single pass does for a fraction of the cost. [`claude_judge()`](#astern.judge.claude_judge) defaults
  to `strict_schema=False`: the schema is embedded in the prompt as an
  instruction instead of passed as `--json-schema`, and the answer is parsed
  with `_parse_json_loose()`. There is no CLI-side retry to fail, so a slightly
  malformed answer costs one round trip and a `judge_error` finding — not three
  round trips and the same finding. `strict_schema=True` restores the old
  behaviour for callers that need CLI-side schema enforcement more than they need
  a predictable bill.

A replacement is any callable with the same signature returning a [`Judgment`](#astern.judge.Judgment)
— an `aix.prompt_func` for API billing, or a recorded-replay judge for tests.

```pycon
>>> j = replay_judge({'hello': '{"answer": 1}'})
>>> r = j('hello', schema={'type': 'object'})
>>> r.data, r.usage['input_tokens'] > 0, r.error
({'answer': 1}, True, None)
>>> total_tokens(r.usage) == r.usage['input_tokens'] + r.usage['output_tokens']
True
```

### Module Attributes

| [`SANDBOX_FLAGS`](#astern.judge.SANDBOX_FLAGS)   | Flags that make one headless call a stateless, tool-less, corpus-neutral judge.   |
|------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| [`INPUT_KEYS`](#astern.judge.INPUT_KEYS)      | The usage keys that are billed as input.                                          |

### Functions

| [`claude_judge`](#astern.judge.claude_judge)(prompt, \*[, schema, model, ...])   | Run `claude -p` headless on `prompt` and return a [`Judgment`](#astern.judge.Judgment).   |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [`input_tokens`](#astern.judge.input_tokens)(usage)                              | Billed input of one call: fresh input plus both halves of the cache.                                           |
| [`replay_judge`](#astern.judge.replay_judge)(answers, \*[, default])             | A judge that answers from a mapping of prompt → text; for tests and dry runs.                                  |
| [`total_tokens`](#astern.judge.total_tokens)(usage)                              | Input (all three flavours) plus output.                                                                        |

### Classes

| [`Judgment`](#astern.judge.Judgment)(text[, data, usage, cost_usd, ...])   | What one judge call returned and what it cost.   |
|-------------------------------------------------------------------------------------------------|--------------------------------------------------|

### astern.judge.INPUT_KEYS *= ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')*

The usage keys that are billed as input. `input_tokens` alone under-reports by
an order of magnitude whenever the default system prompt is cached.

### *class* astern.judge.Judgment(text, data=None, usage=<factory>, cost_usd=None, duration_ms=None, model='', prompt_chars=0, num_turns=1, raw=None, error=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

What one judge call returned and what it cost.

#### num_turns *: [int](https://docs.python.org/3/builtins/functions.html#int)* *= 1*

How many API round trips the CLI made for this one call. It is normally 1, but a
structured answer the schema rejects is retried with the whole conversation
resent, so this is the multiplier on the input the view actually explains — the
single largest source of variance in the cost model, and invisible without it.

### astern.judge.SANDBOX_FLAGS *= ('--safe-mode', '--no-session-persistence', '--tools', '')*

Flags that make one headless call a stateless, tool-less, corpus-neutral judge.
`--safe-mode` (not `--bare`: see the module docstring) drops CLAUDE.md, skills,
hooks, plugins and MCP; `--tools ""` empties the tool set; the persistence flag
keeps the call out of `~/.claude/projects`.

### astern.judge.claude_judge(prompt, , schema=None, model='haiku', effort=None, system=None, timeout_s=900, claude_bin='claude', strict_schema=False)

Run `claude -p` headless on `prompt` and return a [`Judgment`](#astern.judge.Judgment).

Tools are disabled and customizations skipped ([`SANDBOX_FLAGS`](#astern.judge.SANDBOX_FLAGS)): the judge
reads what it is given and answers; it never explores. `schema` requests
structured output.

By default (`strict_schema=False`, see the module docstring) `schema` is
never passed to the CLI as `--json-schema` — that is what triggers the CLI’s
own retry-with-full-conversation behaviour on a rejected answer. Instead the
schema is embedded in the prompt (or the system prompt, when one is given) as a
terse instruction, and the answer is parsed with `_parse_json_loose()`. An
answer that still does not parse is not retried; it comes back as a
`Judgment` with `error='unparseable JSON'` and `data=None` — the usage the
call actually spent is still recorded, because the call still happened.

With `strict_schema=True`, `schema` is passed as `--json-schema` and the
parsed object comes back under `structured_output` — today’s behaviour,
retries included.

* **Return type:**
  [`Judgment`](#astern.judge.Judgment)

### astern.judge.input_tokens(usage)

Billed input of one call: fresh input plus both halves of the cache.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

```pycon
>>> input_tokens({'input_tokens': 9, 'cache_creation_input_tokens': 4241})
4250
```

### astern.judge.replay_judge(answers, , default=None)

A judge that answers from a mapping of prompt → text; for tests and dry runs.

A prompt with no recorded answer is an *error* judgment unless `default` is
given — silently answering `""` would let a test mistake a missing recording
for a real (empty) verdict, which is the one thing a replay judge must not do.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[`...`](https://docs.python.org/3/builtins/constants.html#Ellipsis), [`Judgment`](#astern.judge.Judgment)]

```pycon
>>> replay_judge({})('unseen').error
'no recorded answer'
>>> replay_judge({}, default='{}')('unseen').error is None
True
```

### astern.judge.total_tokens(usage)

Input (all three flavours) plus output.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

```pycon
>>> total_tokens({'input_tokens': 10, 'output_tokens': 5})
15
```
