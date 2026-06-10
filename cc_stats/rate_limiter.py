"""Usage Quota Predictor — sliding-window usage quota analysis"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .analyzer import SessionStats

# Default limit: Claude Pro Sonnet series 5-minute sliding window
DEFAULT_WINDOW_LIMIT = 40_000  # output tokens / 5 min
DEFAULT_WINDOW_MINUTES = 5


@dataclass
class RateLimitStatus:
    """Usage quota status"""
    status: str          # "safe" | "warning" | "critical" | "idle"
    window_limit: int    # 5-minute window limit (default 40000)
    window_used: int     # output tokens used within the 5-minute window
    pct: float           # window_used / window_limit (0.0 ~ 1.0+)
    rate_per_min: float  # tokens/min (within the recent window)
    minutes_until_limit: float | None  # None = limit will not be reached


def analyze_rate_limit(
    stats: SessionStats,
    window_limit: int = DEFAULT_WINDOW_LIMIT,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> RateLimitStatus:
    """Compute usage quota forecast based on token_by_minute data

    Args:
        stats: Session stats result (requires token_by_minute data)
        window_limit: Maximum output tokens allowed in the sliding window
        window_minutes: Sliding window size (minutes)
    """
    if not stats.token_by_minute:
        return RateLimitStatus(
            status="idle",
            window_limit=window_limit,
            window_used=0,
            pct=0.0,
            rate_per_min=0.0,
            minutes_until_limit=None,
        )

    # Find the most recent time point in the data as the window end
    sorted_keys = sorted(stats.token_by_minute.keys())
    latest_key = sorted_keys[-1]

    try:
        latest_dt = datetime.strptime(latest_key, "%Y-%m-%d %H:%M")
    except ValueError:
        return RateLimitStatus(
            status="idle",
            window_limit=window_limit,
            window_used=0,
            pct=0.0,
            rate_per_min=0.0,
            minutes_until_limit=None,
        )

    # Window start (exclusive): latest - window_minutes
    window_start_dt = latest_dt - timedelta(minutes=window_minutes)
    window_start_key = window_start_dt.strftime("%Y-%m-%d %H:%M")

    # Accumulate output tokens within the window
    window_used = 0
    active_minutes = 0
    for key in sorted_keys:
        if key > window_start_key:
            window_used += stats.token_by_minute[key].output_tokens
            active_minutes += 1

    if active_minutes == 0:
        return RateLimitStatus(
            status="idle",
            window_limit=window_limit,
            window_used=0,
            pct=0.0,
            rate_per_min=0.0,
            minutes_until_limit=None,
        )

    pct = window_used / window_limit if window_limit > 0 else 0.0
    rate_per_min = window_used / window_minutes

    # Predict remaining time
    remaining = window_limit - window_used
    if rate_per_min > 0 and remaining > 0:
        minutes_until_limit = remaining / rate_per_min
    elif remaining <= 0:
        minutes_until_limit = 0.0
    else:
        minutes_until_limit = None

    # Status classification
    if pct >= 0.85:
        status = "critical"
    elif pct >= 0.60:
        status = "warning"
    else:
        status = "safe"

    return RateLimitStatus(
        status=status,
        window_limit=window_limit,
        window_used=window_used,
        pct=pct,
        rate_per_min=rate_per_min,
        minutes_until_limit=minutes_until_limit,
    )
