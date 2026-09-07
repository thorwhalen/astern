"""Tests for astern.turns: turn boundaries, cleaning, usage de-dup, session_meta."""

from __future__ import annotations

from fixtures import (
    agent_name,
    ai_title,
    cost_state,
    custom_title,
    mk_records,
    permission_mode,
    pr_link,
)

from astern.turns import clean_prompt, iter_turns, session_meta

# --- turn boundaries -----------------------------------------------------------


def test_tool_result_does_not_start_a_new_turn():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {
                "prompt": "fix the bug",
                "tool_uses": [{"name": "Bash", "input": {"command": "pytest"}, "is_error": True}],
                "final_text": "fixed it",
            }
        ],
    )
    turns = list(iter_turns(recs))
    assert len(turns) == 1
    assert turns[0]["n_tool_calls"] == 1
    assert turns[0]["n_errors"] == 1
    assert turns[0]["assistant_summary"] == "fixed it"


def test_a_real_prompt_starts_a_new_turn():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {"prompt": "first ask", "final_text": "first answer"},
            {"prompt": "second ask", "final_text": "second answer"},
        ],
    )
    turns = list(iter_turns(recs))
    assert len(turns) == 2
    assert [t["user_prompt"] for t in turns] == ["first ask", "second ask"]
    assert [t["assistant_summary"] for t in turns] == ["first answer", "second answer"]


def test_meta_user_line_does_not_start_a_turn_and_is_excluded():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {"prompt": "real prompt", "final_text": "ack"},
            {"kind": "meta_user", "prompt": "queued unattended prompt"},
            {"prompt": "another real prompt", "final_text": "ack2"},
        ],
    )
    turns = list(iter_turns(recs))
    assert len(turns) == 2
    prompts = " ".join(t["user_prompt"] for t in turns)
    assert "queued unattended prompt" not in prompts


# --- clean_prompt ----------------------------------------------------------


def test_clean_prompt_strips_system_reminder():
    assert clean_prompt("<system-reminder>x</system-reminder> hello  world") == "hello world"


def test_clean_prompt_strips_multiple_wrapper_tags():
    text = "<command-name>foo</command-name><command-args>bar</command-args> do the thing"
    assert clean_prompt(text) == "do the thing"


def test_clean_prompt_leaves_plain_prose_untouched():
    assert clean_prompt("just a normal prompt") == "just a normal prompt"


def test_prompt_content_as_blocks_and_reminder_wrapped():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[{"prompt": "please help", "prompt_blocks": True, "wrap_reminder": True, "final_text": "ok"}],
    )
    turns = list(iter_turns(recs))
    assert turns[0]["user_prompt"] == "please help"


# --- usage de-dup by message.id ---------------------------------------------


def test_usage_deduped_by_message_id():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {
                "prompt": "do a thing",
                "tool_uses": [{"name": "Bash", "input": {"command": "ls"}}],
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "repeat_usage": True,
                "final_text": None,
            }
        ],
    )
    turns = list(iter_turns(recs))
    usage = turns[0]["usage"]
    # Two assistant lines share the same message.id and usage: must not be doubled.
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["api_calls"] == 1


def test_usage_sums_distinct_message_ids():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {
                "prompt": "do two things",
                "tool_uses": [{"name": "Bash", "input": {"command": "ls"}}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "final_text": "done",
                "final_usage": {"input_tokens": 7, "output_tokens": 3},
            }
        ],
    )
    turns = list(iter_turns(recs))
    usage = turns[0]["usage"]
    assert usage["input_tokens"] == 17
    assert usage["output_tokens"] == 8
    assert usage["api_calls"] == 2


# --- tools list --------------------------------------------------------------


def test_tools_list_records_is_error_and_result_chars():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {
                "prompt": "run two commands",
                "tool_uses": [
                    {"name": "Bash", "input": {"command": "ls"}, "result": "ok", "is_error": False},
                    {"name": "Bash", "input": {"command": "boom"}, "result": "traceback!!", "is_error": True},
                ],
                "final_text": "done",
            }
        ],
    )
    turns = list(iter_turns(recs))
    tools = turns[0]["tools"]
    assert len(tools) == 2
    assert tools[0]["is_error"] is False
    assert tools[0]["result_chars"] == len("ok")
    assert tools[1]["is_error"] is True
    assert tools[1]["result_chars"] == len("traceback!!")
    assert turns[0]["n_errors"] == 1


# --- system records ------------------------------------------------------------


def test_system_records_kept_turn_duration_and_compact_boundary():
    recs = mk_records(
        "s1", "/tmp/p",
        turns=[
            {
                "prompt": "long task",
                "duration_ms": 45000,
                "compact_boundary": {"preTokens": 190000, "postTokens": 4000},
                "final_text": "done",
            }
        ],
    )
    turns = list(iter_turns(recs))
    subtypes = {s["subtype"] for s in turns[0]["system"]}
    assert subtypes == {"turn_duration", "compact_boundary"}
    dur = next(s for s in turns[0]["system"] if s["subtype"] == "turn_duration")
    assert dur["durationMs"] == 45000
    cb = next(s for s in turns[0]["system"] if s["subtype"] == "compact_boundary")
    assert cb["compactMetadata"] == {"preTokens": 190000, "postTokens": 4000}


# --- session_meta ------------------------------------------------------------


def test_session_meta_title_precedence_custom_over_ai():
    recs = mk_records(
        "s1", "/tmp/proj",
        turns=[{"prompt": "hi", "final_text": "hey"}],
        meta=[ai_title("AI-generated title"), custom_title("My real title")],
    )
    meta = session_meta(recs)
    assert meta["title"] == "My real title"
    assert meta["ai_title"] == "AI-generated title"
    assert meta["custom_title"] == "My real title"


def test_session_meta_title_falls_back_to_ai_title():
    recs = mk_records(
        "s1", "/tmp/proj",
        turns=[{"prompt": "hi", "final_text": "hey"}],
        meta=[ai_title("only an ai title")],
    )
    meta = session_meta(recs)
    assert meta["title"] == "only an ai title"


def test_session_meta_prs_cost_state_agent_name_permission_mode():
    recs = mk_records(
        "s1", "/tmp/proj",
        turns=[{"prompt": "hi", "final_text": "hey"}],
        meta=[
            pr_link(42, "https://github.com/o/r/pull/42", "o/r"),
            cost_state(totalCostUSD=1.23, modelUsage={"claude-haiku": {"input": 10}}),
            agent_name("my-agent"),
            permission_mode("acceptEdits"),
        ],
    )
    meta = session_meta(recs)
    assert meta["prs"] == [{"number": 42, "url": "https://github.com/o/r/pull/42", "repo": "o/r"}]
    assert meta["cost_state"]["totalCostUSD"] == 1.23
    assert meta["agent_name"] == "my-agent"
    assert meta["permission_mode"] == "acceptEdits"


def test_session_meta_version_cwd_project_and_record_types():
    recs = mk_records("s1", "/tmp/some-proj", turns=[{"prompt": "hi", "final_text": "hey"}], version="9.9.9")
    meta = session_meta(recs)
    assert meta["session_id"] == "s1"
    assert meta["cwd"] == "/tmp/some-proj"
    assert meta["project"] == "some-proj"
    assert meta["version"] == "9.9.9"
    assert meta["record_types"]["user"] >= 1
    assert meta["record_types"]["assistant"] >= 1
    assert meta["n_records"] == len(recs)
