"""Format statistics output with ANSI colors"""

from __future__ import annotations

import os
from datetime import timedelta

from .analyzer import (
    TOOL_DESCRIPTIONS,
    CacheStats,
    SessionStats,
    SkillUsage,
    compute_cache_stats,
)
from .rate_limiter import RateLimitStatus


# ── ANSI colors ────────────────────────────────────────────
def _supports_color() -> bool:
    """Detect whether the terminal supports color"""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(os, "isatty") and os.isatty(1)


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    """Apply ANSI color to text"""
    if not _COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# Common color shortcuts
def _bold(t: str) -> str: return _c("1", t)
def _dim(t: str) -> str: return _c("2", t)
def _cyan(t: str) -> str: return _c("36", t)
def _green(t: str) -> str: return _c("32", t)
def _red(t: str) -> str: return _c("31", t)
def _yellow(t: str) -> str: return _c("33", t)
def _blue(t: str) -> str: return _c("34", t)
def _magenta(t: str) -> str: return _c("35", t)
def _white_bold(t: str) -> str: return _c("1;37", t)
def _cyan_bold(t: str) -> str: return _c("1;36", t)
def _green_bold(t: str) -> str: return _c("1;32", t)


# ── Formatting helpers ─────────────────────────────────────

def _fmt_duration(td: timedelta) -> str:
    """Format a timedelta into a human-readable string"""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "0s"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _fmt_tokens(n: int) -> str:
    """Format token count"""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _bar(value: int, max_value: int, width: int = 20) -> str:
    """Generate a colored bar chart"""
    if max_value == 0:
        return ""
    filled = int(value / max_value * width)
    bar_filled = "█" * filled
    bar_empty = "░" * (width - filled)
    return _cyan(bar_filled) + _dim(bar_empty)


def _net_str(net: int) -> str:
    """Format net change with color"""
    if net > 0:
        return _green(f"+{net}")
    elif net < 0:
        return _red(str(net))
    return _dim("0")


# ── Cache hit rate ─────────────────────────────────────────

_CACHE_GRADE_COLORS = {
    "excellent": _green,
    "good": _blue,
    "fair": _yellow,
    "poor": _red,
    "na": _dim,
}

_CACHE_GRADE_TIPS = {
    "excellent": "Great! Prompts are well-cached",
    "good": "Good cache utilization",
    "fair": "Room to improve caching",
    "poor": "Low cache hit rate",
    "na": "No cache data available",
}


def format_cache_stats(cache: CacheStats) -> str:
    """Format cache hit rate analysis sub-block"""
    lines: list[str] = []

    color_fn = _CACHE_GRADE_COLORS.get(cache.grade, _dim)
    tip = _CACHE_GRADE_TIPS.get(cache.grade, "")

    lines.append(f"  {_dim('Cache hit rate:')}")

    if cache.grade == "na":
        lines.append(f"    {_dim('N/A')} — {_dim(tip)}")
        lines.append("")
        return "\n".join(lines)

    pct = f"{cache.hit_rate * 100:.1f}%"
    lines.append(f"    {color_fn(f'{cache.grade_label} ({pct})')} — {_dim(tip)}")
    lines.append(
        f"    cache_read: {_fmt_tokens(cache.cache_read_tokens)} / "
        f"total_input: {_fmt_tokens(cache.total_input_tokens)}"
    )

    if cache.savings_usd > 0:
        lines.append(f"    Savings: {_green(f'~${cache.savings_usd:.2f}')}")

    if len(cache.by_model) > 1:
        lines.append(f"    {_dim('By model:')}")
        for model, rate in sorted(cache.by_model.items(), key=lambda x: x[1], reverse=True):
            m_color = _CACHE_GRADE_COLORS.get(_cache_grade_key(rate), _dim)
            lines.append(f"      {_cyan(model)}: {m_color(f'{rate * 100:.1f}%')}")

    lines.append("")
    return "\n".join(lines)


