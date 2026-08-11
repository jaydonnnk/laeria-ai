"""_parse_json_lenient against the ways models actually malform JSON.

The first case is verbatim from a live run: deepseek emitted a stray opening
brace before the real object, and the old greedy regex — spanning first brace
to last — failed on precisely the input it existed to rescue.
"""
from __future__ import annotations

import pytest

from services.llm import _parse_json_lenient


def test_stray_leading_brace_the_case_seen_in_production():
    raw = '{\n{"subreddits": ["snowboarding", "ShredditGirls"], "search_queries": ["oxygen snowboard review"]}'
    assert _parse_json_lenient(raw) == {
        "subreddits": ["snowboarding", "ShredditGirls"],
        "search_queries": ["oxygen snowboard review"],
    }


def test_clean_json_still_parses():
    assert _parse_json_lenient('{"a": 1}') == {"a": 1}


def test_markdown_fences():
    assert _parse_json_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_before_and_after():
    raw = 'Here is the brief:\n{"confidence": "low"}\nHope that helps.'
    assert _parse_json_lenient(raw) == {"confidence": "low"}


def test_trailing_stray_brace():
    assert _parse_json_lenient('{"a": 1}}') == {"a": 1}


def test_outer_object_wins_over_its_own_nested_value():
    """Earliest balanced span, not largest — otherwise a nested value with
    more keys than its parent would be returned as the whole payload."""
    raw = '{"signal_quality": {"a": 1, "b": 2, "c": 3, "d": 4}}'
    assert _parse_json_lenient(raw) == {"signal_quality": {"a": 1, "b": 2, "c": 3, "d": 4}}


def test_braces_inside_strings_do_not_break_depth_counting():
    raw = 'noise {"bias_notes": "users wrote {this} and {that}", "confidence": "high"}'
    assert _parse_json_lenient(raw) == {
        "bias_notes": "users wrote {this} and {that}",
        "confidence": "high",
    }


def test_escaped_quote_inside_a_string():
    raw = r'{"consensus_pick": "the \"best\" option"}'
    assert _parse_json_lenient(raw) == {"consensus_pick": 'the "best" option'}


def test_empty_object_is_not_accepted_as_a_result():
    """An empty dict before the real payload must not win — it parses, but it
    is not an answer."""
    assert _parse_json_lenient('{} {"confidence": "low"}') == {"confidence": "low"}


def test_unparseable_output_raises_rather_than_returning_blank():
    with pytest.raises(ValueError, match="did not return valid JSON"):
        _parse_json_lenient("I'm sorry, I can't help with that.")


def test_truncated_object_raises():
    """A response cut off by max_tokens has no closing brace; that is a failed
    run, and silently returning partial data would be worse than failing."""
    with pytest.raises(ValueError):
        _parse_json_lenient('{"subreddits": ["snowboarding", "Shredd')
