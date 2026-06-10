"""Unit tests for cross-day session date attribution by message timestamp (Issue #15)"""

from __future__ import annotations

from datetime import datetime, timezone

from pathlib import Path

import pytest

from cc_stats.analyzer import (
    SessionStats,
    TokenUsage,
    analyze_session,
    merge_stats,
)
from cc_stats.parser import Message, Session, ToolCall


def _make_session(messages: list[Message]) -> Session:
    return Session(
        session_id="cross-midnight-session",
        project_path="/tmp/test-project",
        file_path=Path("/tmp/test.jsonl"),
        messages=messages,
    )


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> str:
    """Generate a UTC ISO-format timestamp"""
    return f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00+00:00"


def _assistant_msg_with_usage(
    ts: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
    model: str = "claude-sonnet-4-20250514",
) -> Message:
    """Create an assistant message with token usage"""
    return Message(
        role="assistant",
        timestamp=ts,
        content="response",
        model=model,
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    )


class TestTokenByDate:
    """Tests for token_by_date attribution by message timestamp"""

    def test_single_day_session(self):
        """Single-day session: all tokens attributed to the same date"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 10), content="hello"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 10, 1), input_tokens=200, output_tokens=100),
            Message(role="user", timestamp=_ts(2026, 3, 15, 11), content="continue"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 11, 1), input_tokens=300, output_tokens=150),
        ])
        stats = analyze_session(session)

        # token_usage total is unchanged (backward-compatible)
        assert stats.token_usage.input_tokens == 500
        assert stats.token_usage.output_tokens == 250

        # token_by_date has only one date
        assert len(stats.token_by_date) == 1
        # Note: key is the local date, which depends on timezone. UTC 10:00 is 18:00 same day in UTC+8
        # So we only verify the total
        total_by_date = sum(tu.input_tokens for tu in stats.token_by_date.values())
        assert total_by_date == 500

    def test_cross_midnight_session(self):
        """Cross-midnight session: tokens distributed across two different dates

        Uses UTC times with a gap large enough (> 24h) to ensure that,
        regardless of local timezone, the two assistant messages fall on
        different local calendar days.
        """
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 3), content="start"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 4), input_tokens=200, output_tokens=100),
            Message(role="user", timestamp=_ts(2026, 3, 16, 14), content="next day"),
            _assistant_msg_with_usage(_ts(2026, 3, 16, 15), input_tokens=300, output_tokens=150),
        ])
        stats = analyze_session(session)

        # token_usage total stays unchanged (backward-compatible)
        assert stats.token_usage.input_tokens == 500
        assert stats.token_usage.output_tokens == 250

        # token_by_date should have two dates
        assert len(stats.token_by_date) == 2

        # The sum of tokens across both dates equals token_usage
        total_input = sum(tu.input_tokens for tu in stats.token_by_date.values())
        total_output = sum(tu.output_tokens for tu in stats.token_by_date.values())
        assert total_input == 500
        assert total_output == 250

    def test_cross_midnight_correct_date_assignment(self):
        """Verify tokens are attributed to the correct local dates

        Uses UTC times to construct a cross-day scenario and verifies that
        token_by_date keys contain the correct two dates.
        """
        # Use a gap large enough to guarantee a cross-day boundary in any timezone
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 2), content="early morning"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 3), input_tokens=100, output_tokens=50),
            Message(role="user", timestamp=_ts(2026, 3, 16, 14), content="afternoon next day"),
            _assistant_msg_with_usage(_ts(2026, 3, 16, 15), input_tokens=200, output_tokens=80),
        ])
        stats = analyze_session(session)

        # Regardless of local timezone, the two assistant messages should fall on different local dates
        assert len(stats.token_by_date) == 2

        # Get tokens for each date
        dates = sorted(stats.token_by_date.keys())
        day1_tokens = stats.token_by_date[dates[0]]
        day2_tokens = stats.token_by_date[dates[1]]

        assert day1_tokens.input_tokens == 100
        assert day1_tokens.output_tokens == 50
        assert day2_tokens.input_tokens == 200
        assert day2_tokens.output_tokens == 80

    def test_three_day_session(self):
        """Session spanning three days"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 14, 3), content="day 1"),
            _assistant_msg_with_usage(_ts(2026, 3, 14, 4), input_tokens=100, output_tokens=50),
            Message(role="user", timestamp=_ts(2026, 3, 15, 14), content="day 2"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 15), input_tokens=200, output_tokens=100),
            Message(role="user", timestamp=_ts(2026, 3, 16, 14), content="day 3"),
            _assistant_msg_with_usage(_ts(2026, 3, 16, 15), input_tokens=300, output_tokens=150),
        ])
        stats = analyze_session(session)

        assert len(stats.token_by_date) == 3
        total_input = sum(tu.input_tokens for tu in stats.token_by_date.values())
        assert total_input == 600
        assert stats.token_usage.input_tokens == 600  # backward-compatible

    def test_no_usage_messages(self):
        """Messages without usage do not affect token_by_date"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 10), content="hello"),
            Message(role="assistant", timestamp=_ts(2026, 3, 15, 10, 1), content="hi"),
        ])
        stats = analyze_session(session)
        assert stats.token_by_date == {}
        assert stats.token_usage.total == 0

    def test_cache_tokens_in_token_by_date(self):
        """Cache tokens are also correctly attributed by date"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 3), content="q1"),
            Message(
                role="assistant",
                timestamp=_ts(2026, 3, 15, 4),
                content="a1",
                model="claude-sonnet-4-20250514",
                usage={
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 200,
                },
            ),
        ])
        stats = analyze_session(session)
        assert len(stats.token_by_date) == 1
        date_key = list(stats.token_by_date.keys())[0]
        tu = stats.token_by_date[date_key]
        assert tu.cache_read_input_tokens == 500
        assert tu.cache_creation_input_tokens == 200