def _cache_grade_key(hit_rate: float) -> str:
    """Return the grade key for a given hit rate (used for color lookup)"""
    if hit_rate >= 0.80:
        return "excellent"
    if hit_rate >= 0.60:
        return "good"
    if hit_rate >= 0.40:
        return "fair"
    return "poor"


# ── Main formatter ─────────────────────────────────────────

def format_stats(stats: SessionStats, session_count: int = 1) -> str:
    """Format statistics results for terminal output"""
    lines: list[str] = []
    sep = _dim("─" * 60)

    # ── Header ──
    lines.append("")
    lines.append(_cyan("  ╔══════════════════════════════════════════════════════════╗"))
    lines.append(_cyan("  ║") + _white_bold("          Claude Code Session Report") + "                 " + _cyan("║"))
    lines.append(_cyan("  ╚══════════════════════════════════════════════════════════╝"))
    lines.append("")

    if stats.project_path and stats.project_path != "all":
        lines.append(f"  {_dim('Project:')} {_bold(stats.project_path)}")
    if session_count > 1:
        lines.append(f"  {_dim('Sessions:')} {_bold(str(session_count))}")
    if stats.start_time:
        start_local = stats.start_time.astimezone()
        end_local = stats.end_time.astimezone() if stats.end_time else None
        end_str = end_local.strftime('%Y-%m-%d %H:%M') if end_local else '?'
        lines.append(f"  {_dim('Time range:')} {start_local.strftime('%Y-%m-%d %H:%M')} ~ {end_str}")
    lines.append("")

    # ── ① User Instructions ──
    lines.append(f"  {_cyan_bold('①')} {_bold('User Instructions')}")
    lines.append(sep)
    lines.append(f"  Conversation turns: {_yellow(str(stats.user_message_count))}")
    lines.append("")

    # ── ② AI Tool Calls ──
    lines.append(f"  {_cyan_bold('②')} {_bold('AI Tool Calls')}")
    lines.append(sep)
    lines.append(f"  Total calls: {_yellow(str(stats.tool_call_total))}")
    lines.append("")
    if stats.tool_call_counts:
        sorted_tools = sorted(
            stats.tool_call_counts.items(), key=lambda x: x[1], reverse=True
        )
        max_count = sorted_tools[0][1] if sorted_tools else 1
        max_name_len = max(len(name) for name, _ in sorted_tools)

        for name, count in sorted_tools:
            desc = TOOL_DESCRIPTIONS.get(name, "")
            bar = _bar(count, max_count, 15)
            desc_part = f"  {_dim(desc)}" if desc else ""
            lines.append(
                f"  {_bold(f'{name:<{max_name_len}}')}  {bar} {_yellow(f'{count:>5}')}{desc_part}"
            )
    lines.append("")

    # ── ③ Dev Time ──
    lines.append(f"  {_cyan_bold('③')} {_bold('Dev Time')}")
    lines.append(sep)
    lines.append(f"  Active time:     {_green_bold(_fmt_duration(stats.active_duration))}")
    lines.append(f"    {_blue('AI processing:')}    {_blue(_fmt_duration(stats.ai_duration))}")
    lines.append(f"    {_magenta('User active:')}  {_magenta(_fmt_duration(stats.user_duration))}")
    if stats.active_duration.total_seconds() > 0:
        ai_ratio = stats.ai_duration.total_seconds() / stats.active_duration.total_seconds() * 100
        lines.append(f"  AI ratio:      {_blue(f'{ai_ratio:.0f}%')}")
    if stats.turn_count:
        avg_ai = stats.ai_duration / stats.turn_count
        lines.append(f"  Avg turn time: {_fmt_duration(avg_ai)}/turn {_dim(f'({stats.turn_count} turns)')}")
    lines.append("")

    # ── ④ Code Changes ──
    lines.append(f"  {_cyan_bold('④')} {_bold('Code Changes')}")
    lines.append(sep)

    if stats.git_available:
        git_net = stats.git_total_added - stats.git_total_removed
        ai_pct = (
            f" ({stats.git_ai_commit_count}/{stats.git_commit_count} AI)"
            if stats.git_ai_commit_count > 0
            else ""
        )
        lines.append(f"  {_yellow('[Git committed]')}  {stats.git_commit_count} commits{_cyan(ai_pct)}")
        lines.append(f"  Added: {_green(f'+{stats.git_total_added}')}  Removed: {_red(f'-{stats.git_total_removed}')}  Net: {_net_str(git_net)}")
        if stats.git_ai_commit_count > 0:
            ai_code_pct = round(
                (stats.git_ai_added + stats.git_ai_removed)
                / max(stats.git_total_added + stats.git_total_removed, 1)
                * 100
            )
            lines.append(
                f"  AI code: {_cyan(f'+{stats.git_ai_added}')} {_cyan(f'-{stats.git_ai_removed}')}  "
                f"Share: {_cyan(f'{ai_code_pct}%')}"
            )
        lines.append("")

        if stats.git_lines_by_lang:
            sorted_langs = sorted(
                stats.git_lines_by_lang.items(),
                key=lambda x: x[1]["added"] + x[1]["removed"],
                reverse=True,
            )
            max_lang_len = max(len(lang) for lang, _ in sorted_langs)
            for lang, counts in sorted_langs:
                added = counts["added"]
                removed = counts["removed"]
                net_l = added - removed
                lines.append(
                    f"  {_dim(f'{lang:<{max_lang_len}}')}  {_green(f'+{added:<6}')} {_red(f'-{removed:<6}')} net {_net_str(net_l)}"
                )
        lines.append("")

    ai_net = stats.total_added - stats.total_removed
    lines.append(f"  {_blue('[AI tool changes]')}  {_dim('from Edit/Write calls')}")
    lines.append(f"  Added: {_green(f'+{stats.total_added}')}  Removed: {_red(f'-{stats.total_removed}')}  Net: {_net_str(ai_net)}")
    lines.append("")

    if stats.lines_by_lang:
        sorted_langs = sorted(
            stats.lines_by_lang.items(),
            key=lambda x: x[1]["added"] + x[1]["removed"],
            reverse=True,
        )
        max_lang_len = max(len(lang) for lang, _ in sorted_langs)
        for lang, counts in sorted_langs:
            added = counts["added"]
            removed = counts["removed"]
            net_l = added - removed
            lines.append(
                f"  {_dim(f'{lang:<{max_lang_len}}')}  {_green(f'+{added:<6}')} {_red(f'-{removed:<6}')} net {_net_str(net_l)}"
            )
    lines.append("")

    # ── ⑤ Token Usage ──
    lines.append(f"  {_cyan_bold('⑤')} {_bold('Token Usage')}")
    lines.append(sep)
    tu = stats.token_usage
    lines.append(f"  Input tokens:          {_fmt_tokens(tu.input_tokens):>10}")
    lines.append(f"  Output tokens:         {_yellow(_fmt_tokens(tu.output_tokens)):>22}")
    lines.append(f"  Cache read tokens:     {_dim(_fmt_tokens(tu.cache_read_input_tokens)):>22}")
    lines.append(f"  Cache creation tokens: {_dim(_fmt_tokens(tu.cache_creation_input_tokens)):>22}")
    lines.append(f"  {_dim('─' * 40)}")
    lines.append(f"  Total:                 {_white_bold(_fmt_tokens(tu.total)):>22}")
    lines.append("")

    if stats.token_by_model:
        lines.append(f"  {_dim('By model:')}")
        for model, usage in sorted(stats.token_by_model.items()):
            if usage.total == 0:
                continue
            lines.append(
                f"    {_cyan(model)}: "
                f"input={_fmt_tokens(usage.input_tokens)} "
                f"output={_yellow(_fmt_tokens(usage.output_tokens))} "
                f"cache_read={_dim(_fmt_tokens(usage.cache_read_input_tokens))} "
                f"total={_bold(_fmt_tokens(usage.total))}"
            )
    lines.append("")

    # ── Cache hit rate analysis (⑤ sub-block) ──
    cache = compute_cache_stats(stats.token_usage, stats.token_by_model)
    lines.append(format_cache_stats(cache))

    # ⑥ Efficiency Score
    total_tokens = stats.token_usage.total
    total_code = stats.total_added + stats.total_removed
    if total_tokens > 0 and stats.user_message_count > 0:
        avg_tokens_per_msg = total_tokens // stats.user_message_count
        code_per_1k = round(total_code / max(total_tokens / 1000, 1), 2)
        active_secs = stats.active_duration.total_seconds()
        ai_secs = stats.ai_duration.total_seconds()
        ai_ratio = round(ai_secs / max(active_secs, 1) * 100)

        code_score = min(40, int(code_per_1k / 0.5 * 40))
        precision_score = max(0, min(30, int((1 - min(avg_tokens_per_msg, 200_000) / 200_000) * 30)))
        util_score = min(30, int(ai_ratio / 70 * 30))
        total_score = code_score + precision_score + util_score
        grade = "S" if total_score >= 90 else "A" if total_score >= 75 else "B" if total_score >= 60 else "C" if total_score >= 40 else "D"

        grade_color = _green if grade in ("S", "A") else _yellow if grade == "B" else _red
        lines.append(f"  {_bold('⑥ Efficiency Score')}")
        lines.append("─" * 60)
        lines.append(f"  Score: {grade_color(f'{grade} ({total_score}/100)')}")
        lines.append(f"  Code output: {_fmt_tokens(total_code)} lines / {_fmt_tokens(total_tokens)} Token = {_cyan(f'{code_per_1k} lines/K')}")
        lines.append(f"  Instruction precision: {_fmt_tokens(avg_tokens_per_msg)} Token/msg")
        lines.append(f"  AI utilization: {ai_ratio}%")
        lines.append("")

    # ── ⑧ Coding Rhythm & Work Mode ──
    rhythm_block = format_coding_rhythm(stats)
    if rhythm_block:
        lines.append(rhythm_block)

    # ── ⑨ Usage Quota Forecast ──
    from .rate_limiter import analyze_rate_limit
    rl_status = analyze_rate_limit(stats)
    rl_block = format_rate_limit(rl_status)
    if rl_block:
        lines.append(rl_block)

    return "\n".join(lines)


