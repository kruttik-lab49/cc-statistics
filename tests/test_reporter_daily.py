"""Tests for reporter.py daily statistics cross-day session token/cost fix (Issue #15)"""

from __future__ import annotations

import pytest

from cc_stats.analyzer import SessionStats, TokenUsage
from cc_stats.reporter import _daily_token_and_cost, _estimate_cost


def _make_stats(
    session_id: str,
    token_by_date: dict[str, TokenUsage],
    token_by_model: dict[str, TokenUsage] | None = None,
    total_usage: TokenUsage | None = None,
) -> SessionStats:
    """Create a SessionStats with token_by_date populated"""
    s = SessionStats(session_id=session_id, project_path="/tmp/test")
    s.token_by_date = token_by_date
    if token_by_model is not None:
        s.token_by_model = token_by_model
    # Compute total token_usage
    if total_usage:
        s.token_usage = total_usage
    else:
        for tu in token_by_date.values():
            s.token_usage.input_tokens += tu.input_tokens
            s.token_usage.output_tokens += tu.output_tokens
            s.token_usage.cache_read_input_tokens += tu.cache_read_input_tokens
            s.token_usage.cache_creation_input_tokens += tu.cache_creation_input_tokens
    return s


class TestDailyTokenAndCost:
    """Tests for the _daily_token_and_cost function"""

    def test_single_day_session(self):
        """Single-day session: all tokens attributed to that day"""
        s = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=1000, output_tokens=500),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=1000, output_tokens=500
                ),
            },
        )
        usage, cost = _daily_token_and_cost([s], "2026-03-15")
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.total == 1500
        # Cost should equal the entire session's cost
        assert cost == pytest.approx(_estimate_cost(s))

    def test_cross_day_session_day1(self):
        """Cross-day session: take only the first day's tokens (not the full total)"""
        s = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=400, output_tokens=200),
                "2026-03-16": TokenUsage(input_tokens=600, output_tokens=300),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=1000, output_tokens=500
                ),
            },
        )
        usage, cost = _daily_token_and_cost([s], "2026-03-15")
        # Take only 3/15 tokens
        assert usage.input_tokens == 400
        assert usage.output_tokens == 200
        assert usage.total == 600
        # Cost should be 600/1500 = 40% of the session's total cost
        total_cost = _estimate_cost(s)
        assert cost == pytest.approx(total_cost * 600 / 1500)

    def test_cross_day_session_day2(self):
        """Cross-day session: take the second day's tokens"""
        s = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=400, output_tokens=200),
                "2026-03-16": TokenUsage(input_tokens=600, output_tokens=300),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=1000, output_tokens=500
                ),
            },
        )
        usage, cost = _daily_token_and_cost([s], "2026-03-16")
        assert usage.input_tokens == 600
        assert usage.output_tokens == 300
        assert usage.total == 900
        total_cost = _estimate_cost(s)
        assert cost == pytest.approx(total_cost * 900 / 1500)

    def test_cross_day_costs_sum_to_total(self):
        """Sum of both days' costs for a cross-day session equals the session total cost"""
        s = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=400, output_tokens=200),
                "2026-03-16": TokenUsage(input_tokens=600, output_tokens=300),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=1000, output_tokens=500
                ),
            },
        )
        _, cost_day1 = _daily_token_and_cost([s], "2026-03-15")
        _, cost_day2 = _daily_token_and_cost([s], "2026-03-16")
        total_cost = _estimate_cost(s)
        assert cost_day1 + cost_day2 == pytest.approx(total_cost)

    def test_multiple_sessions_same_day(self):
        """Tokens from multiple sessions on the same day are accumulated"""
        s1 = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=100, output_tokens=50),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=100, output_tokens=50
                ),
            },
        )
        s2 = _make_stats(
            "s2",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=200, output_tokens=80),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=200, output_tokens=80
                ),
            },
        )
        usage, cost = _daily_token_and_cost([s1, s2], "2026-03-15")
        assert usage.input_tokens == 300
        assert usage.output_tokens == 130
        assert cost == pytest.approx(_estimate_cost(s1) + _estimate_cost(s2))

    def test_mixed_single_and_cross_day(self):
        """Mixed: one single-day session + one cross-day session"""
        s_single = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=100, output_tokens=50),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=100, output_tokens=50
                ),
            },
        )
        s_cross = _make_stats(
            "s2",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=400, output_tokens=200),
                "2026-03-16": TokenUsage(input_tokens=600, output_tokens=300),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=1000, output_tokens=500
                ),
            },
        )
        # 3/15: s_single full amount + s_cross's 3/15 portion
        usage, cost = _daily_token_and_cost([s_single, s_cross], "2026-03-15")
        assert usage.input_tokens == 500  # 100 + 400
        assert usage.output_tokens == 250  # 50 + 200

        cross_total_cost = _estimate_cost(s_cross)
        expected_cost = _estimate_cost(s_single) + cross_total_cost * 600 / 1500
        assert cost == pytest.approx(expected_cost)

    def test_session_without_token_by_date_for_day(self):
        """Session's token_by_date does not contain the requested date"""
        s = _make_stats(
            "s1",
            token_by_date={
                "2026-03-15": TokenUsage(input_tokens=100, output_tokens=50),
            },
            token_by_model={
                "claude-sonnet-4-20250514": TokenUsage(
                    input_tokens=100, output_tokens=50
                ),
            },
        )
        usage, cost = _daily_token_and_cost([s], "2026-03-20")
        assert usage.total == 0
        assert cost == 0.0

    def test_empty_stats_list(self):
        """Empty stats list"""
        usage, cost = _daily_token_and_cost([], "2026-03-15")
        assert usage.total == 0
        assert cost == 0.0

    def test_zero_total_tokens_no_division_error(self):
        """No division-by-zero exception when session total tokens is 0"""
        s = SessionStats(session_id="s1", project_path="/tmp")
        s.token_by_date["2026-03-15"] = TokenUsage()  # all zeros
        usage, cost = _daily_token_and_cost([s], "2026-03-15")
        assert usage.total == 0
        assert cost == 0.0
