"""What will judging this batch cost? — features, a fitted model, a prediction.

The point of the whole package is to burn fewer tokens, so it must be able to say
what a run *will* burn before it burns it. Every judged session stores its
:func:`features` (all computable with no model at all) next to the usage the judge
actually reported, which makes the cost model a plain regression of a measured
quantity on free ones. :func:`fit` reads those pairs, :func:`predict` applies them,
and ``astern judge --dry-run`` prices a batch without calling anything.

Three decisions:

- **The regressor is ``view_chars``, not ``bytes``.** The judge never sees the
  transcript; it sees the view :mod:`astern.views` renders, and the view has a
  ceiling. Session bytes vary 100x, view chars vary ~3x, and the second is what is
  actually sent. ``bytes`` stays in the feature dict as the fallback proxy for
  sessions with no view yet.
- **Ordinary least squares, in stdlib.** ``numpy`` is not a dependency and three
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
  R² = 0.995 — so ``fit`` reports ``input_single_pass`` and ``retry_rate`` beside
  the headline line, and a low overall R² has an explanation instead of a shrug.
  :func:`predict` still uses the all-calls line, because retries are part of what a
  batch actually costs.

>>> pts = [{'features': {'view_chars': c, 'n_turns': 2}, 'usage':
...         {'input_tokens': 1000 + c // 4, 'output_tokens': 300}}
...        for c in (2000, 6000, 12000, 20000)]
>>> m = fit(pts)
>>> m['n'], round(m['input']['per_1k_chars']), round(m['input']['r2'], 3)
(4, 250, 1.0)
>>> p = predict(m, {'view_chars': 8000, 'n_turns': 3})
>>> p['input_tokens'], p['output_tokens']
(3000, 300)
"""

from __future__ import annotations

from statistics import mean, pstdev

from astern.judge import input_tokens, total_tokens
from astern.views import DFLT_MAX_CHARS

#: What one tool-less ``claude -p`` call costs before the view is added, and roughly
#: how many input tokens a char of view buys (measured 2026-09-07 on haiku). Used
#: only when nothing has been judged yet — a prior, replaced by the first real fit.
PRIOR_INPUT_INTERCEPT = 1100.0
PRIOR_INPUT_PER_CHAR = 0.27
PRIOR_OUTPUT_TOKENS = 900.0

#: Below this many observations a slope is noise; :func:`fit` reports a ratio model
#: instead and says so in ``method``.
MIN_POINTS_FOR_OLS = 3