class TestTokenByDateMerge:
    """Tests for merge_stats merging of token_by_date"""

    def test_merge_token_by_date(self):
        """Merge token_by_date from two sessions"""
        s1 = SessionStats(session_id="s1", project_path="/tmp")
        s1.token_by_date["2026-03-15"] = TokenUsage(
            input_tokens=100, output_tokens=50,
        )

        s2 = SessionStats(session_id="s2", project_path="/tmp")
        s2.token_by_date["2026-03-15"] = TokenUsage(
            input_tokens=200, output_tokens=80,
        )
        s2.token_by_date["2026-03-16"] = TokenUsage(
            input_tokens=300, output_tokens=120,
        )

        merged = merge_stats([s1, s2])

        assert "2026-03-15" in merged.token_by_date
        assert "2026-03-16" in merged.token_by_date
        assert merged.token_by_date["2026-03-15"].input_tokens == 300
        assert merged.token_by_date["2026-03-15"].output_tokens == 130
        assert merged.token_by_date["2026-03-16"].input_tokens == 300
        assert merged.token_by_date["2026-03-16"].output_tokens == 120

    def test_merge_empty_token_by_date(self):
        """Empty token_by_date during merge does not affect result"""
        s1 = SessionStats(session_id="s1", project_path="/tmp")
        s2 = SessionStats(session_id="s2", project_path="/tmp")
        s2.token_by_date["2026-03-15"] = TokenUsage(input_tokens=100)

        merged = merge_stats([s1, s2])
        assert merged.token_by_date["2026-03-15"].input_tokens == 100


class TestBackwardCompatibility:
    """Ensure token_usage backward compatibility"""

    def test_token_usage_unchanged(self):
        """token_usage is unaffected by token_by_date and retains the full total"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 23), content="start"),
            _assistant_msg_with_usage(_ts(2026, 3, 15, 23, 30), input_tokens=100, output_tokens=50),
            Message(role="user", timestamp=_ts(2026, 3, 16, 1), content="continue"),
            _assistant_msg_with_usage(_ts(2026, 3, 16, 1, 30), input_tokens=200, output_tokens=80),
        ])
        stats = analyze_session(session)

        # token_usage is the full sum, consistent with previous behavior
        assert stats.token_usage.input_tokens == 300
        assert stats.token_usage.output_tokens == 130
        assert stats.token_usage.total == 430

        # The sum of token_by_date across all dates also equals the total
        by_date_total = sum(tu.total for tu in stats.token_by_date.values())
        assert by_date_total == 430

    def test_token_by_model_unchanged(self):
        """token_by_model is unaffected"""
        session = _make_session([
            Message(role="user", timestamp=_ts(2026, 3, 15, 10), content="q"),
            _assistant_msg_with_usage(
                _ts(2026, 3, 15, 10, 1),
                input_tokens=100, output_tokens=50,
                model="claude-sonnet-4-20250514",
            ),
        ])
        stats = analyze_session(session)
        assert "claude-sonnet-4-20250514" in stats.token_by_model
        assert stats.token_by_model["claude-sonnet-4-20250514"].input_tokens == 100