_PERIOD_LABELS = {
    "morning": "Morning   (06-12)",
    "afternoon": "Afternoon (12-18)",
    "evening": "Evening   (18-24)",
    "night": "Night     (00-06)",
}

_PERIOD_ORDER = ["morning", "afternoon", "evening", "night"]

_MODE_ICONS = {
    "Exploration": "🔍",
    "Building": "🏗️",
    "Execution": "⚡",
}


def format_coding_rhythm(stats: SessionStats) -> str:
    """Format the coding rhythm and work mode block (⑧)"""
    has_rhythm = bool(stats.coding_rhythm)
    has_modes = bool(stats.work_mode_distribution)
    if not has_rhythm and not has_modes:
        return ""

    lines: list[str] = []
    sep = _dim("─" * 60)

    lines.append(f"  {_cyan_bold('⑧')} {_bold('Coding Rhythm & Work Mode')}")
    lines.append(sep)

    # Coding Rhythm — horizontal bar chart (by token count)
    if has_rhythm:
        lines.append(f"  {_dim('Coding rhythm (by period):')}")

        max_tokens = max(
            (int(d.get("token_count", 0)) for d in stats.coding_rhythm.values()),
            default=0,
        )
        peak_period = max(
            stats.coding_rhythm,
            key=lambda p: int(stats.coding_rhythm[p].get("token_count", 0)),
        )

        for period in _PERIOD_ORDER:
            data = stats.coding_rhythm.get(period)
            if data is None:
                label = _PERIOD_LABELS[period]
                lines.append(f"    {_dim(label)}  {_dim('░' * 20)}  {_dim('—')}")
                continue

            tokens = int(data.get("token_count", 0))
            sessions = int(data.get("session_count", 0))
            minutes = float(data.get("active_minutes", 0))
            label = _PERIOD_LABELS[period]
            bar = _bar(tokens, max_tokens, 20)
            token_str = _fmt_tokens(tokens)
            detail = f"{sessions}s {minutes:.0f}min"

            if period == peak_period:
                lines.append(
                    f"    {_yellow(label)}  {bar}  {_yellow(token_str)}  "
                    f"{_dim(detail)}  {_yellow('★')}"
                )
            else:
                lines.append(
                    f"    {label}  {bar}  {token_str}  {_dim(detail)}"
                )
        lines.append("")

    # Work Mode Distribution
    if has_modes:
        lines.append(f"  {_dim('Work mode distribution:')}")
        total_sessions = sum(stats.work_mode_distribution.values())
        for mode in ("Exploration", "Building", "Execution"):
            count = stats.work_mode_distribution.get(mode, 0)
            pct = count / max(total_sessions, 1) * 100
            icon = _MODE_ICONS.get(mode, "")
            bar = _bar(count, total_sessions, 15)
            if pct >= 50:
                pct_str = _yellow(f"{pct:.0f}%")
            else:
                pct_str = f"{pct:.0f}%"
            lines.append(
                f"    {icon} {mode:<13} {bar}  {pct_str}  {_dim(f'({count}s)')}"
            )
        lines.append("")

    return "\n".join(lines)


