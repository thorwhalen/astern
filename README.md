# astern

**Look astern: mine your past Claude Code sessions.**

`crowsnest` watches the sessions running now. `astern` looks back at the wake they left: the transcripts Claude Code already keeps under `~/.claude/projects/`. It answers, without spending a token twice, what problems recur, where agents get stuck, what one-off code keeps being rewritten, and which words you and your agents do not share.

```bash
pip install astern
astern sync                      # read new or changed transcripts, run the free lenses
astern report friction           # where agents got stuck, with the turns as evidence
```

Work in progress. The plan this is built from lives in the `ai` group's docs (session-mining-plan.md).
