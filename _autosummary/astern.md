# astern

astern — look astern: mine your past Claude Code sessions.

The crow’s nest (`crowsnest`) watches the sessions that are running now; astern
looks back at the wake they left. It reads the transcripts Claude Code already writes,
keeps its own store of turns and findings, and answers, without spending tokens twice:
what problems recur, where agents get stuck, what one-off code keeps being rewritten,
what words you and your agents do not share.

Core contract: [`astern.tools`](astern.tools.md#module-astern.tools) (plain functions, JSON in, JSON out) over
[`astern.sources`](astern.sources.md#module-astern.sources) → [`astern.turns`](astern.turns.md#module-astern.turns) → [`astern.store`](astern.store.md#module-astern.store) with
[`astern.ledger`](astern.ledger.md#module-astern.ledger) deciding what still needs analyzing, [`astern.lenses`](astern.lenses.md#astern.lenses)
answering the questions, [`astern.provenance`](astern.provenance.md#module-astern.provenance) tracing a line of code back to the
turn that wrote it, and [`astern.judge`](astern.judge.md#module-astern.judge) the one LLM seam.

### Modules

| [`entire`](astern.entire.md#module-astern.entire)         | Enable and inspect the [Entire CLI](https://github.com/entireio/cli) safely, per repo.   |
|--------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| [`estimate`](astern.estimate.md#module-astern.estimate)     | What will judging this batch cost? — features, a fitted model, a prediction.             |
| [`judge`](astern.judge.md#module-astern.judge)           | The `judge=` seam: an LLM call that returns text or JSON *and says what it cost*.        |
| [`ledger`](astern.ledger.md#module-astern.ledger)         | Idempotency: what has already been analyzed, so no token is spent twice.                 |
| [`lenses`](astern.lenses.md#astern.lenses)()              | The registered lenses: name, kind (H = heuristic, L = LLM-judged), version, one line.    |
| [`provenance`](astern.provenance.md#module-astern.provenance) | From a line of code back to the session turn that wrote it.                              |
| [`recall`](astern.recall.md#module-astern.recall)         | Recall: what did past sessions already think, try and decide about X?                    |
| [`report`](astern.report.md#module-astern.report)         | Cross-session reports: one aggregator + markdown renderer per lens.                      |
| [`skills`](astern.skills.md#module-astern.skills)         | The skills astern ships, and the command that makes an agent host see them.              |
| [`sources`](astern.sources.md#module-astern.sources)       | Where the transcripts are: homes, session files, raw records.                            |
| [`store`](astern.store.md#module-astern.store)           | Where records, findings and the ledger live: one directory, several JSON stores.         |
| [`tools`](astern.tools.md#module-astern.tools)           | The SSOT of what astern does: plain functions, JSON-able in, JSON-able dict out.         |
| [`turns`](astern.turns.md#module-astern.turns)           | From raw transcript records to one record per user→assistant turn, plus session meta.    |
| [`views`](astern.views.md#module-astern.views)           | The compressed markdown a judge reads instead of a transcript.                           |
