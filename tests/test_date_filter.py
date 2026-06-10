"""Unit tests for date filter precision (Issue #20 Bug 2)

Verifies:
1. _parse_time_arg's as_end_of_day parameter correctly fills in 23:59:59
2. _trim_stats_by_date_range correctly trims token_by_date and recomputes token_usage
3. End-to-end: --since/--until only counts tokens for dates within the range
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cc_stats.analyzer import SessionStats, TokenUsage
from cc_stats.cli import _parse_time_arg, _trim_stats_by_date_range


# ── _parse_time_arg tests ────────────────────────────────────


class TestParseTimeArg:
    def test_date_only_default_midnight(self):
        """Date-only format defaults to 00:00:00"""
        dt = _parse_time_arg("2026-04-03")
        local = dt.astimezone()
        assert local.hour == 0
        assert local.minute == 0
        assert local.second == 0

    def test_date_only_as_end_of_day(self):
        """Date-only format + as_end_of_day=True fills in 23:59:59"""
        dt = _parse_time_arg("2026-04-03", as_end_of_day=True)
        local = dt.astimezone()
        assert local.hour == 23
        assert local.minute == 59
        assert local.second == 59

    def test_datetime_format_ignores_end_of_day(self):
        """Datetime format is unaffected by as_end_of_day"""
        dt = _parse_time_arg("2026-04-03T10:30", as_end_of_day=True)
        local = dt.astimezone()
        assert local.hour == 10
        assert local.minute == 30

    def test_relative_time_ignores_end_of_day(self):
        """Relative time is unaffected by as_end_of_day"""
        dt1 = _parse_time_arg("1d")
        dt2 = _parse_time_arg("1d", as_end_of_day=True)
        # The two should be very close (within a few milliseconds)
        assert abs((dt1 - dt2).total_seconds()) < 1

    def test_invalid_format_raises(self):
        """Invalid format should raise an exception"""
        with pytest.raises(Exception):
            _parse_time_arg("not-a-date")


# ── _trim_stats_by_date_range tests ─────────────────────────


def _make_stats_with_dates(
    date_tokens: dict[str, tuple[int, int, int, int]],
) -> SessionStats:
    """Create a SessionStats with token_by_date populated

    date_tokens: {"YYYY-MM-DD": (input, output, cache_read, cache_create)}
    """
    stats = SessionStats(session_id="test", project_path="/tmp")
    total = TokenUsage()
    for date_key, (inp, out, cr, cc) in date_tokens.items():
        tu = TokenUsage(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cr,
            cache_creation_input_tokens=cc,
        )
        stats.token_by_date[date_key] = tu
        total.input_tokens += inp
        total.output_tokens += out
        total.cache_read_input_tokens += cr
        total.cache_creation_input_tokens += cc
    stats.token_usage = total
    return stats


class TestTrimStatsByDateRange:
    def test_trim_both_sides(self):
        """Specifying both since and until retains only dates within the range"""
        stats = _make_stats_with_dates({
            "2026-03-31": (100, 50, 0, 0),
            "2026-04-01": (200, 100, 0, 0),
            "2026-04-02": (300, 150, 0, 0),
            "2026-04-03": (400, 200, 0, 0),
            "2026-04-04": (500, 250, 0, 0),
        })
        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-03")

        assert set(stats.token_by_date.keys()) == {"2026-04-02", "2026-04-03"}
        assert stats.token_usage.input_tokens == 700  # 300 + 400
        assert stats.token_usage.output_tokens == 350  # 150 + 200
        assert stats.token_usage.total == 1050

    def test_trim_since_only(self):
        """Specifying only since excludes dates earlier than since"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 0, 0),
            "2026-04-02": (200, 100, 0, 0),
            "2026-04-03": (300, 150, 0, 0),
        })
        _trim_stats_by_date_range(stats, "2026-04-02", None)

        assert set(stats.token_by_date.keys()) == {"2026-04-02", "2026-04-03"}
        assert stats.token_usage.input_tokens == 500

    def test_trim_until_only(self):
        """Specifying only until excludes dates later than until"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 0, 0),
            "2026-04-02": (200, 100, 0, 0),
            "2026-04-03": (300, 150, 0, 0),
        })
        _trim_stats_by_date_range(stats, None, "2026-04-02")

        assert set(stats.token_by_date.keys()) == {"2026-04-01", "2026-04-02"}
        assert stats.token_usage.input_tokens == 300

    def test_no_range_no_change(self):
        """No trimming when no range is specified"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 0, 0),
            "2026-04-02": (200, 100, 0, 0),
        })
        original_total = stats.token_usage.input_tokens
        _trim_stats_by_date_range(stats, None, None)

        assert len(stats.token_by_date) == 2
        assert stats.token_usage.input_tokens == original_total

    def test_empty_token_by_date(self):
        """No error when token_by_date is empty"""
        stats = SessionStats(session_id="test", project_path="/tmp")
        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-03")
        assert stats.token_by_date == {}

    def test_all_dates_outside_range(self):
        """All dates outside range: tokens zeroed out"""
        stats = _make_stats_with_dates({
            "2026-03-30": (100, 50, 0, 0),
            "2026-03-31": (200, 100, 0, 0),
        })
        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-03")

        assert stats.token_by_date == {}
        assert stats.token_usage.total == 0

    def test_cache_tokens_preserved(self):
        """Cache tokens are also correctly recomputed after trimming"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 1000, 200),
            "2026-04-02": (200, 100, 2000, 400),
            "2026-04-03": (300, 150, 3000, 600),
        })
        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-02")

        assert stats.token_usage.input_tokens == 200
        assert stats.token_usage.output_tokens == 100
        assert stats.token_usage.cache_read_input_tokens == 2000
        assert stats.token_usage.cache_creation_input_tokens == 400

    def test_token_by_model_trimmed_with_date_range(self):
        """Per-model breakdown is recomputed when dates are trimmed, avoiding cost/total token mismatch"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 0, 0),
            "2026-04-02": (200, 100, 20, 0),
            "2026-04-03": (300, 150, 30, 0),
        })
        stats.token_by_model_by_date = {
            "2026-04-01": {
                "gpt-5.5": TokenUsage(input_tokens=100, output_tokens=50),
            },
            "2026-04-02": {
                "gpt-5.5": TokenUsage(
                    input_tokens=120,
                    output_tokens=60,
                    cache_read_input_tokens=20,
                ),
                "claude-sonnet-4.5": TokenUsage(input_tokens=80, output_tokens=40),
            },
            "2026-04-03": {
                "claude-sonnet-4.5": TokenUsage(
                    input_tokens=300,
                    output_tokens=150,
                    cache_read_input_tokens=30,
                ),
            },
        }
        stats.token_by_model = {
            "gpt-5.5": TokenUsage(input_tokens=220, output_tokens=110, cache_read_input_tokens=20),
            "claude-sonnet-4.5": TokenUsage(input_tokens=380, output_tokens=190, cache_read_input_tokens=30),
        }

        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-02")

        assert set(stats.token_by_model) == {"gpt-5.5", "claude-sonnet-4.5"}
        assert stats.token_by_model["gpt-5.5"].total == 200
        assert stats.token_by_model["claude-sonnet-4.5"].total == 120
        assert sum(tu.total for tu in stats.token_by_model.values()) == stats.token_usage.total

    def test_coding_rhythm_token_count_trimmed_with_date_range(self):
        """Coding rhythm token_count is also synced to the trimmed total when dates are trimmed"""
        stats = _make_stats_with_dates({
            "2026-04-01": (100, 50, 0, 0),
            "2026-04-02": (200, 100, 0, 0),
        })
        stats.coding_rhythm = {
            "morning": {
                "session_count": 1,
                "token_count": stats.token_usage.total,
                "active_minutes": 5.0,
            }
        }

        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-02")

        assert stats.token_usage.total == 300
        assert stats.coding_rhythm["morning"]["token_count"] == 300


# ── End-to-end scenario tests ────────────────────────────────


class TestDateFilterEndToEnd:
    """Simulates Issue #20 scenario: cross-day sessions after date filtering should only count tokens within the range"""

    def test_cross_day_session_trimmed(self):
        """A session from 03-31 to 04-04, filtered to 04-02~04-03, counts only those two days"""
        stats = _make_stats_with_dates({
            "2026-03-31": (1000, 500, 0, 0),
            "2026-04-01": (2000, 1000, 0, 0),
            "2026-04-02": (3000, 1500, 0, 0),
            "2026-04-03": (4000, 2000, 0, 0),
            "2026-04-04": (5000, 2500, 0, 0),
        })
        # Set original time range
        stats.start_time = datetime(2026, 3, 31, tzinfo=timezone.utc)
        stats.end_time = datetime(2026, 4, 4, tzinfo=timezone.utc)

        # Trim
        _trim_stats_by_date_range(stats, "2026-04-02", "2026-04-03")

        # Only 04-02 and 04-03 should remain
        assert stats.token_usage.input_tokens == 7000  # 3000 + 4000
        assert stats.token_usage.output_tokens == 3500  # 1500 + 2000
        assert stats.token_usage.total == 10500
