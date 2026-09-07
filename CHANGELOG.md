# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.0.1] - 2026-09-07

### Added

- Skeleton package: `sources.py` (homes, session files, raw records), `turns.py` (records → turn records + `session_meta`), `store.py` (`dol`-backed JSON stores under `~/.local/share/astern/`), `ledger.py` (per-session, per-lens idempotency: skip / incremental / full), `judge.py` (the `claude` CLI as the `L`-lens judge, plus a replay judge for tests), `lenses/` registry with the `stats` lens.
- `tools.py`: `sync`, `sessions`, `show`, `lenses` — the SSOT verbs, exposed as the `astern` CLI via `cw`.
- Test suite (`pytest`, `pytest --doctest-modules astern`) with a synthetic-transcript fixture builder; no real transcript is ever committed.
- README describing the pitch, the lens catalogue, the ledger, the seams, and what's deliberately not in astern.