def format_rate_limit(status: RateLimitStatus) -> str:
    """Format Usage Quota forecast result

    Returns empty string when idle (block not displayed).
    """
    if status.status == "idle":
        return ""

    lines: list[str] = []
    sep = _dim("─" * 60)

    lines.append(f"  {_cyan_bold('⑨')} {_bold('Usage Quota Forecast')}")
    lines.append(sep)

    # Status label
    if status.status == "safe":
        status_label = f"\u2705 {_green('SAFE')}"
    elif status.status == "warning":
        status_label = f"\u26a0\ufe0f  {_yellow('WARNING')}"
    else:
        status_label = f"\U0001f534 {_red('CRITICAL')}"

    lines.append(f"  Status:      {status_label}")

    # Rate
    rate_str = f"{status.rate_per_min:,.0f} tokens/min (5-min window)"
    lines.append(f"  Rate:        {_yellow(rate_str)}")

    # Window usage
    pct_display = status.pct * 100
    pct_str = f"{pct_display:.0f}%"
    if status.pct >= 0.85:
        pct_colored = _red(pct_str)
    elif status.pct >= 0.60:
        pct_colored = _yellow(pct_str)
    else:
        pct_colored = _green(pct_str)
    lines.append(
        f"  Window:      {status.window_used:,} / "
        f"{status.window_limit:,} ({pct_colored})"
    )

    # Remaining / ETA
    if status.status == "critical":
        if status.minutes_until_limit is not None and status.minutes_until_limit <= 0:
            eta_str = _red("~0 min until quota limit \u2014 Consider pausing")
        elif status.minutes_until_limit is not None:
            eta_str = _red(f"~{status.minutes_until_limit:.0f} min until quota limit \u2014 Consider pausing")
        else:
            eta_str = _red("Consider pausing")
        lines.append(f"  ETA:         {eta_str}")
    else:
        if status.minutes_until_limit is not None:
            if status.minutes_until_limit <= 0:
                lines.append(f"  Remaining:   {_red('limit reached')}")
            else:
                rem_str = f"~{status.minutes_until_limit:.0f} min of headroom"
                if status.status == "warning":
                    rem_str = _yellow(rem_str)
                lines.append(f"  Remaining:   {rem_str}")
        else:
            lines.append(f"  Remaining:   {_dim('N/A')}")

    lines.append("")
    return "\n".join(lines)


