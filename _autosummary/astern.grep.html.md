# astern.grep

Regex search over raw transcripts, across every home, with no index to maintain.

The index-backed route ([`astern.tools.recall()`](astern.tools.html.md#astern.tools.recall), over `ir`) needs `sync` and
`index` to have run and finds paraphrases; this one reads the JSONL files where
they lie and finds exact things: a name, an identifier, an error string, a URL, a
phrase. It is *structure-aware*: the pattern is matched against the text of the
conversational fields only (the human prompt, the assistant’s prose, optionally the
tool inputs and session titles), never against the raw JSON line, so the
`CLAUDE.md` attachment every session carries, JSON escapes and record keys do not
produce hits.

Two stages keep it fast on gigabytes: `rg -l` picks the files whose raw bytes
could match (a superset, see [`raw_prefilter_pattern()`](#astern.grep.raw_prefilter_pattern)), then only those files
are parsed and matched field by field. Without `rg` every candidate is parsed.

```pycon
>>> import json, tempfile
>>> recs = [
...   {"type": "user", "sessionId": "s1", "cwd": "/p/shop", "timestamp": "2026-10-01T10:00:00Z",
...    "message": {"role": "user", "content": "Where is the Acme-corp agreement?"}},
...   {"type": "assistant", "timestamp": "2026-10-01T10:01:00Z",
...    "message": {"content": [{"type": "text", "text": "In the agreement folder."}]}},
... ]
>>> with tempfile.TemporaryDirectory() as d:
...     p = Path(d, ".claude", "projects", "-p-shop"); p.mkdir(parents=True)
...     _ = Path(p, "s1.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
...     r = grep_sessions("acme\\w*", home=Path(d, ".claude"), use_rg=False)
>>> r["n_sessions"], r["sessions"][0]["n_hits"], r["sessions"][0]["hits"][0]["role"]
(1, 1, 'user')
```

### Module Attributes

| [`ROLES`](#astern.grep.ROLES)   | The fields a hit can come from.   |
|----------------------------------------------------------|-----------------------------------|

### Functions

| [`grep_sessions`](#astern.grep.grep_sessions)(pattern, \*[, home, project, ...])   | Sessions whose conversational text matches the regex `pattern`, best first.     |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| [`raw_prefilter_pattern`](#astern.grep.raw_prefilter_pattern)(pattern)                     | Widen `pattern` so it matches the *raw JSON line* wherever it matches the text. |

### astern.grep.ROLES *= ('user', 'assistant', 'tool', 'title')*

The fields a hit can come from. `user` is the human prompt (wrapper tags and
system reminders stripped), `assistant` the assistant’s prose, `tool` the
tool inputs (commands, file bodies, queries), `title` the session’s titles.

### astern.grep.grep_sessions(pattern, , home='all', project=None, since_days=None, roles='user,assistant,title', kinds='session', ignore_case=True, k=20, context=100, max_snippets=3, sort='recent', use_rg=True)

Sessions whose conversational text matches the regex `pattern`, best first.

`home` defaults to every home ([`ALL_HOMES`](astern.sources.html.md#astern.sources.ALL_HOMES)). `project`
is a case-insensitive substring of the session’s cwd (or project slug).
`roles` picks the fields ([`ROLES`](#astern.grep.ROLES); `all` for every one). `sort` is
`recent` (last activity first) or `hits` (most matches first). The pattern is
a Python regex; `ignore_case` is on by default.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### astern.grep.raw_prefilter_pattern(pattern)

Widen `pattern` so it matches the *raw JSON line* wherever it matches the text.

The real pattern runs on the decoded text of a field; this one only picks the
raw lines worth decoding, so it may over-match but must never under-match. In
JSON a quote, a backslash and control characters are escaped (`\"`, `\\`,
`\n`, `\u001b`), so every atom that could match one of those (`.`, `\s`,
`\W`, `\S`, `\D`, a character class, a literal `"` or `\\`, a control
escape) also accepts any JSON escape. Assertions that cannot hold inside a JSON
string (`\b`, `\B`, `^`, `$`, `\A`, `\Z`) are dropped. Constructs
`rg` cannot compile (lookarounds, backreferences) make the caller fall back to
decoding every line.

```pycon
>>> raw_prefilter_pattern(r'foo\s+bar')
'foo(?:\\\\(?:u[0-9a-fA-F]{4}|[^u])|\\s)+bar'
>>> raw_prefilter_pattern(r'^\bac\b$')
'ac'
>>> raw_prefilter_pattern(r'ab\d+')
'ab\\d+'
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)
