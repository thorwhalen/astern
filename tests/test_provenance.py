"""Tests for astern.provenance and tools.why: the code→session chain, link by link.

The synthetic corpus mirrors the real shapes that make this hard: a subagent
transcript whose records carry the *parent's* ``sessionId``, and a commit trailer
whose id is the claude.ai session, not the local one.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fixtures import bridge_session, write_home

from astern import provenance as prov
from astern import tools

LINE = "    return sum(int(usage.get(k) or 0) for k in INPUT_KEYS)"
BRIDGE = "01G5vonGySLGRrgL4GkxN95c"


# --- trailer parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "message, expected",
    [
        ("feat: x\n\nClaude-Session: https://claude.ai/code/session_01AB", "01AB"),
        ("feat: x\n\nclaude-session: session_01AB", "01AB"),
        ("feat: x\n\nCo-Authored-By: nobody", None),
        ("", None),
    ],
)
def test_session_id_from_trailer(message, expected):
    assert prov.session_id_from_trailer(message) == expected


def test_trailer_id_is_not_the_local_session_id():
    """The trailer names the claude.ai session; only ``bridge_key`` makes them meet."""
    trailer = prov.session_id_from_trailer(
        f"x\n\nClaude-Session: https://claude.ai/code/session_{BRIDGE}"
    )
    assert trailer == BRIDGE
    assert prov.bridge_key(f"cse_{BRIDGE}") == prov.bridge_key(f"session_{BRIDGE}")
    assert prov.bridge_key(BRIDGE) == BRIDGE


# --- normalization ------------------------------------------------------------


def test_normalize_ignores_indentation_and_reflow():
    assert prov.normalize(LINE) == prov.normalize(LINE.strip().replace(" ", "  "))


def test_loose_survives_a_formatter_and_a_renumbering():
    assert prov.loose("f('a', 12)") == prov.loose('f("a", 300)')
    assert prov.loose("f('a')") != prov.loose("g('a')")


# --- ranking ------------------------------------------------------------------


def test_rank_hits_prefers_exact_then_writing_tool_then_earliest():
    hits = [
        {"match": "exact", "tool": "Bash", "timestamp": "2026-01-02"},
        {"match": "loose", "tool": "Write", "timestamp": "2026-01-01"},
        {"match": "exact", "tool": "Write", "timestamp": "2026-01-05"},
        {"match": "exact", "tool": "Write", "timestamp": "2026-01-03"},
    ]
    ranked = prov.rank_hits(hits)
    assert [(h["tool"], h["timestamp"]) for h in ranked] == [
        ("Write", "2026-01-03"),
        ("Write", "2026-01-05"),
        ("Bash", "2026-01-02"),
        ("Write", "2026-01-01"),
    ]


# --- the subagent join --------------------------------------------------------


def _home_with_subagent_write(tmp_path, cwd, *, bridge=True):
    """A session that delegated: the parent talks, the subagent writes ``LINE``."""
    meta = [bridge_session("s-main", f"cse_{BRIDGE}")] if bridge else []
    return write_home(
        tmp_path,
        sessions=[
            {
                "sid": "s-main",
                "cwd": cwd,
                "meta": meta,
                "turns": [{"prompt": "add the token sum", "final_text": "delegated"}],
                "with_subagent": True,
                "subagent_turns": [
                    {
                        "prompt": "write judge.py",
                        "tool_uses": [
                            {
                                "name": "Write",
                                "input": {
                                    "file_path": f"{cwd}/mod.py",
                                    "content": f"X = 1\n{LINE}\n",
                                },
                            }
                        ],
                        "final_text": "written",
                    }
                ],
            }
        ],
    )


def test_subagent_is_stored_under_its_own_id_with_its_parent(tmp_path, store):
    """The records say ``sessionId: s-main``; the store must not believe them."""
    w = _home_with_subagent_write(tmp_path, "/tmp/proj-sub")
    tools.sync(str(w.home), kinds="session,subagent", store=store)
    assert set(store.sessions) == {"s-main", "agent-x"}
    assert store.sessions["agent-x"]["session_id"] == "agent-x"
    assert store.sessions["agent-x"]["parent_id"] == "s-main"
    assert store.sessions["s-main"]["parent_id"] is None
    assert prov.with_subagents(store, ["s-main"]) == ["s-main", "agent-x"]


def test_bridge_session_id_is_kept_on_the_session_record(tmp_path, store):
    w = _home_with_subagent_write(tmp_path, "/tmp/proj-bridge")
    tools.sync(str(w.home), kinds="session,subagent", store=store)
    assert store.sessions["s-main"]["bridge_session_id"] == f"cse_{BRIDGE}"
    assert prov.sessions_for_bridge(store, BRIDGE) == ["s-main"]


def test_hits_report_the_subagent_and_name_its_parent(tmp_path, store):
    w = _home_with_subagent_write(tmp_path, "/tmp/proj-hits")
    tools.sync(str(w.home), kinds="session,subagent", store=store)
    hits = list(prov.iter_hits(store, prov.with_subagents(store, ["s-main"]), LINE))
    assert [(h["session_id"], h["parent_id"], h["tool"]) for h in hits] == [
        ("agent-x", "s-main", "Write")
    ]
    assert hits[0]["match"] == "exact"
    assert hits[0]["user_prompt"] == "write judge.py"


def test_a_line_only_the_parent_was_synced_is_not_found(tmp_path, store):
    """Without ``kinds='session,subagent'`` the line is simply not in the corpus."""
    w = _home_with_subagent_write(tmp_path, "/tmp/proj-parent-only")
    tools.sync(str(w.home), store=store)
    assert list(prov.iter_hits(store, ["s-main"], LINE)) == []


def test_too_short_a_needle_matches_nothing(tmp_path, store):
    w = _home_with_subagent_write(tmp_path, "/tmp/proj-short")
    tools.sync(str(w.home), kinds="session,subagent", store=store)
    assert list(prov.iter_hits(store, ["agent-x"], "X = 1")) == []


# --- end to end, over a real git repo -----------------------------------------

pytestmark_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _git_repo(tmp_path, *, trailer: bool):
    """A one-commit repo whose file holds ``LINE``, with or without the trailer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
    }

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (repo / "mod.py").write_text(f"X = 1\n{LINE}\n", encoding="utf-8")
    git("add", "mod.py")
    message = "feat: sum the input tokens"
    if trailer:
        message += f"\n\nClaude-Session: https://claude.ai/code/session_{BRIDGE}"
    git("commit", "-q", "-m", message)
    return repo


