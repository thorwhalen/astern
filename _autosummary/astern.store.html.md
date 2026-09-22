# astern.store

Where records, findings and the ledger live: one directory, several JSON stores.

The `store=` seam. The default is files under `~/.local/share/astern/` (or
`$ASTERN_DATA_DIR`) through `dol.JsonFiles`, which is a real store, not a stub:
it survives restarts, is greppable, and a session’s turns are one file you can open.
Any other `MutableMapping` of the same key shape serves the same code — a dict for
tests, an S3 store when the corpus outgrows a laptop.

Key shapes (all end in `.json` on disk; keys are relative paths):

- `sessions`   `<sid>`                   session meta (see [`astern.turns.session_meta()`](astern.turns.html.md#astern.turns.session_meta)) + source facts
- `turns`      `<sid>`                   the list of turn records of that session
- `findings`   `<lens>/<sid>`            the list of findings one lens produced for one session
- `ledger`     `<sid>`                   what has been analyzed, by which lens version, through which turn
- `judgments`  `<lens>/<sid>/<n>`        raw judge calls: prompt size, output, usage — the cost model’s data

```pycon
>>> s = MemoryStore()
>>> s.sessions['abc'] = {'title': 'x'}; s.sessions['abc']['title']
'x'
```

### Functions

| [`data_dir`](#astern.store.data_dir)([root])   | `root` → `$ASTERN_DATA_DIR` → `~/.local/share/astern`.                                                            |
|---------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| [`mk_store`](#astern.store.mk_store)([store])  | Resolve the `store=` seam: a [`Store`](#astern.store.Store), a directory, or the default. |

### Classes

| [`MemoryStore`](#astern.store.MemoryStore)()   | The same five stores as dicts; what tests and dry runs use.   |
|------------------------------------------------------------------|---------------------------------------------------------------|
| [`Store`](#astern.store.Store)([root])   | The five stores, lazily opened under one data directory.      |

### *class* astern.store.MemoryStore

Bases: [`Store`](#astern.store.Store)

The same five stores as dicts; what tests and dry runs use.

### *class* astern.store.Store(root=<factory>)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The five stores, lazily opened under one data directory.

### astern.store.data_dir(root=None)

`root` → `$ASTERN_DATA_DIR` → `~/.local/share/astern`.

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### astern.store.mk_store(store=None)

Resolve the `store=` seam: a [`Store`](#astern.store.Store), a directory, or the default.

* **Return type:**
  [`Store`](#astern.store.Store)
