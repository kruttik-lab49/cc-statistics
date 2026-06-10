"""CLI entry point"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .analyzer import SessionStats, TokenUsage, analyze_session, merge_stats
from .formatter import format_skill_stats, format_stats
from .parser import (
    _claude_session_entry_files,
    find_codex_sessions,
    find_codex_sessions_by_keyword,
    find_gemini_sessions,
    find_gemini_sessions_by_keyword,
    find_sessions,
    find_sessions_by_keyword,
    parse_session_file,
)


def _parse_session(path: Path):
    """Select parser based on file type"""
    return parse_session_file(path)


def _parse_time_arg(value: str, *, as_end_of_day: bool = False) -> datetime:
    """Parse time argument supporting multiple formats:

    Absolute time:
      2026-03-13
      2026-03-13T10:00
      2026-03-13T10:00:00

    Relative time (relative to now):
      1h    → 1 hour ago
      3d    → 3 days ago
      2w    → 2 weeks ago

    as_end_of_day: when True and input is a plain date, fills in 23:59:59 for that day
                   used for --until so that --until 2026-04-03 includes all of 04-03
    """
    value = value.strip()

    # Relative time
    if value and value[-1] in ("h", "d", "w") and value[:-1].isdigit():
        n = int(value[:-1])
        unit = value[-1]
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return datetime.now(tz=timezone.utc) - delta

    # Absolute time (treated as local time)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            # When a plain date is used as the until argument, fill in end-of-day 23:59:59
            if fmt == "%Y-%m-%d" and as_end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    raise argparse.ArgumentTypeError(
        f"Cannot parse time: {value} (supported formats: 2026-03-13, 2026-03-13T10:00, 3d, 2w, 1h)"
    )


def _trim_stats_by_date_range(
    stats: SessionStats,
    since_date: str | None,
    until_date: str | None,
) -> None:
    """Trim token_by_date by local date range and recompute token_usage

    When --since/--until are specified, sessions that span the date range only count tokens
    for dates within the range.
    since_date / until_date format: "YYYY-MM-DD" local date string.
    """
    if not stats.token_by_date:
        return
    if not since_date and not until_date:
        return

    trimmed: dict[str, TokenUsage] = {}
    for date_key, tu in stats.token_by_date.items():
        if since_date and date_key < since_date:
            continue
        if until_date and date_key > until_date:
            continue
        trimmed[date_key] = tu

    stats.token_by_date = trimmed

    had_model_by_date = bool(stats.token_by_model_by_date)
    trimmed_model_by_date: dict[str, dict[str, TokenUsage]] = {}
    for date_key, model_map in stats.token_by_model_by_date.items():
        if since_date and date_key < since_date:
            continue
        if until_date and date_key > until_date:
            continue
        trimmed_model_by_date[date_key] = model_map
    stats.token_by_model_by_date = trimmed_model_by_date

    # Recompute token_usage total from trimmed token_by_date
    new_usage = TokenUsage()
    for tu in trimmed.values():
        new_usage.input_tokens += tu.input_tokens
        new_usage.output_tokens += tu.output_tokens
        new_usage.cache_read_input_tokens += tu.cache_read_input_tokens
        new_usage.cache_creation_input_tokens += tu.cache_creation_input_tokens
    stats.token_usage = new_usage

    if stats.coding_rhythm:
        old_rhythm_tokens = sum(
            int(data.get("token_count", 0) or 0)
            for data in stats.coding_rhythm.values()
        )
        periods = list(stats.coding_rhythm.items())
        allocated = 0
        for idx, (_period, data) in enumerate(periods):
            if old_rhythm_tokens <= 0:
                new_count = 0
            elif idx == len(periods) - 1:
                new_count = new_usage.total - allocated
            else:
                old_count = int(data.get("token_count", 0) or 0)
                new_count = int(new_usage.total * old_count / old_rhythm_tokens)
                allocated += new_count
            data["token_count"] = max(new_count, 0)

    # Recompute model breakdown to keep totals and cost/model details consistent after date filtering.
    if trimmed_model_by_date:
        new_by_model: dict[str, TokenUsage] = {}
        for model_map in trimmed_model_by_date.values():
            for model, tu in model_map.items():
                if model not in new_by_model:
                    new_by_model[model] = TokenUsage()
                m = new_by_model[model]
                m.input_tokens += tu.input_tokens
                m.output_tokens += tu.output_tokens
                m.cache_read_input_tokens += tu.cache_read_input_tokens
                m.cache_creation_input_tokens += tu.cache_creation_input_tokens
        stats.token_by_model = new_by_model
    elif had_model_by_date:
        stats.token_by_model = {}


def _resolve_project_name(proj_dir: Path, jsonl_files: list[Path]) -> str:
    """Resolve the real project path from the cwd field in JSONL files"""
    import json
    for jf in jsonl_files:
        with open(jf, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    obj = json.loads(ln)
                    if obj.get("cwd"):
                        return obj["cwd"]
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
    # fallback: use the directory name itself
    return proj_dir.name


def _display_width(s: str) -> int:
    """Calculate terminal display width of a string (CJK characters count as 2 columns)"""
    width = 0
    for c in s:
        if unicodedata.east_asian_width(c) in ('W', 'F'):
            width += 2
        else:
            width += 1
    return width


def _pad_right(s: str, width: int) -> str:
    """Right-pad with spaces to the specified display width"""
    return s + ' ' * (width - _display_width(s))


def _pad_left(s: str, width: int) -> str:
    """Left-pad with spaces to the specified display width (right-align)"""
    return ' ' * (width - _display_width(s)) + s


def _compare_projects(args) -> None:
    """Compare key metrics across all projects"""
    from .formatter import _fmt_duration, _fmt_tokens

    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        print("No Claude Code project data found")
        return

    projects: list[dict] = []

    for proj in sorted(claude_projects.iterdir()):
        if not proj.is_dir():
            continue
        jsonl_files = _claude_session_entry_files(proj)
        if not jsonl_files:
            continue

        name = _resolve_project_name(proj, jsonl_files)
        # Simplify path display
        short_name = Path(name).name if "/" in name else name

        all_stats = []
        for f in jsonl_files:
            try:
                session = _parse_session(f)
                stats = analyze_session(session)

                # Time filtering
                if args.since and stats.end_time and stats.end_time < args.since:
                    continue
                if args.until and stats.start_time and stats.start_time > args.until:
                    continue

                all_stats.append(stats)
            except Exception:
                continue

        if not all_stats:
            continue

        # Trim tokens by date range
        if args.since or args.until:
            sd = args.since.astimezone().strftime("%Y-%m-%d") if args.since else None
            ud = args.until.astimezone().strftime("%Y-%m-%d") if args.until else None
            for s in all_stats:
                _trim_stats_by_date_range(s, sd, ud)

        merged = merge_stats(all_stats) if len(all_stats) > 1 else all_stats[0]

        from .reporter import _estimate_cost
        cost = _estimate_cost(merged)

        projects.append({
            "name": short_name,
            "sessions": len(all_stats),
            "instructions": merged.user_message_count,
            "duration": merged.active_duration,
            "tokens": merged.token_usage.total,
            "cost": cost,
            "added": merged.total_added + merged.git_total_added,
            "removed": merged.total_removed + merged.git_total_removed,
            "grade": merged.efficiencyGrade if hasattr(merged, 'efficiencyGrade') else "",
        })

    if not projects:
        print("No project data")
        return

    # Sort by total token count descending
    projects.sort(key=lambda p: p["tokens"], reverse=True)

    # Calculate column widths
    max_name = max(_display_width(p["name"]) for p in projects)
    max_name = max(max_name, 7)  # minimum width

    # Header
    print()
    COL_SESSIONS = 7
    COL_INSTRUCTIONS = 12
    COL_DURATION = 11
    COL_TOKENS = 8
    COL_COST = 8
    COL_CODE = 10
    print(f"  {_pad_right('Project', max_name)}  {_pad_left('Sessions', COL_SESSIONS)}  {_pad_left('Instructions', COL_INSTRUCTIONS)}  {_pad_left('Active Time', COL_DURATION)}  {_pad_left('Token', COL_TOKENS)}  {_pad_left('Cost', COL_COST)}  {_pad_left('Code', COL_CODE)}")
    sep_width = max_name + 2 + COL_SESSIONS + 2 + COL_INSTRUCTIONS + 2 + COL_DURATION + 2 + COL_TOKENS + 2 + COL_COST + 2 + COL_CODE + 2
    print("─" * sep_width)

    total_sessions = 0
    total_instructions = 0
    total_tokens = 0
    total_cost = 0.0

    for p in projects:
        dur_str = _fmt_duration(p["duration"])
        tok_str = _fmt_tokens(p["tokens"])
        cost_str = f"${p['cost']:.0f}" if p["cost"] >= 1 else f"${p['cost']:.2f}"
        code_str = f"+{p['added']}/-{p['removed']}"

        print(f"  {_pad_right(p['name'], max_name)}  {p['sessions']:>7}  {p['instructions']:>12}  {dur_str:>11}  {tok_str:>8}  {cost_str:>8}  {code_str:>10}")

        total_sessions += p["sessions"]
        total_instructions += p["instructions"]
        total_tokens += p["tokens"]
        total_cost += p["cost"]

    print("─" * sep_width)
    print(f"  {_pad_right('Total', max_name)}  {total_sessions:>7}  {total_instructions:>12}  {'':>11}  {_fmt_tokens(total_tokens):>8}  ${total_cost:>7.0f}")
    print()


def _list_projects() -> None:
    """List all known projects (Claude + Codex + Gemini)"""
    has_any = False

    # Claude projects
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        print("\nAvailable projects (Claude Code):")
        print("─" * 60)
        for proj in sorted(claude_projects.iterdir()):
            if not proj.is_dir():
                continue
            jsonl_files = _claude_session_entry_files(proj)
            if not jsonl_files:
                continue
            display_name = _resolve_project_name(proj, jsonl_files)
            print(f"  {display_name}  ({len(jsonl_files)} sessions)")
            has_any = True

    # Codex projects
    codex_sessions = find_codex_sessions()
    if codex_sessions:
        from collections import defaultdict
        codex_by_dir: dict[str, list[Path]] = defaultdict(list)
        for cf in codex_sessions:
            try:
                session = _parse_session(cf)
                key = session.project_path or "Unknown"
            except Exception:
                key = "Unknown"
            codex_by_dir[key].append(cf)

        print("\nAvailable projects (Codex):")
        print("─" * 60)
        for name, files in sorted(codex_by_dir.items()):
            display = Path(name).name if "/" in name else name
            print(f"  {display}  ({len(files)} sessions)")
            has_any = True

    # Gemini projects
    gemini_sessions = find_gemini_sessions()
    if gemini_sessions:
        # Group by project directory
        from collections import defaultdict
        gemini_by_dir: dict[str, list[Path]] = defaultdict(list)
        for gf in gemini_sessions:
            try:
                session = _parse_session(gf)
                key = session.project_path or gf.parent.parent.name
            except Exception:
                key = gf.parent.parent.name
            gemini_by_dir[key].append(gf)

        print("\nAvailable projects (Gemini CLI):")
        print("─" * 60)
        for name, files in sorted(gemini_by_dir.items()):
            display = Path(name).name if "/" in name else name
            print(f"  {display}  ({len(files)} sessions)")
            has_any = True

    if not has_any:
        print("No project data found")
    print()


def _check_update_hint() -> str | None:
    """Check cached update info at startup (no network request, non-blocking)"""
    try:
        from .version_checker import get_cached_update, format_update_message
        result = get_cached_update()
        if result is not None:
            return format_update_message(result)
    except Exception:
        pass
    return None


def _trigger_background_check() -> None:
    """Trigger version check in a background thread (does not block the CLI main flow)"""
    import threading

    def _run() -> None:
        try:
            from .version_checker import check_for_update
            check_for_update()
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _show_rate_limit(args) -> None:
    """Display Usage Quota forecast (analyzes all sessions in the past 1 hour)"""
    from .formatter import format_rate_limit
    from .rate_limiter import analyze_rate_limit

    # Collect all session files (Claude + Codex + Gemini)
    session_files: list[Path] = find_sessions()
    session_files.extend(find_codex_sessions())
    session_files.extend(find_gemini_sessions())

    if not session_files:
        print("No session files found.", file=sys.stderr)
        sys.exit(1)

    # Only keep files modified within the past 1 hour
    one_hour_ago = datetime.now().timestamp() - 3600
    session_files = [f for f in session_files if f.stat().st_mtime >= one_hour_ago]

    if not session_files:
        print("No active sessions in the past 1 hour.")
        return

    all_stats = []
    for f in session_files:
        try:
            session = _parse_session(f)
            stats = analyze_session(session)
            all_stats.append(stats)
        except Exception:
            continue

    if not all_stats:
        print("Unable to analyze session data.", file=sys.stderr)
        sys.exit(1)

    result = merge_stats(all_stats) if len(all_stats) > 1 else all_stats[0]
    rl_status = analyze_rate_limit(result, window_limit=args.window_limit)
    output = format_rate_limit(rl_status)
    if output:
        print(output)
    else:
        print("No active token consumption data (idle).")




def _show_git_integration(args) -> None:
    """Display Git integration analysis: attribute sessions to commits by time, compute AI cost per commit"""
    from .formatter import format_git_integration
    from .git_integration import analyze_git_integration

    repo_path = Path(args.git).resolve()
    if not repo_path.exists():
        import sys
        print(f"Repository path does not exist: {repo_path}", file=sys.stderr)
        sys.exit(1)

    # Collect all session files
    session_files: list[Path] = find_sessions()
    session_files.extend(find_codex_sessions())
    session_files.extend(find_gemini_sessions())

    if not session_files:
        import sys
        print("No session files found.", file=sys.stderr)
        sys.exit(1)

    # Parse & analyze
    all_stats = []
    for f in session_files:
        try:
            session = _parse_session(f)
            stats = analyze_session(session)
            if args.since and stats.end_time and stats.end_time < args.since:
                continue
            if args.until and stats.start_time and stats.start_time > args.until:
                continue
            all_stats.append(stats)
        except Exception:
            continue

    if not all_stats:
        import sys
        print("No sessions in the specified time range.", file=sys.stderr)
        sys.exit(1)

    result = analyze_git_integration(
        repo_path=str(repo_path),
        all_stats=all_stats,
        since=args.since if args.since else None,
        until=args.until if args.until else None,
    )
    print(format_git_integration(result))

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cc-stats",
        description="AI coding session statistics — Claude Code / Codex / Gemini CLI",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"cc-statistics {__version__}",
        help="Show version number and exit",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a JSONL file or project directory. Defaults to all sessions in the current directory.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze all sessions across all projects",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_projects",
        help="List all known projects",
    )
    parser.add_argument(
        "--skills",
        action="store_true",
        help="Show Skill usage statistics (call count, success rate, time distribution)",
    )
    parser.add_argument(
        "--last",
        type=int,
        metavar="N",
        help="Only analyze the most recent N sessions",
    )
    parser.add_argument(
        "--since",
        type=str,
        metavar="TIME",
        help="Only include sessions after this time (e.g. 2026-03-13, 3d, 2w, 1h)",
    )
    parser.add_argument(
        "--until",
        type=str,
        metavar="TIME",
        help="Only include sessions before this time (e.g. 2026-03-14, 1d)",
    )

    parser.add_argument(
        "--report",
        choices=["week", "month"],
        metavar="PERIOD",
        help="Generate a weekly (week) or monthly (month) report in Markdown format",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare key metrics across all projects",
    )
    parser.add_argument(
        "--notify",
        metavar="WEBHOOK_URL",
        help="Send today's statistics to a Webhook (auto-detects Feishu/DingTalk/Slack)",
    )
    parser.add_argument(
        "--platform",
        choices=["feishu", "dingtalk", "slack"],
        help="Specify the Webhook platform (used with --notify)",
    )
    parser.add_argument(
        "--export-chat",
        metavar="KEYWORD",
        help="Export session as Markdown (search by session ID prefix or content keyword)",
    )
    parser.add_argument(
        "--include-tools",
        action="store_true",
        help="Include tool calls in the export (used with --export-chat)",
    )
    parser.add_argument(
        "--rate-limit",
        action="store_true",
        help="Show usage quota forecast (analyzes output token rate of recent sessions)",
    )
    parser.add_argument(
        "--window-limit",
        type=int,
        default=40000,
        metavar="TOKENS",
        help="Usage Quota window limit (default 40000; set to 80000 for Max subscription)",
    )
    parser.add_argument(
        "--git",
        nargs="?",
        const=".",
        metavar="REPO_PATH",
        help="Show Git integration analysis: attribute sessions to commits by time and compute Token/cost per commit (defaults to current directory)",
    )

    args = parser.parse_args(argv)

    # Check for update hints at startup (reads cache only, no network request).
    # Placed after parse_args to avoid triggering background checks for lightweight commands like cc-stats --version.
    update_hint = _check_update_hint()
    if update_hint:
        print(f"\033[33m💡 {update_hint}\033[0m\n")

    # Trigger version check in background (updates cache for next startup)
    _trigger_background_check()

    # Manually parse time arguments: plain dates for since fill in 00:00:00, for until fill in 23:59:59
    if args.since:
        args.since = _parse_time_arg(args.since)
    if args.until:
        args.until = _parse_time_arg(args.until, as_end_of_day=True)

    if args.export_chat:
        from .exporter import find_and_export
        result = find_and_export(
            args.export_chat,
            include_tools=args.include_tools,
        )
        if result:
            # Save to Desktop
            desktop = Path.home() / "Desktop"
            out_file = desktop / f"chat-{args.export_chat[:12]}.md"
            out_file.write_text(result, encoding="utf-8")
            print(f"Exported to {out_file}")
        else:
            print(f"No matching session found: {args.export_chat}", file=sys.stderr)
        return

    if args.report:
        from .reporter import generate_report
        print(generate_report(args.report))
        return

    if args.notify:
        from .webhook import send_notification
        send_notification(args.notify, args.platform or "auto")
        return

    if args.rate_limit:
        _show_rate_limit(args)
        return

    if args.git is not None:
        _show_git_integration(args)
        return

    if args.compare:
        _compare_projects(args)
        return

    if args.list_projects:
        _list_projects()
        return

    # Determine session files to analyze (Claude JSONL + Codex JSONL + Gemini JSON)
    session_files: list[Path] = []

    if args.path:
        p = Path(args.path)
        if p.is_file() and p.suffix in (".jsonl", ".json"):
            session_files = [p]
        elif p.is_dir():
            session_files = find_sessions(p)
            session_files.extend(find_codex_sessions(p))
        if not session_files:
            # Fuzzy keyword search (Claude + Codex + Gemini)
            session_files = find_sessions_by_keyword(args.path)
            session_files.extend(find_codex_sessions_by_keyword(args.path))
            session_files.extend(find_gemini_sessions_by_keyword(args.path))
        if not session_files:
            print(f"Not found: {args.path}", file=sys.stderr)
            sys.exit(1)
    elif args.all:
        session_files = find_sessions()
        session_files.extend(find_codex_sessions())
        session_files.extend(find_gemini_sessions())
    else:
        # Default: current directory
        session_files = find_sessions(Path.cwd())
        session_files.extend(find_codex_sessions(Path.cwd()))

    # Deduplicate (preserve original order)
    session_files = list(dict.fromkeys(session_files))

    if not session_files:
        print("No session files found. Use --list to see available projects.", file=sys.stderr)
        sys.exit(1)

    # Sort by modification time
    session_files.sort(key=lambda f: f.stat().st_mtime)

    if args.last:
        session_files = session_files[-args.last:]

    # Parse & analyze (filter by time range)
    all_stats = []
    for f in session_files:
        session = _parse_session(f)
        stats = analyze_session(session)

        # --since: skip sessions whose end time is before since
        if args.since and stats.end_time and stats.end_time < args.since:
            continue
        # --until: skip sessions whose start time is after until
        if args.until and stats.start_time and stats.start_time > args.until:
            continue

        all_stats.append(stats)

    if not all_stats:
        print("No sessions in the specified time range.", file=sys.stderr)
        sys.exit(1)

    # Trim tokens by date: sessions spanning the filter range only count tokens for dates within the range
    if args.since or args.until:
        since_date = args.since.astimezone().strftime("%Y-%m-%d") if args.since else None
        until_date = args.until.astimezone().strftime("%Y-%m-%d") if args.until else None
        for s in all_stats:
            _trim_stats_by_date_range(s, since_date, until_date)

    if len(all_stats) == 1:
        result = all_stats[0]
    else:
        result = merge_stats(all_stats)

    # Clamp the displayed time range to the filter range, not the original session start/end times
    if args.since and result.start_time and result.start_time < args.since:
        result.start_time = args.since
    if args.until and result.end_time and result.end_time > args.until:
        result.end_time = args.until

    if args.skills:
        print(format_skill_stats(result, session_count=len(all_stats)))
    else:
        print(format_stats(result, session_count=len(all_stats)))


if __name__ == "__main__":
    main()
