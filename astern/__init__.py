"""astern — look astern: mine your past Claude Code sessions.

The crow's nest (``crowsnest``) watches the sessions that are running now; astern
looks back at the wake they left. It reads the transcripts Claude Code already writes,
keeps its own store of turns and findings, and answers, without spending tokens twice:
what problems recur, where agents get stuck, what one-off code keeps being rewritten,
what words you and your agents do not share.

Core contract: :mod:`astern.tools` (plain functions, JSON in, JSON out) over
:mod:`astern.sources` → :mod:`astern.turns` → :mod:`astern.store` with
:mod:`astern.ledger` deciding what still needs analyzing, :mod:`astern.lenses`
answering the questions, and :mod:`astern.judge` the one LLM seam.
"""

from astern.tools import sync, sessions, show, lenses  # noqa: F401
from astern.store import Store, MemoryStore, mk_store  # noqa: F401
from astern.judge import claude_judge, replay_judge, Judgment  # noqa: F401
from astern.lenses import LENSES, lens, finding  # noqa: F401

__version__ = "0.0.1"
