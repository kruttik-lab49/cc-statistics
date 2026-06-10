"""Generate weekly/monthly Markdown reports"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analyzer import SessionStats, TokenUsage, analyze_session, merge_stats
from .parser import (
    find_codex_sessions,
    find_gemini_sessions,
    find_sessions,
    parse_session_file,
)
from .pricing import (
    Pricing,
    estimate_cost_from_token_by_model,
    match_model_pricing,
)


def _match_pricing(model: str) -> Pricing:
    return match_model_pricing(model)


def _estimate_cost(stats: SessionStats) -> float:
    return estimate_cost_from_token_by_model(stats.token_by_model)


def _fmt_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        return "0s"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def _fmt_tokens(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(n)


def _fmt_cost(n: float) -> str:
    if n >= 100:
        return f"${n:.0f}"
    if n >= 1:
        return f"${n:.2f}"
    return f"${n:.3f}"


def _daily_token_and_cost(
    stats_list: list[SessionStats], day_key: str
) -> tuple[TokenUsage, float]:
    """Calculate token usage and cost for a given day (extracted from each session's token_by_date)

    For cross-day sessions, only the token portion for day_key is used;
    cost is estimated proportionally based on that day's share of the session's total tokens.
    """
    day_usage = TokenUsage()
    day_cost = 0.0
    for s in stats_list:
        usage = s.token_by_date.get(day_key)
        if not usage or usage.total == 0:
            continue
        day_usage.input_tokens += usage.input_tokens
        day_usage.output_tokens += usage.output_tokens
        day_usage.cache_read_input_tokens += usage.cache_read_input_tokens
        day_usage.cache_creation_input_tokens += usage.cache_creation_input_tokens
        # Allocate cost proportionally by that day's token share
        if s.token_usage.total > 0:
            fraction = usage.total / s.token_usage.total
            day_cost += _estimate_cost(s) * fraction
    return day_usage, day_cost


def generate_report(period: str = "week") -> str:
    """Generate weekly or monthly Markdown report

    Args:
        period: "week" or "month"
    """
    now = datetime.now(tz=timezone.utc)
    if period == "month":
        since = now - timedelta(days=30)
        title = "Monthly Report"
        title_en = "Monthly Report"
        days = 30
    else:
        since = now - timedelta(days=7)
        title = "Weekly Report"
        title_en = "Weekly Report"
        days = 7

    start_str = since.astimezone().strftime("%Y-%m-%d")
    end_str = now.astimezone().strftime("%Y-%m-%d")

    # Collect all sessions (Claude + Codex + Gemini)
    session_files: list[Path] = [
        f for f in find_sessions() if not f.name.startswith("agent-")
    ]
    session_files.extend(find_codex_sessions())
    session_files.extend(find_gemini_sessions())
    session_files.sort(key=lambda f: f.stat().st_mtime)

    all_stats: list[SessionStats] = []
    daily: dict[str, list[SessionStats]] = defaultdict(list)

    for f in session_files:
        try:
            session = parse_session_file(f)
            stats = analyze_session(session)
            if stats.end_time and stats.end_time < since:
                continue
            all_stats.append(stats)
            # Group by token_by_date: cross-day session data is assigned to each calendar day
            for day_key in stats.token_by_date:
                daily[day_key].append(stats)
            # Fall back to start_time grouping when no token data is available
            if not stats.token_by_date and stats.start_time:
                day_key = stats.start_time.astimezone().strftime("%Y-%m-%d")
                daily[day_key].append(stats)
        except Exception:
            continue

    if not all_stats:
        return f"# Claude Code {title} ({start_str} ~ {end_str})\n\n> No session data for this period.\n"

    merged = merge_stats(all_stats) if len(all_stats) > 1 else all_stats[0]
    cost = _estimate_cost(merged)

    # Group by project
    project_stats: dict[str, list[SessionStats]] = defaultdict(list)
    for s in all_stats:
        proj = s.project_path or "Unknown"
        proj_name = Path(proj).name if proj != "all" else proj
        project_stats[proj_name].append(s)

    # Daily statistics
    daily_lines = []
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_key = d.strftime("%Y-%m-%d")
        day_stats_list = daily.get(day_key, [])
        if day_stats_list:
            ds = merge_stats(day_stats_list) if len(day_stats_list) > 1 else day_stats_list[0]
            # Use token_by_date for that day's tokens to avoid double-counting cross-day sessions
            day_token_usage, day_cost = _daily_token_and_cost(day_stats_list, day_key)
            daily_lines.append(
                f"| {day_key} | {len(day_stats_list)} | {ds.user_message_count} | "
                f"{_fmt_duration(ds.active_duration)} | {_fmt_tokens(day_token_usage.total)} | {_fmt_cost(day_cost)} |"
            )

    # Top 5 tool calls
    sorted_tools = sorted(merged.tool_call_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # Language statistics
    sorted_langs = sorted(merged.lines_by_lang.items(), key=lambda x: x[1]["added"], reverse=True)[:5]

    # Generate Markdown
    lines = [
        f"# Claude Code {title}",
        f"",
        f"**{start_str} ~ {end_str}**",
        f"",
        f"## Overview",
        f"",
        f"| Metric | Value |",
        f"|------|------|",
        f"| Sessions | {len(all_stats)} |",
        f"| Instructions | {merged.user_message_count} |",
        f"| Tool Calls | {merged.tool_call_total} |",
        f"| Active Time | {_fmt_duration(merged.active_duration)} |",
        f"| AI Processing | {_fmt_duration(merged.ai_duration)} |",
        f"| User Active | {_fmt_duration(merged.user_duration)} |",
        f"| Token Usage | {_fmt_tokens(merged.token_usage.total)} |",
        f"| Est. Cost | {_fmt_cost(cost)} |",
        f"| Code Added | +{merged.total_added} |",
        f"| Code Removed | -{merged.total_removed} |",
    ]

    if merged.git_available:
        ai_info = ""
        if merged.git_ai_commit_count > 0:
            ai_pct = round(merged.git_ai_commit_count / max(merged.git_commit_count, 1) * 100)
            ai_info = f" ({merged.git_ai_commit_count} AI, {ai_pct}%)"
        lines.append(f"| Git Commits | {merged.git_commit_count}{ai_info} |")

    lines += [
        f"",
        f"## Daily Breakdown",
        f"",
        f"| Date | Sessions | Instructions | Active Time | Token | Cost |",
        f"|------|------|------|----------|-------|------|",
    ]
    lines.extend(daily_lines)

    if sorted_tools:
        lines += [
            f"",
            f"## Top 5 Tool Calls",
            f"",
            f"| Tool | Count |",
            f"|------|------|",
        ]
        for name, count in sorted_tools:
            lines.append(f"| {name} | {count} |")

    if sorted_langs:
        lines += [
            f"",
            f"## Code Changes (by language)",
            f"",
            f"| Language | Added | Removed | Net |",
            f"|------|------|------|------|",
        ]
        for lang, counts in sorted_langs:
            net = counts["added"] - counts["removed"]
            sign = "+" if net >= 0 else ""
            lines.append(f"| {lang} | +{counts['added']} | -{counts['removed']} | {sign}{net} |")

    if merged.token_by_model:
        lines += [
            f"",
            f"## Token Usage (by model)",
            f"",
            f"| Model | Tokens | Cost |",
            f"|------|-------|------|",
        ]
        for model, usage in sorted(merged.token_by_model.items(), key=lambda x: x[1].total, reverse=True):
            mc = 0.0
            p = _match_pricing(model)
            mc += usage.input_tokens / 1e6 * p["input"]
            mc += usage.output_tokens / 1e6 * p["output"]
            mc += usage.cache_read_input_tokens / 1e6 * p["cache_read"]
            mc += usage.cache_creation_input_tokens / 1e6 * p["cache_create"]
            lines.append(f"| {model} | {_fmt_tokens(usage.total)} | {_fmt_cost(mc)} |")

    if len(project_stats) > 1:
        lines += [
            f"",
            f"## Project Distribution",
            f"",
            f"| Project | Sessions | Instructions | Cost |",
            f"|------|------|------|------|",
        ]
        for proj_name, stats_list in sorted(project_stats.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            pm = merge_stats(stats_list) if len(stats_list) > 1 else stats_list[0]
            pc = _estimate_cost(pm)
            lines.append(f"| {proj_name} | {len(stats_list)} | {pm.user_message_count} | {_fmt_cost(pc)} |")

    # Compare with previous period
    prev_since = since - timedelta(days=days)
    prev_stats: list[SessionStats] = []
    for f in session_files:
        try:
            session = parse_session_file(f)
            stats_item = analyze_session(session)
            if stats_item.end_time and prev_since <= stats_item.end_time < since:
                prev_stats.append(stats_item)
        except Exception:
            continue

    if prev_stats:
        prev_merged = merge_stats(prev_stats) if len(prev_stats) > 1 else prev_stats[0]
        prev_cost = _estimate_cost(prev_merged)

        def _delta(curr, prev, fmt_fn=str):
            if prev == 0:
                return "—"
            pct = (curr - prev) / prev * 100
            sign = "+" if pct >= 0 else ""
            return f"{fmt_fn(curr)} ({sign}{pct:.0f}%)"

        prev_title = "Previous Period Comparison"
        lines += [
            f"",
            f"## {prev_title}",
            f"",
            f"| Metric | This {title} | Previous {title} | Change |",
            f"|------|------|------|------|",
            f"| Sessions | {len(all_stats)} | {len(prev_stats)} | {_delta(len(all_stats), len(prev_stats))} |",
            f"| Instructions | {merged.user_message_count} | {prev_merged.user_message_count} | {_delta(merged.user_message_count, prev_merged.user_message_count)} |",
            f"| Token | {_fmt_tokens(merged.token_usage.total)} | {_fmt_tokens(prev_merged.token_usage.total)} | {_delta(merged.token_usage.total, prev_merged.token_usage.total, _fmt_tokens)} |",
            f"| Cost | {_fmt_cost(cost)} | {_fmt_cost(prev_cost)} | {_delta(cost, prev_cost, _fmt_cost)} |",
            f"| Code Added | +{merged.total_added} | +{prev_merged.total_added} | {_delta(merged.total_added, prev_merged.total_added)} |",
        ]

    # Cost projection
    active_days = len([d for d in daily_lines if d])  # Days with data
    if active_days > 0 and cost > 0:
        daily_avg = cost / active_days
        month_projection = daily_avg * 30
        lines += [
            f"",
            f"## Cost Projection",
            f"",
            f"| Metric | Value |",
            f"|------|------|",
            f"| Daily avg cost | {_fmt_cost(daily_avg)} |",
            f"| Monthly projection | {_fmt_cost(month_projection)} |",
            f"| Active days | {active_days}/{days} days |",
        ]

    # Efficiency metrics
    total_tokens = merged.token_usage.total
    total_code = merged.total_added + merged.total_removed
    avg_tokens_per_msg = total_tokens // max(merged.user_message_count, 1)
    code_per_1k_token = round(total_code / max(total_tokens / 1000, 1), 2)
    active_secs = merged.active_duration.total_seconds()
    ai_secs = merged.ai_duration.total_seconds()
    ai_ratio = round(ai_secs / max(active_secs, 1) * 100)

    # Efficiency score (0-100)
    # Code output (40pts): code_per_1k_token, full score at 0.5+
    code_score = min(40, int(code_per_1k_token / 0.5 * 40))
    # Instruction precision (30pts): lower avg_tokens_per_msg is better, full score below 50K
    precision_score = max(0, min(30, int((1 - min(avg_tokens_per_msg, 200_000) / 200_000) * 30)))
    # AI utilization (30pts): full score at 70%+
    util_score = min(30, int(ai_ratio / 70 * 30))
    total_score = code_score + precision_score + util_score

    grade = "S" if total_score >= 90 else "A" if total_score >= 75 else "B" if total_score >= 60 else "C" if total_score >= 40 else "D"

    lines += [
        f"",
        f"## Efficiency Score",
        f"",
        f"**{grade} ({total_score}/100)**",
        f"",
        f"| Dimension | Value | Score |",
        f"|------|------|------|",
        f"| Code output rate | {code_per_1k_token} lines/K Token | {code_score}/40 |",
        f"| Instruction precision | {_fmt_tokens(avg_tokens_per_msg)} Token/msg | {precision_score}/30 |",
        f"| AI utilization | {ai_ratio}% | {util_score}/30 |",
    ]

    lines += [
        f"",
        f"---",
        f"*Generated by [cc-statistics](https://github.com/androidZzT/cc-statistics)*",
    ]

    return "\n".join(lines)