def features(session: dict, turns: list[dict], view_text: str) -> dict:
    """Everything about a session that costs no tokens to know.

    ``turns`` is the scope actually rendered (the new turns, on an incremental run),
    so the features describe the same thing the usage will.

    >>> f = features({'source': {'size': 4096}}, [{'user_prompt': 'hi', 'assistant_chars': 10,
    ...     'n_tool_calls': 2, 'n_errors': 1, 'tool_result_chars': 99, 'tools': []}], 'x' * 40)
    >>> f['bytes'], f['n_turns'], f['prose_chars'], f['view_chars'], f['view_tokens_est']
    (4096, 1, 12, 40, 10)
    """
    prompt_chars = sum(len(t.get("user_prompt", "")) for t in turns)
    assistant_chars = sum(int(t.get("assistant_chars") or 0) for t in turns)
    return {
        "bytes": int((session.get("source") or {}).get("size") or 0),
        "n_turns": len(turns),
        "n_tool_calls": sum(int(t.get("n_tool_calls") or 0) for t in turns),
        "n_errors": sum(int(t.get("n_errors") or 0) for t in turns),
        "prompt_chars": prompt_chars,
        "assistant_chars": assistant_chars,
        "prose_chars": prompt_chars + assistant_chars,
        "tool_result_chars": sum(int(t.get("tool_result_chars") or 0) for t in turns),
        "view_chars": len(view_text),
        "view_tokens_est": len(view_text) // 4,
    }


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; ``None`` if singular.

    >>> _solve([[2.0, 0.0], [0.0, 4.0]], [2.0, 8.0])
    [1.0, 2.0]
    """
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _ols(rows: list[list[float]], y: list[float]) -> dict | None:
    """Least squares of ``y`` on ``rows`` (each row already carries its 1 intercept).

    >>> r = _ols([[1.0, 0.0], [1.0, 1.0], [1.0, 2.0]], [1.0, 3.0, 5.0])
    >>> [round(c, 6) for c in r['coef']], round(r['r2'], 6)
    ([1.0, 2.0], 1.0)
    """
    n, k = len(rows), len(rows[0])
    if n <= k:
        return None
    xtx = [[sum(rows[i][a] * rows[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xty = [sum(rows[i][a] * y[i] for i in range(n)) for a in range(k)]
    coef = _solve(xtx, xty)
    if coef is None:
        return None
    pred = [sum(c * v for c, v in zip(coef, row)) for row in rows]
    resid = [yi - pi for yi, pi in zip(y, pred)]
    ybar = mean(y)
    ss_tot = sum((yi - ybar) ** 2 for yi in y)
    ss_res = sum(r * r for r in resid)
    dof = max(1, n - k)
    return {"coef": coef, "n": n,
            "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0,
            "resid_std": (ss_res / dof) ** 0.5}


def _points(judgments) -> list[tuple[dict, dict]]:
    """(features, usage) pairs from stored judgment records; errored calls dropped."""
    out = []
    for j in judgments:
        if not isinstance(j, dict) or j.get("error"):
            continue
        f, u = j.get("features") or {}, j.get("usage") or {}
        if f.get("view_chars") and total_tokens(u):
            out.append((f, u))
    return out


#: A *non-zero* cache read does not mean a retry. Two consecutive judge calls that each
#: took exactly one round trip still read ~1.3k cached tokens: the system prompt and the
#: schema are identical between invocations and the CLI's cache window outlives one call.
#: Only a read comfortably above that block means the *conversation* was re-sent.
FIXED_CACHE_TOKENS = 2000


def round_trips(judgment: dict) -> int:
    """How many API calls one judge call took — recorded, else inferred from the cache.

    ``n_iterations`` (recorded since the CLI's ``usage.iterations`` was captured) is
    authoritative. Older records have only the cache read to go on, and the threshold
    it is compared against is :data:`FIXED_CACHE_TOKENS`, not zero — see there.

    >>> round_trips({'usage': {'n_iterations': 3}})
    3
    >>> round_trips({'usage': {'cache_read_input_tokens': 5197}})
    2
    >>> round_trips({'usage': {'cache_read_input_tokens': 1319}})
    1
    """
    n = (judgment.get("usage") or {}).get("n_iterations") or judgment.get("num_turns")
    if n:
        return int(n)
    cached = (judgment.get("usage") or {}).get("cache_read_input_tokens") or 0
    return 2 if cached > FIXED_CACHE_TOKENS else 1


def _spread(values: list[float]) -> dict:
    return {"mean": mean(values), "std": pstdev(values) if len(values) > 1 else 0.0,
            "n": len(values)}


def fit(judgments) -> dict:
    """Fit input tokens on view size; summarise output tokens, totals and cost.

    ``judgments`` are the records :mod:`astern.lenses.synopsis` stores (each with
    ``features`` and ``usage``). With fewer than :data:`MIN_POINTS_FOR_OLS` points
    the slope is a ratio through the measured intercept, and ``method`` says so.

    >>> fit([])['method']
    'prior'
    >>> fit([{'features': {'view_chars': 4000, 'n_turns': 2},
    ...       'usage': {'input_tokens': 2200, 'output_tokens': 800}}])['method']
    'ratio'
    """
    pts = _points(judgments)
    n = len(pts)
    xs = [float(f["view_chars"]) for f, _ in pts]
    ins = [float(input_tokens(u)) for _, u in pts]
    outs = [float(u.get("output_tokens") or 0) for _, u in pts]
    tots = [i + o for i, o in zip(ins, outs)]
    costs = [float(j.get("cost_usd") or 0.0) for j in judgments
             if isinstance(j, dict) and not j.get("error") and j.get("cost_usd") is not None]
    model: dict = {"n": n, "method": "prior", "models": sorted(
        {str(j.get("model")) for j in judgments if isinstance(j, dict) and j.get("model")})}
    if n == 0:
        b0, b1, resid = PRIOR_INPUT_INTERCEPT, PRIOR_INPUT_PER_CHAR, PRIOR_INPUT_INTERCEPT
        r2 = None
    elif n < MIN_POINTS_FOR_OLS:
        model["method"] = "ratio"
        b0 = PRIOR_INPUT_INTERCEPT
        b1 = max(0.0, mean((i - b0) / x for i, x in zip(ins, xs)))
        resid = pstdev(ins) if n > 1 else 0.25 * mean(ins)
        r2 = None
    else:
        model["method"] = "ols"
        simple = _ols([[1.0, x] for x in xs], ins)
        b0, b1 = simple["coef"]
        resid, r2 = simple["resid_std"], simple["r2"]
        multi = _ols([[1.0, x, float(f["n_turns"])] for (f, _), x in zip(pts, xs)], ins)
        if multi:
            model["input_multi"] = {
                "names": ["1", "view_chars", "n_turns"],
                "coef": multi["coef"], "r2": multi["r2"], "resid_std": multi["resid_std"]}
    model["input"] = {"intercept": b0, "per_char": b1, "per_1k_chars": b1 * 1000,
                      "r2": r2, "resid_std": resid,
                      "reads_as": f"≈ {b1 * 1000:.0f} input tokens per 1k view chars"
                                  f" + {b0:.0f}"}
    model["output"] = _spread(outs) if outs else {"mean": PRIOR_OUTPUT_TOKENS, "std": 0.0, "n": 0}
    model["total"] = _spread(tots) if tots else {"mean": PRIOR_INPUT_INTERCEPT + PRIOR_OUTPUT_TOKENS,
                                                 "std": 0.0, "n": 0}
    model["cost_usd"] = _spread(costs) if costs else {"mean": 0.0, "std": 0.0, "n": 0}
    # Round trips are the variance nobody sees: a structured answer the schema rejects
    # is retried with the whole conversation resent, so one session's input can be four
    # times the view it was built from. Reported separately, so a low overall R² has an
    # explanation rather than a shrug — and so the fix (fewer retries) is measurable.
    ok = [j for j in judgments if isinstance(j, dict) and not j.get("error")
          and (j.get("features") or {}).get("view_chars") and total_tokens(j.get("usage") or {})]
    if ok:
        trips = [float(round_trips(j)) for j in ok]
        model["iterations"] = _spread(trips)
        model["retry_rate"] = sum(1 for t in trips if t > 1) / len(trips)
        one = [(f, u) for (f, u), t in zip(pts, trips) if t <= 1]
        if len(one) >= MIN_POINTS_FOR_OLS:
            single = _ols([[1.0, float(f["view_chars"])] for f, _ in one],
                          [float(input_tokens(u)) for _, u in one])
            if single:
                b = single["coef"]
                model["input_single_pass"] = {
                    "n": len(one), "intercept": b[0], "per_char": b[1],
                    "per_1k_chars": b[1] * 1000, "r2": single["r2"],
                    "resid_std": single["resid_std"],
                    "reads_as": f"≈ {b[1] * 1000:.0f} input tokens per 1k view chars"
                                f" + {b[0]:.0f}, on calls that took one round trip"}
    tot_tokens = sum(tots)
    model["cost_per_total_token"] = (sum(costs) / tot_tokens) if (costs and tot_tokens) else 0.0
    # view_chars per source byte: the proxy for a session that has never been viewed.
    src = [float(f.get("bytes") or 0) for f, _ in pts]
    model["view_chars_per_byte"] = (sum(xs) / sum(src)) if sum(src) else 0.0
    return model


def predict(model: dict, features: dict) -> dict:
    """Predicted tokens (and cost) of judging one session, with a ±1σ band.

    >>> m = fit([])
    >>> p = predict(m, {'view_chars': 10000})
    >>> p['input_tokens'], p['total_tokens'] == p['input_tokens'] + p['output_tokens']
    (3800, True)
    >>> p['low'] <= p['total_tokens'] <= p['high']
    True
    """
    x = float(features.get("view_chars") or 0)
    if not x and model.get("view_chars_per_byte"):
        # The bytes proxy must respect the ceiling the view itself has, or a 17 MB
        # session is priced as if the judge would read all of it. It never does.
        x = min(float(features.get("bytes") or 0) * model["view_chars_per_byte"],
                float(DFLT_MAX_CHARS))
    inp = model["input"]
    y_in = max(0.0, inp["intercept"] + inp["per_char"] * x)
    y_out = max(0.0, float(model["output"]["mean"]))
    total = y_in + y_out
    band = float(inp["resid_std"] or 0.0) + float(model["output"]["std"] or 0.0)
    return {"input_tokens": round(y_in), "output_tokens": round(y_out),
            "total_tokens": round(total),
            "low": round(max(0.0, total - band)), "high": round(total + band),
            "cost_usd": round(total * model.get("cost_per_total_token", 0.0), 6)}
