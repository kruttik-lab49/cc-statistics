"""Unit tests for coding rhythm analysis and work mode classification"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_stats.analyzer import (
    SessionStats,
    TokenUsage,
    _time_period,
    analyze_session,
    classify_work_mode,
    merge_stats,
)
from cc_stats.formatter import format_coding_rhythm
from cc_stats.parser import Message, Session, ToolCall


# ── _time_period ──────────────────────────────────────────────


class TestTimePeriod:
    def test_morning(self):
        assert _time_period(6) == "morning"
        assert _time_period(11) == "morning"

    def test_afternoon(self):
        assert _time_period(12) == "afternoon"
        assert _time_period(17) == "afternoon"

    def test_evening(self):
        assert _time_period(18) == "evening"
        assert _time_period(23) == "evening"

    def test_night(self):
        assert _time_period(0) == "night"
        assert _time_period(5) == "night"

    def test_boundaries(self):
        assert _time_period(5) == "night"
        assert _time_period(6) == "morning"
        assert _time_period(11) == "morning"
        assert _time_period(12) == "afternoon"
        assert _time_period(17) == "afternoon"
        assert _time_period(18) == "evening"
        assert _time_period(23) == "evening"


# ── classify_work_mode ────────────────────────────────────────


class TestClassifyWorkMode:
    def test_exploration_low_code(self):
        # code_per_msg = 4/1 = 4 < 5 → Exploration
        assert classify_work_mode(1, 2, 2) == "Exploration"

    def test_exploration_zero_code(self):
        assert classify_work_mode(10, 0, 0) == "Exploration"

    def test_execution_high_code(self):
        # code_per_msg = 510/10 = 51 > 50 → Execution
        assert classify_work_mode(10, 300, 210) == "Execution"

    def test_building_balanced(self):
        # code_per_msg = 200/10 = 20, 5 ≤ 20 ≤ 50 → Building
        assert classify_work_mode(10, 120, 80) == "Building"

    def test_boundary_exactly_5(self):
        # code_per_msg = 5/1 = 5 → Building (not < 5)
        assert classify_work_mode(1, 3, 2) == "Building"

    def test_boundary_exactly_50(self):
        # code_per_msg = 50/1 = 50 → Building (not > 50)
        assert classify_work_mode(1, 30, 20) == "Building"

    def test_boundary_51(self):
        # code_per_msg = 51/1 = 51 > 50 → Execution
        assert classify_work_mode(1, 31, 20) == "Execution"

    def test_zero_messages(self):
        # user_message_count=0 → max(0,1)=1 → code_per_msg = 100 → Execution
        assert classify_work_mode(0, 60, 40) == "Execution"

    def test_zero_everything(self):
        # code_per_msg = 0/1 = 0 < 5 → Exploration
        assert classify_work_mode(0, 0, 0) == "Exploration"


# ── analyze_session coding_rhythm ─────────────────────────────


def _local_tz() -> timezone:
    """Get the local timezone offset"""
    now = datetime.now()
    utc_now = datetime.now(timezone.utc)
    offset = now.replace(tzinfo=None) - utc_now.replace(tzinfo=None)
    # Round to the nearest minute
    total_secs = int(offset.total_seconds() / 60) * 60
    return timezone(timedelta(seconds=total_secs))


def _make_session(
    start_hour: int = 10,
    user_msgs: int = 5,
    write_lines: int = 0,
) -> Session:
    """Create a simple test Session; start_hour is in local time"""
    local_tz = _local_tz()
    base_ts = datetime(2026, 4, 12, start_hour, 0, 0, tzinfo=local_tz)
    messages: list[Message] = []

    for i in range(user_msgs):
        t = base_ts + timedelta(minutes=i * 2)
        messages.append(Message(
            role="user",
            timestamp=t.isoformat(),
            content=f"instruction {i}",
            session_id="test-session",
        ))
        # assistant response with token usage
        t_resp = t + timedelta(seconds=30)
        tool_calls = []
        if write_lines > 0 and i == 0:
            tool_calls.append(ToolCall(
                name="Write",
                input={
                    "file_path": "test.py",
                    "content": "\n".join(["line"] * write_lines),
                },
                timestamp=t_resp.isoformat(),
            ))
        messages.append(Message(
            role="assistant",
            timestamp=t_resp.isoformat(),
            content="response",
            model="claude-sonnet-4-6",
            usage={
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 0,
            },
            tool_calls=tool_calls,
            session_id="test-session",
        ))

    return Session(
        session_id="test-session",
        project_path="/tmp/test",
        file_path=Path("/tmp/test.jsonl"),
        messages=messages,
    )


class TestAnalyzeSessionCodingRhythm:
    def test_morning_session(self):
        session = _make_session(start_hour=8)
        stats = analyze_session(session)
        assert "morning" in stats.coding_rhythm
        data = stats.coding_rhythm["morning"]
        assert data["session_count"] == 1
        assert data["token_count"] > 0
        assert data["active_minutes"] >= 0

    def test_afternoon_session(self):
        session = _make_session(start_hour=14)
        stats = analyze_session(session)
        assert "afternoon" in stats.coding_rhythm

    def test_evening_session(self):
        session = _make_session(start_hour=20)
        stats = analyze_session(session)
        assert "evening" in stats.coding_rhythm

    def test_night_session(self):
        session = _make_session(start_hour=2)
        stats = analyze_session(session)
        assert "night" in stats.coding_rhythm

    def test_single_period_only(self):
        session = _make_session(start_hour=10)
        stats = analyze_session(session)
        assert len(stats.coding_rhythm) == 1


class TestAnalyzeSessionWorkMode:
    def test_exploration_mode(self):
        # Many user messages, no code changes
        session = _make_session(user_msgs=10, write_lines=0)
        stats = analyze_session(session)
        assert "Exploration" in stats.work_mode_distribution
        assert stats.work_mode_distribution["Exploration"] == 1

    def test_execution_mode(self):
        # Few user messages, many code lines
        session = _make_session(user_msgs=1, write_lines=100)
        stats = analyze_session(session)
        assert "Execution" in stats.work_mode_distribution
        assert stats.work_mode_distribution["Execution"] == 1

    def test_building_mode(self):
        # Balanced: 5 msgs, ~30 lines → code_per_msg = 30/5 = 6
        session = _make_session(user_msgs=5, write_lines=30)
        stats = analyze_session(session)
        assert "Building" in stats.work_mode_distribution
        assert stats.work_mode_distribution["Building"] == 1


# ── merge_stats ───────────────────────────────────────────────


class TestMergeStatsCodingRhythm:
    def _make_stats(
        self,
        period: str,
        tokens: int = 1000,
        minutes: float = 10.0,
        mode: str = "Building",
    ) -> SessionStats:
        return SessionStats(
            session_id="s1",
            project_path="/tmp",
            coding_rhythm={
                period: {
                    "session_count": 1,
                    "token_count": tokens,
                    "active_minutes": minutes,
                }
            },
            work_mode_distribution={mode: 1},
        )

    def test_merge_same_period(self):
        s1 = self._make_stats("morning", tokens=1000, minutes=10.0)
        s2 = self._make_stats("morning", tokens=2000, minutes=20.0)
        merged = merge_stats([s1, s2])

        assert "morning" in merged.coding_rhythm
        data = merged.coding_rhythm["morning"]
        assert data["session_count"] == 2
        assert data["token_count"] == 3000
        assert data["active_minutes"] == 30.0

    def test_merge_different_periods(self):
        s1 = self._make_stats("morning", tokens=1000)
        s2 = self._make_stats("evening", tokens=2000)
        merged = merge_stats([s1, s2])

        assert "morning" in merged.coding_rhythm
        assert "evening" in merged.coding_rhythm
        assert merged.coding_rhythm["morning"]["session_count"] == 1
        assert merged.coding_rhythm["evening"]["session_count"] == 1

    def test_merge_work_modes(self):
        s1 = self._make_stats("morning", mode="Exploration")
        s2 = self._make_stats("morning", mode="Building")
        s3 = self._make_stats("morning", mode="Exploration")
        merged = merge_stats([s1, s2, s3])

        assert merged.work_mode_distribution["Exploration"] == 2
        assert merged.work_mode_distribution["Building"] == 1

    def test_merge_empty(self):
        merged = merge_stats([])
        assert merged.coding_rhythm == {}
        assert merged.work_mode_distribution == {}


# ── format_coding_rhythm ──────────────────────────────────────


class TestFormatCodingRhythm:
    def test_empty_returns_empty(self):
        stats = SessionStats(session_id="t", project_path="/tmp")
        assert format_coding_rhythm(stats) == ""

    def test_has_rhythm_content(self):
        stats = SessionStats(
            session_id="t",
            project_path="/tmp",
            coding_rhythm={
                "morning": {
                    "session_count": 3,
                    "token_count": 50000,
                    "active_minutes": 45.0,
                },
                "evening": {
                    "session_count": 1,
                    "token_count": 10000,
                    "active_minutes": 15.0,
                },
            },
        )
        output = format_coding_rhythm(stats)
        assert "Morning" in output
        assert "Evening" in output
        assert "★" in output  # peak indicator

    def test_has_work_mode_content(self):
        stats = SessionStats(
            session_id="t",
            project_path="/tmp",
            work_mode_distribution={
                "Exploration": 5,
                "Building": 3,
                "Execution": 2,
            },
        )
        output = format_coding_rhythm(stats)
        assert "Exploration" in output
        assert "Building" in output
        assert "Execution" in output
        assert "50%" in output  # 5/10 = 50%

    def test_peak_period_highlighted(self):
        stats = SessionStats(
            session_id="t",
            project_path="/tmp",
            coding_rhythm={
                "morning": {
                    "session_count": 1,
                    "token_count": 100,
                    "active_minutes": 5.0,
                },
                "afternoon": {
                    "session_count": 5,
                    "token_count": 50000,
                    "active_minutes": 120.0,
                },
            },
        )
        output = format_coding_rhythm(stats)
        # The ★ should be on the afternoon line (highest tokens)
        assert "★" in output

    def test_all_periods_shown(self):
        """All time periods should be shown even when they have no data (greyed out)"""
        stats = SessionStats(
            session_id="t",
            project_path="/tmp",
            coding_rhythm={
                "morning": {
                    "session_count": 1,
                    "token_count": 1000,
                    "active_minutes": 10.0,
                },
            },
        )
        output = format_coding_rhythm(stats)
        assert "Morning" in output
        assert "Afternoon" in output
        assert "Evening" in output
        assert "Night" in output
