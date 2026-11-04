"""``load_records`` decodes with orjson where it provably agrees with ``json.loads``.

The policy (shared with openloops): orjson when importable, ``json.loads`` for any line
orjson rejects (``NaN``/``Infinity``/``-Infinity``, lone surrogates, huge exponents) and
for any line holding a run of 19+ digits (orjson turns integers beyond 64 bits into
floats where ``json.loads`` returns exact ints). Lines both decoders reject stay skipped.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import fixtures
import fixtures_lenses
import pytest

from astern import sources
from astern.sources import load_records

orjson = pytest.importorskip("orjson")

TESTS = Path(__file__).parent

BIG_INTS = [2**63 - 1, 2**63, 2**64, -(2**63) - 1]


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fixture_transcripts(tmp_path: Path) -> list[Path]:
    """Every transcript the test fixtures can produce, plus any JSONL shipped under tests/."""
    turns = [
        {
            "prompt": "fix the bug",
            "tool_uses": [
                {"name": "Bash", "input": {"command": "ls"}, "result": "a b c"},
                {"name": "Bash", "input": {"command": "false"}, "is_error": True},
            ],
            "repeat_usage": True,
            "duration_ms": 1234,
        },
        {"prompt": "thanks", "prompt_blocks": True, "wrap_reminder": True},
        {"kind": "meta_user", "prompt": "queued"},
    ]
    home = fixtures.write_home(
        tmp_path,
        sessions=[
            {
                "sid": "s1",
                "cwd": "/home/dev/proj/a",
                "turns": turns,
                "meta": [fixtures.ai_title("t"), fixtures.cost_state(totalCostUSD=0.5)],
                "with_subagent": True,
            },
            {"sid": "s2", "cwd": "/home/dev/proj/b", "turns": turns[:1]},
        ],
    )
    paths = sorted((home.home / "projects").rglob("*.jsonl"))
    lenses_path = tmp_path / "lenses.jsonl"
    _write(lenses_path, [json.dumps(r) for r in fixtures_lenses.mk_records()])
    shipped = [p for p in TESTS.rglob("*.jsonl") if "__pycache__" not in p.parts]
    return paths + [lenses_path] + shipped


def test_fixture_transcripts_decode_identically_with_both_decoders(tmp_path):
    paths = _fixture_transcripts(tmp_path)
    assert len(paths) >= 4
    for p in paths:
        want = load_records(p, loads=json.loads)
        got = load_records(p)
        assert want, p
        assert repr(got) == repr(want), p


def test_fixture_lines_agree_line_by_line(tmp_path):
    n = 0
    for p in _fixture_transcripts(tmp_path):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert repr(sources.fast_loads(line)) == repr(json.loads(line))
                n += 1
    assert n > 20


def test_default_decoder_is_orjson_for_ordinary_lines(tmp_path, monkeypatch):
    seen = []

    def spy(s):
        seen.append(s)
        return orjson.loads(s)

    monkeypatch.setattr(sources, "_orjson_loads", spy)
    p = _write(tmp_path / "t.jsonl", ['{"type": "user", "n": 1}', '{"type": "x"}'])
    assert load_records(p) == [{"type": "user", "n": 1}, {"type": "x"}]
    assert len(seen) == 2


@pytest.mark.parametrize("n", BIG_INTS)
def test_integers_beyond_53_bits_stay_exact(tmp_path, n):
    p = _write(
        tmp_path / "t.jsonl",
        [
            json.dumps({"type": "u", "n": n}),
            json.dumps({"type": "u", "nested": {"deep": [1, n, 2]}}),
        ],
    )
    recs = load_records(p)
    assert recs[0]["n"] == n and type(recs[0]["n"]) is int
    assert recs[1]["nested"]["deep"] == [1, n, 2]
    assert repr(recs) == repr(load_records(p, loads=json.loads))


def test_big_int_lines_never_reach_orjson(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        sources, "_orjson_loads", lambda s: seen.append(s) or orjson.loads(s)
    )
    p = _write(
        tmp_path / "t.jsonl",
        [
            '{"n": 9223372036854775807}',  # 19 digits
            '{"n": 18446744073709551616}',  # 20 digits
            '{"f": 0.1234567890123456789}',  # 19 digits after the point
            '{"n": 999999999999999999}',  # 18 digits: safe for orjson
        ],
    )
    recs = load_records(p)
    assert len(recs) == 4
    assert [bytes(x).decode() for x in seen] == ['{"n": 999999999999999999}']


@pytest.mark.parametrize(
    "token, check",
    [
        ("NaN", math.isnan),
        ("Infinity", lambda v: v == float("inf")),
        ("-Infinity", lambda v: v == float("-inf")),
        ("1e999", lambda v: v == float("inf")),
    ],
)
def test_non_finite_numbers_fall_back_to_json(tmp_path, token, check):
    p = _write(tmp_path / "t.jsonl", [f'{{"type": "u", "v": {token}, "k": [{token}]}}'])
    (rec,) = load_records(p)
    assert check(rec["v"]) and check(rec["k"][0])
    assert repr(load_records(p)) == repr(load_records(p, loads=json.loads))


def test_lone_surrogate_escape_falls_back_to_json(tmp_path):
    p = _write(tmp_path / "t.jsonl", [r'{"type": "u", "s": "\ud800"}'])
    assert repr(load_records(p)) == repr(load_records(p, loads=json.loads))
    assert len(load_records(p)) == 1


def test_digit_guard_agrees_with_a_19_digit_regex():
    import random
    import re

    ref = re.compile("[0-9]{19}")
    rnd = random.Random(7)
    for _ in range(20000):
        s = "".join(rnd.choice("0123456789 a,\u0660") for _ in range(rnd.randint(0, 50)))
        s += rnd.choice(["", "9" * 19, "1" * 18 + "x" + "1" * 18])
        want = bool(ref.search(s))
        got = sources._LONG_DIGIT_RUN in s.encode().translate(sources._DIGITS_TO_ONES)
        assert got == want, s


def test_without_orjson_results_are_unchanged(tmp_path, monkeypatch):
    p = _write(
        tmp_path / "t.jsonl",
        [f'{{"type": "u", "n": {2**64}, "v": NaN}}', '{"type": "a"}', "garbage"],
    )
    want = repr(load_records(p, loads=json.loads))
    monkeypatch.setattr(sources, "_orjson_loads", None)
    assert repr(load_records(p)) == want


def test_blank_and_malformed_lines_tolerated_as_before(tmp_path):
    p = _write(
        tmp_path / "t.jsonl",
        [
            '{"type": "user"}',
            "",
            "   ",
            "\t",
            "not json {{{",
            '{"type": "cut", "n": ',  # truncated
            '{"n": 99999999999999999999999 oops}',  # big int *and* malformed
            "[1, 2, 3]",  # valid JSON, not an object
            '"just a string"',
            "42",
            "null",
            '{"type": "assistant"}',
        ],
    )
    assert load_records(p) == [{"type": "user"}, {"type": "assistant"}]
    assert load_records(p) == load_records(p, loads=json.loads)


def test_invalid_utf8_still_replaced_not_dropped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_bytes(b'{"type": "u", "s": "bad \xff byte"}\n')
    (rec,) = load_records(p)
    assert rec["s"] == "bad � byte"


def test_injected_loads_is_used_and_its_value_errors_tolerated(tmp_path):
    calls = []

    def loads(line):
        calls.append(line)
        if "bad" in line:
            raise ValueError("nope")
        return {"seen": line}

    p = _write(tmp_path / "t.jsonl", ["one", "", "bad", "two"])
    assert load_records(p, loads=loads) == [{"seen": "one"}, {"seen": "two"}]
    assert calls == ["one", "bad", "two"]


def test_loads_is_keyword_only(tmp_path):
    p = _write(tmp_path / "t.jsonl", ['{"a": 1}'])
    with pytest.raises(TypeError):
        load_records(p, json.loads)  # type: ignore[misc]
