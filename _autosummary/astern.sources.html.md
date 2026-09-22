# astern.sources

Where the transcripts are: homes, session files, raw records.

A *home* is one `~/.claude`-shaped directory. The default is the one home this
process would use; a second account (`~/.claude-iq`) or a synced copy of another
machine’s home is just another entry in the list. Nothing here interprets a record;
that is [`astern.turns`](astern.turns.html.md#module-astern.turns) (turn pairs) and, for session-level meaning,
`openloops.transcripts`.

```pycon
>>> h = homes()
>>> [sf.session_id for sf in iter_session_files(h, max_sessions=2)]
```

### Module Attributes

| [`KINDS`](#astern.sources.KINDS)   | How a nested transcript relates to its parent session.   |
|----------------------------------------------------------|----------------------------------------------------------|

### Functions

| [`homes`](#astern.sources.homes)([home])                               | Resolve the `home=` seam to a list of [`Home`](#astern.sources.Home).   |
|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| [`iter_session_files`](#astern.sources.iter_session_files)([home, since_days, ...]) | Yield transcript files across the given homes, newest first.                                   |
| [`load_records`](#astern.sources.load_records)(path)                          | Parse one JSONL transcript, tolerating blank and malformed lines.                              |

### Classes

| [`Home`](#astern.sources.Home)(name, path)                         | One `~/.claude`-shaped directory, named so records can say where they came from.   |
|-------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [`SessionFile`](#astern.sources.SessionFile)(path, home, session_id, ...) | One transcript on disk, with the facts the ledger keys on (`size`, `mtime`).       |

### *class* astern.sources.Home(name, path)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One `~/.claude`-shaped directory, named so records can say where they came from.

### astern.sources.KINDS *= ('session', 'subagent', 'workflow')*

How a nested transcript relates to its parent session. `session` is a top-level
transcript; `subagent` lives under `<sid>/subagents/`; `workflow` under a
`<sid>/wf_*/` directory (the `Workflow` tool’s runs).

### *class* astern.sources.SessionFile(path, home, session_id, project_slug, kind, parent_id, size, mtime)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One transcript on disk, with the facts the ledger keys on (`size`, `mtime`).

### astern.sources.homes(home=None)

Resolve the `home=` seam to a list of [`Home`](#astern.sources.Home).

`None` → the one default home (`$ASTERN_HOME` or `~/.claude`). A string or
path → that one home. An iterable → several. A home’s name is its directory
name with the leading dot dropped (`claude`, `claude-iq`).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`Home`](#astern.sources.Home)]

```pycon
>>> homes('/tmp/.claude-x')[0].name
'claude-x'
```

### astern.sources.iter_session_files(home=None, , since_days=None, projects=None, max_sessions=None, kinds=('session',))

Yield transcript files across the given homes, newest first.

`since_days` keeps files modified within that window; `projects` keeps only
project dirs whose slug contains one of the substrings; `kinds` selects
top-level sessions and/or their nested `subagent` / `workflow` transcripts.
`max_sessions` caps the count after sorting, so it means “the N most recent”.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator)[[`SessionFile`](#astern.sources.SessionFile)]

### astern.sources.load_records(path)

Parse one JSONL transcript, tolerating blank and malformed lines.

Unknown record types are kept as-is: the format drifts between Claude Code
versions and a parser that drops what it does not recognise loses data silently.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
