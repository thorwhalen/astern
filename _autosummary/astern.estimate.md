# astern.estimate

What will judging this batch cost? — features, a fitted model, a prediction.

The point of the whole package is to burn fewer tokens, so it must be able to say
what a run *will* burn before it burns it. Every judged session stores its
[`features()`](#astern.estimate.features) (all computable with no model at all) next to the usage the judge
actually reported, which makes the cost model a plain regression of a measured
quantity on free ones. [`fit()`](#astern.estimate.fit) reads those pairs, [`predict()`](#astern.estimate.predict) applies them,
and `astern judge --dry-run` prices a batch without calling anything.

Three decisions:

- \*\*The regressor is `view_chars`, not `bytes`.\*\* The judge never sees the
  transcript; it sees the view [`astern.views`](astern.views.md#module-astern.views) renders, and the view has a
  ceiling. Session bytes vary 100x, view chars vary ~3x, and the second is what is
  actually sent. `bytes` stays in the feature dict as the fallback proxy for
  sessions with no view yet.
- **Ordinary least squares, in stdlib.** `numpy` is not a dependency and three
  columns do not justify one; the normal equations solved by Gaussian elimination
  are twenty lines and exact enough for a token budget.
- **The intercept is real and must not be forced through zero.** A tool-less call
  with our own system prompt still costs ~1.1k input tokens before the view is
  added, so a proportional model under-prices short sessions by a factor of two.
- **Retries, not view size, are what the residual is made of.** Measured over ten
  real sessions on haiku, two views within 6% of each other cost 4.8k and 20.8k
  input tokens; the difference is the number of API round trips, because a
  structured answer the schema rejects is retried with the whole conversation
  resent. Restricted to the calls that took one round trip the same regression has
  R² = 0.995 — so `fit` reports `input_single_pass` and `retry_rate` beside
  the headline line. `astern.judge.claude_judge` now defaults to
  `strict_schema=False`, which is what makes a 0% retry rate the normal case
  rather than the exception: [`predict()`](#astern.estimate.predict) uses the `input_single_pass` line
  whenever the stored judgments’ `retry_rate` for the current settings is 0 (a
  batch that has not retried once), and falls back to the all-calls `input` line
  the moment even one retry is on record — a low overall R² then has an
  explanation instead of a shrug, and a prediction says in its own output
  (`input_model`) which line priced it.

```pycon
>>> pts = [{'features': {'view_chars': c, 'n_turns': 2}, 'usage':
...         {'input_tokens': 1000 + c // 4, 'output_tokens': 300}}
...        for c in (2000, 6000, 12000, 20000)]
>>> m = fit(pts)
>>> m['n'], round(m['input']['per_1k_chars']), round(m['input']['r2'], 3)
(4, 250, 1.0)
>>> p = predict(m, {'view_chars': 8000, 'n_turns': 3})
>>> p['input_tokens'], p['output_tokens']
(3000, 300)
```

### Module Attributes

| [`PRIOR_INPUT_INTERCEPT`](#astern.estimate.PRIOR_INPUT_INTERCEPT)   | What one tool-less `claude -p` call costs before the view is added, and roughly how many input tokens a char of view buys (measured 2026-09-07 on haiku).    |
|--------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`MIN_POINTS_FOR_OLS`](#astern.estimate.MIN_POINTS_FOR_OLS)      | Below this many observations a slope is noise; [`fit()`](#astern.estimate.fit) reports a ratio model instead and says so in `method`. |
| [`FIXED_CACHE_TOKENS`](#astern.estimate.FIXED_CACHE_TOKENS)      | A *non-zero* cache read does not mean a retry.                                                                                                               |

### Functions

| [`features`](#astern.estimate.features)(session, turns, view_text)   | Everything about a session that costs no tokens to know.                         |
|----------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`fit`](#astern.estimate.fit)(judgments)                        | Fit input tokens on view size; summarise output tokens, totals and cost.         |
| [`predict`](#astern.estimate.predict)(model, features)              | Predicted tokens (and cost) of judging one session, with a ±1σ band.             |
| [`round_trips`](#astern.estimate.round_trips)(judgment)                 | How many API calls one judge call took — recorded, else inferred from the cache. |

### astern.estimate.FIXED_CACHE_TOKENS *= 2000*

A *non-zero* cache read does not mean a retry. Two consecutive judge calls that each
took exactly one round trip still read ~1.3k cached tokens: the system prompt and the
schema are identical between invocations and the CLI’s cache window outlives one call.
Only a read comfortably above that block means the *conversation* was re-sent.

### astern.estimate.MIN_POINTS_FOR_OLS *= 3*

Below this many observations a slope is noise; [`fit()`](#astern.estimate.fit) reports a ratio model
instead and says so in `method`.

### astern.estimate.PRIOR_INPUT_INTERCEPT *= 1100.0*

What one tool-less `claude -p` call costs before the view is added, and roughly
how many input tokens a char of view buys (measured 2026-09-07 on haiku). Used
only when nothing has been judged yet — a prior, replaced by the first real fit.

### astern.estimate.features(session, turns, view_text)

Everything about a session that costs no tokens to know.

`turns` is the scope actually rendered (the new turns, on an incremental run),
so the features describe the same thing the usage will.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> f = features({'source': {'size': 4096}}, [{'user_prompt': 'hi', 'assistant_chars': 10,
...     'n_tool_calls': 2, 'n_errors': 1, 'tool_result_chars': 99, 'tools': []}], 'x' * 40)
>>> f['bytes'], f['n_turns'], f['prose_chars'], f['view_chars'], f['view_tokens_est']
(4096, 1, 12, 40, 10)
```

### astern.estimate.fit(judgments)

Fit input tokens on view size; summarise output tokens, totals and cost.

`judgments` are the records `astern.lenses.synopsis` stores (each with
`features` and `usage`). With fewer than [`MIN_POINTS_FOR_OLS`](#astern.estimate.MIN_POINTS_FOR_OLS) points
the slope is a ratio through the measured intercept, and `method` says so.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> fit([])['method']
'prior'
>>> fit([{'features': {'view_chars': 4000, 'n_turns': 2},
...       'usage': {'input_tokens': 2200, 'output_tokens': 800}}])['method']
'ratio'
```

### astern.estimate.predict(model, features)

Predicted tokens (and cost) of judging one session, with a ±1σ band.

`input_model` in the result says which regression priced it — `single_pass`
when the judged batch this model was fit on never retried, `all_calls`
otherwise (see `_input_model()`).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

```pycon
>>> m = fit([])
>>> p = predict(m, {'view_chars': 10000})
>>> p['input_tokens'], p['total_tokens'] == p['input_tokens'] + p['output_tokens']
(3800, True)
>>> p['low'] <= p['total_tokens'] <= p['high']
True
>>> p['input_model']
'all_calls'
```

### astern.estimate.round_trips(judgment)

How many API calls one judge call took — recorded, else inferred from the cache.

`n_iterations` (recorded since the CLI’s `usage.iterations` was captured) is
authoritative. Older records have only the cache read to go on, and the threshold
it is compared against is [`FIXED_CACHE_TOKENS`](#astern.estimate.FIXED_CACHE_TOKENS), not zero — see there.

* **Return type:**
  [`int`](https://docs.python.org/3/builtins/functions.html#int)

```pycon
>>> round_trips({'usage': {'n_iterations': 3}})
3
>>> round_trips({'usage': {'cache_read_input_tokens': 5197}})
2
>>> round_trips({'usage': {'cache_read_input_tokens': 1319}})
1
```
