"""astern — look astern: mine your past Claude Code sessions.

The crow's nest (``crowsnest``) watches the sessions that are running now; astern
looks back at the wake they left. It reads the transcripts Claude Code already writes,
keeps its own store of turns and findings, and answers, without spending tokens twice:
what problems recur, where agents get stuck, what one-off code keeps being rewritten,
what words you and your agents do not share.

Core contract: :mod:`astern.tools` (plain functions, JSON in, JSON out) over
:mod:`astern.sources` → :mod:`astern.turns` → :mod:`astern.store` with
:mod:`astern.ledger` deciding what still needs analyzing, :mod:`astern.lenses`
answering the questions, :mod:`astern.provenance` tracing a line of code back to the
turn that wrote it, and :mod:`astern.judge` the one LLM seam.
"""

from astern.judge import Judgment, claude_judge, replay_judge  # noqa: F401
from astern.lenses import LENSES, finding, lens  # noqa: F401
from astern.store import MemoryStore, Store, mk_store  # noqa: F401
from astern.tools import install_skills, lenses, sessions, show, sync, why  # noqa: F401

# `recall` and `index` are deliberately NOT re-exported here: binding them on the
# package would shadow the :mod:`astern.recall` module, and `import astern.recall
# as r` would then hand back a *function* — a trap that fails much later than it
# is made. Reach them as `from astern.recall import recall, index` (or through
# :mod:`astern.tools`, which is what the CLI dispatches).

__version__ = "0.0.1"