@pytestmark_git
def test_why_follows_the_trailer_into_the_subagent(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=True)
    w = _home_with_subagent_write(tmp_path, str(repo))
    tools.sync(str(w.home), kinds="session,subagent", store=store)

    out = tools.why(f"{repo}/mod.py:2", store=store, entire=False)

    assert out["file"] == "mod.py"
    assert out["line"] == 2
    assert out["text"] == LINE
    assert out["session_id"] == BRIDGE
    assert out["commits"]["blame"]["session_id"] == BRIDGE
    assert out["commits"]["introduced"]["sha"] == out["commits"]["blame"]["sha"]
    top = out["hits"][0]
    assert (top["session_id"], top["parent_id"], top["tool"]) == (
        "agent-x",
        "s-main",
        "Write",
    )
    assert out["entire"] is None


@pytestmark_git
def test_why_falls_back_to_a_repo_scan_when_there_is_no_trailer(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=False)
    w = _home_with_subagent_write(tmp_path, str(repo), bridge=False)
    tools.sync(str(w.home), kinds="session,subagent", store=store)

    out = tools.why(f"{repo}/mod.py:2", store=store, entire=False, window_days=3650)

    assert out["session_id"] is None
    assert any("no Claude-Session trailer" in n for n in out["notes"])
    assert out["hits"][0]["session_id"] == "agent-x"


@pytestmark_git
def test_why_by_commit_matches_the_files_the_commit_touched(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=True)
    w = _home_with_subagent_write(tmp_path, str(repo))
    tools.sync(str(w.home), kinds="session,subagent", store=store)

    out = tools.why("HEAD", commit=True, repo=str(repo), store=store, entire=False)

    assert out["file"] is None and out["line"] is None
    assert out["session_id"] == BRIDGE
    assert out["hits"][0]["session_id"] == "agent-x"


@pytestmark_git
def test_why_says_when_the_store_has_no_subagents(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=True)
    w = _home_with_subagent_write(tmp_path, str(repo))
    tools.sync(str(w.home), store=store)  # sessions only

    out = tools.why(f"{repo}/mod.py:2", store=store, entire=False)

    assert out["hits"] == []
    assert any("--kinds session,subagent" in n for n in out["notes"])


@pytestmark_git
def test_why_reads_a_bare_revision_as_a_commit(tmp_path, store):
    """No such file, but it resolves as a revision — so ``--commit`` is not needed."""
    repo = _git_repo(tmp_path, trailer=True)
    w = _home_with_subagent_write(tmp_path, str(repo))
    tools.sync(str(w.home), kinds="session,subagent", store=store)

    out = tools.why("HEAD", repo=str(repo), store=store, entire=False)

    assert out["file"] is None
    assert out["session_id"] == BRIDGE


@pytestmark_git
def test_why_rejects_a_target_that_is_neither_a_file_nor_a_revision(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=True)
    with pytest.raises(ValueError):
        tools.why("nope.py", repo=str(repo), store=store, entire=False)


@pytestmark_git
def test_why_needs_a_line_for_a_file_target(tmp_path, store):
    repo = _git_repo(tmp_path, trailer=True)
    with pytest.raises(ValueError, match="needs a line"):
        tools.why("mod.py", repo=str(repo), store=store, entire=False)
