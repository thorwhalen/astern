"""Tests for astern.grep and the all-homes seam: regex search across accounts and mirrors."""

from __future__ import annotations

import shutil

import pytest
from fixtures import write_home

from astern import tools
from astern.grep import grep_sessions, raw_prefilter_pattern
from astern.sources import ALL_HOMES, discover_homes, homes

# A reminder-wrapped prompt and an attachment-like meta line both mention the word, to
# prove the match is field-aware: neither may produce a hit.
SESSIONS_MAIN = [
    {
        "sid": "main-1",
        "cwd": "/p/shop",
        "turns": [
            {"prompt": "Where is the Acme-corp agreement?", "final_text": "Found it."},
            {"prompt": "x", "wrap_reminder": True, "final_text": "ok"},
        ],
        "meta": [{"type": "ai-title", "aiTitle": "agreement hunt"}],
    },
    {
        "sid": "main-2",
        "cwd": "/p/other",
        "turns": [{"prompt": "unrelated", "final_text": "nothing here"}],
    },
]
SESSIONS_IQ = [
    {
        "sid": "iq-1",
        "cwd": "/p/shop",
        "turns": [{"prompt": "draft", "final_text": 'The "acmecorp"\nventure memo.'}],
    }
]
SESSIONS_TW = [
    {
        "sid": "tw-1",
        "cwd": "/root/py/proj/venture",
        "turns": [
            {
                "prompt": "deploy",
                "tool_uses": [{"name": "Bash", "input": {"command": "echo acmecorp"}}],
                "final_text": "deployed",
            }
        ],
    }
]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake machine: two account homes under ``root`` and one mirrored remote host."""
    root = tmp_path / "root"
    write_home(root, sessions=SESSIONS_MAIN, name=".claude")
    write_home(root, sessions=SESSIONS_IQ, name=".claude-iq")
    write_home(tmp_path / "mirrors", sessions=SESSIONS_TW, name="tw")
    monkeypatch.setenv("ASTERN_MIRRORS", str(tmp_path / "mirrors"))
    return root


def test_discover_homes_finds_accounts_and_mirrors(machine):
    names = [h.name for h in discover_homes(root=machine)]
    assert names == ["claude", "claude-iq", "tw"]


def test_homes_all_respects_env_pin(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTERN_HOME", str(tmp_path / ".claude-pinned"))
    assert [h.name for h in homes(ALL_HOMES)] == ["claude-pinned"]


def test_homes_none_unchanged_by_all_homes(monkeypatch):
    monkeypatch.delenv("ASTERN_HOME", raising=False)
    assert [h.name for h in homes(None)] == ["claude"]


@pytest.mark.parametrize("use_rg", [False, True])
def test_grep_across_homes_is_field_aware(machine, use_rg):
    if use_rg and not shutil.which("rg"):
        pytest.skip("rg not installed")
    hs = discover_homes(root=machine)
    r = grep_sessions(r"acme[- ]?corp", home=hs, use_rg=use_rg)
    by_id = {s["session_id"]: s for s in r["sessions"]}
    # main (user prompt, with a hyphen) and iq (assistant prose with a quote and a
    # newline, i.e. JSON escapes in the raw line) match; tw's hit is a tool input,
    # which is not searched by default.
    assert set(by_id) == {"main-1", "iq-1"}
    assert by_id["main-1"]["home"] == "claude"
    assert by_id["main-1"]["hits"][0]["role"] == "user"
    assert by_id["main-1"]["title"] == "agreement hunt"
    assert by_id["iq-1"]["home"] == "claude-iq"
    assert by_id["iq-1"]["cwd"] == "/p/shop"
    r = grep_sessions("acmecorp", home=hs, roles="all", use_rg=use_rg)
    assert {s["session_id"] for s in r["sessions"]} == {"iq-1", "tw-1"}
    tw = next(s for s in r["sessions"] if s["session_id"] == "tw-1")
    assert tw["home"] == "tw" and tw["hits"][0]["role"] == "tool"


def test_grep_escapes_in_raw_line(machine):
    """A pattern spanning a newline/quote in the text must survive the raw prefilter."""
    hs = discover_homes(root=machine)
    r = grep_sessions(r'"acmecorp"\s+venture', home=hs)
    assert [s["session_id"] for s in r["sessions"]] == ["iq-1"]


def test_grep_project_and_title_filters(machine):
    hs = discover_homes(root=machine)
    assert grep_sessions("agreement", home=hs, project="other")["n_sessions"] == 0
    r = grep_sessions("agreement hunt", home=hs, roles="title")
    assert [s["session_id"] for s in r["sessions"]] == ["main-1"]


def test_grep_rejects_unknown_role(machine):
    with pytest.raises(ValueError):
        grep_sessions("x", home=discover_homes(root=machine), roles="bogus")


def test_raw_prefilter_pattern_widens_escapes():
    assert raw_prefilter_pattern(r"a\sb") == r"a[\s\\nrt]b"
    assert raw_prefilter_pattern('"x"') == r'\\?"x\\?"'


def test_tools_grep_does_not_pull_when_pinned(machine, monkeypatch):
    monkeypatch.setenv("ASTERN_HOME", str(machine / ".claude"))
    r = tools.grep("acme")
    assert r["pulled"] is None
    assert [s["session_id"] for s in r["sessions"]] == ["main-1"]


def test_word_boundary_after_escaped_newline(tmp_path):
    r"""``\bterm`` at the start of a line inside a message: raw JSON has ``\nterm``."""
    home = write_home(
        tmp_path,
        sessions=[
            {
                "sid": "nl-1",
                "cwd": "/p/x",
                "turns": [{"prompt": "notes", "final_text": "first line\ntp agreement"}],
            }
        ],
    ).home
    r = grep_sessions(r"^ac\b", home=home)
    assert r["n_sessions"] == 0  # ^ anchors to the start of the whole message
    r = grep_sessions(r"\btp\b", home=home)
    assert [s["session_id"] for s in r["sessions"]] == ["nl-1"]