def format_skill_stats(stats: SessionStats, session_count: int = 1) -> str:
    """Format Skill usage statistics for terminal output"""
    lines: list[str] = []
    sep = _dim("─" * 60)

    # Header
    lines.append("")
    lines.append(_cyan("  ╔══════════════════════════════════════════════════════════╗"))
    lines.append(_cyan("  ║") + _white_bold("            Skill Usage Report") + "                   " + _cyan("║"))
    lines.append(_cyan("  ╚══════════════════════════════════════════════════════════╝"))
    lines.append("")

    if stats.project_path and stats.project_path != "all":
        lines.append(f"  {_dim('Repo:')} {_bold(stats.project_path)}")
    if session_count > 1:
        lines.append(f"  {_dim('Sessions:')} {_bold(str(session_count))}")
    if stats.start_time:
        start_local = stats.start_time.astimezone()
        end_local = stats.end_time.astimezone() if stats.end_time else None
        end_str = end_local.strftime('%Y-%m-%d %H:%M') if end_local else '?'
        lines.append(f"  {_dim('Time range:')} {start_local.strftime('%Y-%m-%d %H:%M')} ~ {end_str}")
    lines.append("")

    if not stats.skill_stats:
        lines.append(f"  {_dim('No Skill invocations found')}")
        lines.append("")
        return "\n".join(lines)

    # ── ① Call count rankings ──
    sorted_skills = sorted(
        stats.skill_stats.values(), key=lambda s: s.call_count, reverse=True
    )
    total_calls = sum(s.call_count for s in sorted_skills)

    lines.append(f"  {_cyan_bold('①')} {_bold('Skill Usage Rankings')}")
    lines.append(sep)
    lines.append(f"  Skills: {_yellow(str(len(sorted_skills)))}  Total calls: {_yellow(str(total_calls))}")
    lines.append("")

    max_count = sorted_skills[0].call_count if sorted_skills else 1
    max_name_len = max(len(s.name) for s in sorted_skills)

    for su in sorted_skills:
        bar = _bar(su.call_count, max_count, 15)
        lines.append(
            f"  {_bold(f'{su.name:<{max_name_len}}')}  {bar} {_yellow(f'{su.call_count:>5}')}"
        )
    lines.append("")

    # ── ② Success rate ──
    lines.append(f"  {_cyan_bold('②')} {_bold('Success/Failure Rate')}")
    lines.append(sep)

    for su in sorted_skills:
        resolved = su.success_count + su.error_count
        if resolved > 0:
            success_rate = su.success_count / resolved * 100
            rate_color = _green if success_rate >= 90 else _yellow if success_rate >= 70 else _red
            rate_str = rate_color(f"{success_rate:.0f}%")
        else:
            rate_str = _dim("N/A")

        parts = []
        if su.success_count:
            parts.append(_green(f"✓{su.success_count}"))
        if su.error_count:
            parts.append(_red(f"✗{su.error_count}"))
        if su.unknown_count:
            parts.append(_dim(f"?{su.unknown_count}"))
        detail = " ".join(parts)

        lines.append(
            f"  {su.name:<{max_name_len}}  Success rate: {rate_str}  ({detail})"
        )
    lines.append("")

    # ── ③ Time distribution (by hour) ──
    lines.append(f"  {_cyan_bold('③')} {_bold('Time Distribution (by hour)')}")
    lines.append(sep)

    # Aggregate hourly distribution across all skills
    hourly_total: dict[int, int] = {}
    for su in sorted_skills:
        for h, c in su.hourly_dist.items():
            hourly_total[h] = hourly_total.get(h, 0) + c

    if hourly_total:
        max_hourly = max(hourly_total.values())
        for hour in range(24):
            count = hourly_total.get(hour, 0)
            if count == 0:
                continue
            bar = _bar(count, max_hourly, 20)
            lines.append(f"  {hour:02d}:00  {bar} {_yellow(f'{count:>3}')}")
    else:
        lines.append(f"  {_dim('No time distribution data')}")
    lines.append("")

    # ── ④ Time distribution (by day) ──
    daily_total: dict[str, int] = {}
    for su in sorted_skills:
        for d, c in su.daily_dist.items():
            daily_total[d] = daily_total.get(d, 0) + c

    if daily_total:
        lines.append(f"  {_cyan_bold('④')} {_bold('Time Distribution (by day)')}")
        lines.append(sep)

        sorted_days = sorted(daily_total.items())
        max_daily = max(daily_total.values())
        for day, count in sorted_days:
            bar = _bar(count, max_daily, 20)
            lines.append(f"  {day}  {bar} {_yellow(f'{count:>3}')}")
        lines.append("")

    return "\n".join(lines)


