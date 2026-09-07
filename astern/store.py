"""Where records, findings and the ledger live: one directory, several JSON stores.

The ``store=`` seam. The default is files under ``~/.local/share/astern/`` (or
``$ASTERN_DATA_DIR``) through ``dol.JsonFiles``, which is a real store, not a stub:
it survives restarts, is greppable, and a session's turns are one file you can open.
Any other ``MutableMapping`` of the same key shape serves the same code — a dict for
tests, an S3 store when the corpus outgrows a laptop.

Key shapes (all end in ``.json`` on disk; keys are relative paths):

- ``sessions``   ``<sid>``                   session meta (see :func:`astern.turns.session_meta`) + source facts
- ``turns``      ``<sid>``                   the list of turn records of that session
- ``findings``   ``<lens>/<sid>``            the list of findings one lens produced for one session
- ``ledger``     ``<sid>``                   what has been analyzed, by which lens version, through which turn
- ``judgments``  ``<lens>/<sid>/<n>``        raw judge calls: prompt size, output, usage — the cost model's data

>>> s = MemoryStore()
>>> s.sessions['abc'] = {'title': 'x'}; s.sessions['abc']['title']
'x'
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

KINDS = ("sessions", "turns", "findings", "ledger", "judgments")


def data_dir(root: str | Path | None = None) -> Path:
    """``root`` → ``$ASTERN_DATA_DIR`` → ``~/.local/share/astern``."""
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get("ASTERN_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share" / "astern"


def _json_files(directory: Path) -> MutableMapping:
    from dol import JsonFiles, mk_dirs_if_missing

    directory.mkdir(parents=True, exist_ok=True)
    return mk_dirs_if_missing(JsonFiles(str(directory)))


class _Suffixed(MutableMapping):
    """Add the ``.json`` suffix on the way in and hide it on the way out."""

    def __init__(self, inner: MutableMapping, suffix: str = ".json"):
        self._inner, self._suffix = inner, suffix

    def _k(self, key: str) -> str:
        return key if key.endswith(self._suffix) else key + self._suffix

    def __getitem__(self, key):
        return self._inner[self._k(key)]

    def __setitem__(self, key, value):
        self._inner[self._k(key)] = value

    def __delitem__(self, key):
        del self._inner[self._k(key)]

    def __iter__(self):
        for k in self._inner:
            yield k.removesuffix(self._suffix)

    def __len__(self):
        return len(self._inner)

    def __contains__(self, key):
        return self._k(key) in self._inner


@dataclass
class Store:
    """The five stores, lazily opened under one data directory."""

    root: Path = field(default_factory=data_dir)

    def __post_init__(self):
        self.root = data_dir(self.root)

    def _open(self, kind: str) -> MutableMapping:
        return _Suffixed(_json_files(self.root / kind))

    @cached_property
    def sessions(self) -> MutableMapping:
        return self._open("sessions")

    @cached_property
    def turns(self) -> MutableMapping:
        return self._open("turns")

    @cached_property
    def findings(self) -> MutableMapping:
        return self._open("findings")

    @cached_property
    def ledger(self) -> MutableMapping:
        return self._open("ledger")

    @cached_property
    def judgments(self) -> MutableMapping:
        return self._open("judgments")


class MemoryStore(Store):
    """The same five stores as dicts; what tests and dry runs use."""

    def __init__(self):
        self.root = Path("<memory>")

    def _open(self, kind: str) -> MutableMapping:
        return {}


def mk_store(store: Store | str | Path | None = None) -> Store:
    """Resolve the ``store=`` seam: a :class:`Store`, a directory, or the default."""
    if isinstance(store, Store):
        return store
    return Store(root=data_dir(store))