def format_git_integration(result) -> str:
    """Format Git integration analysis results (Top Commits by AI Cost)

    Args:
        result: GitIntegrationResult instance

    Returns:
        Formatted terminal output string
    """
    from .git_integration import GitIntegrationResult

    lines: list[str] = []
    sep = _dim("─" * 60)

    lines.append("")
    lines.append(_cyan("  ╔══════════════════════════════════════════════════════════╗"))
    lines.append(_cyan("  ║") + _white_bold("        Git Integration — AI Cost per Commit") + "     " + _cyan("║"))
    lines.append(_cyan("  ╚══════════════════════════════════════════════════════════╝"))
    lines.append("")

    lines.append(f"  {_dim('Repo:')} {_bold(result.repo_path)}")
    lines.append(f"  {_dim('Commits:')} {_bold(str(result.total_commits))}")
    lines.append(f"  {_dim('Sessions matched:')} {_bold(str(result.sessions_matched))}")
    lines.append(
        f"  {_dim('Total AI cost:')} {_green(f'~${result.total_cost_usd:.3f}')}"
        f"  {_dim('(')} {_fmt_tokens(result.total_tokens)} tokens {_dim(')')}"
    )
    lines.append("")

    if not result.commit_costs:
        lines.append(f"  {_dim('No commits found in the specified range.')}")
        lines.append("")
        return "\n".join(lines)

    # Top 10 commits by cost
    sorted_costs = sorted(result.commit_costs, key=lambda c: c.estimated_cost_usd, reverse=True)
    top = sorted_costs[:10]

    lines.append(f"  {_cyan_bold('Top Commits by AI Cost')}")
    lines.append(sep)

    max_cost = max((c.estimated_cost_usd for c in top), default=0)

    for cc in top:
        if cc.total_tokens == 0:
            continue

        commit = cc.commit
        # Short hash
        short_hash = commit.hash[:7]
        # Truncate message
        msg = commit.message[:40] + ("…" if len(commit.message) > 40 else "")
        # Date
        local_ts = commit.timestamp.astimezone()
        date_str = local_ts.strftime("%m-%d %H:%M")
        # Cost
        cost_str = f"${cc.estimated_cost_usd:.3f}"
        # Bar
        bar = _bar(int(cc.estimated_cost_usd * 10000), int(max_cost * 10000), 12)

        lines.append(
            f"  {_dim(date_str)}  {_cyan(short_hash)}  {bar}  "
            f"{_yellow(cost_str):>10}  {_fmt_tokens(cc.total_tokens):>7}  "
            f"{_dim(f'{cc.session_count}s')}  {msg}"
        )

    lines.append("")

    # All commits summary table (if more than top 10)
    if len(result.commit_costs) > 10:
        lines.append(f"  {_dim(f'(showing top 10 of {len(result.commit_costs)} commits with AI activity)')}")
        lines.append("")

    return "\n".join(lines)
