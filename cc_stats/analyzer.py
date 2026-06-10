"""Analyze session data and compute engineering metrics"""

from __future__ import annotations

import os
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .parser import Message, Session, ToolCall
from .pricing import is_claude_model, match_model_pricing

# File extension → language mapping
EXT_TO_LANG: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (JSX)",
    ".jsx": "JavaScript (JSX)",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin Script",
    ".swift": "Swift",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ Header",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".less": "Less",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".sql": "SQL",
    ".md": "Markdown",
    ".r": "R",
    ".lua": "Lua",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".gradle": "Gradle",
}

# Tool descriptions
TOOL_DESCRIPTIONS: dict[str, str] = {
    "Bash": "Execute shell command",
    "Read": "Read file contents",
    "Write": "Create/overwrite file",
    "Edit": "Edit file (precise replacement)",
    "Glob": "Search files by pattern",
    "Grep": "Search files by content",
    "Agent": "Launch subagent for subtask",
    "Skill": "Invoke skill/slash command",
    "WebFetch": "Fetch web page content",
    "WebSearch": "Search the internet",
    "NotebookEdit": "Edit Jupyter notebook",
    "LSP": "Call language server",
    "TodoWrite": "Write todo items",
    "AskUserQuestion": "Ask user a question",
    "TaskCreate": "Create task",
    "TaskUpdate": "Update task status",
    "TaskGet": "Get task info",
    "TaskList": "List tasks",
    "TaskOutput": "Get task output",
    "TaskStop": "Stop task",
    "ToolSearch": "Search available tools",
    "SendMessage": "Send message to subagent",
}

# Active time threshold: gaps larger than this between messages are considered "idle"
IDLE_THRESHOLD = timedelta(minutes=5)


def _parse_ts(ts: str) -> datetime | None:
    """Parse ISO format or millisecond timestamp"""
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)) or ts.isdigit():
            return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        # ISO format
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, OSError):
        return None


def _get_local_date(ts: str) -> str | None:
    """Extract local date string (YYYY-MM-DD) from message timestamp"""
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return dt.astimezone().strftime("%Y-%m-%d")


def _get_local_minute(ts: str) -> str | None:
    """Extract local minute string (YYYY-MM-DD HH:MM) from message timestamp"""
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _detect_lang(file_path: str) -> str:
    """Detect programming language from file extension"""
    _, ext = os.path.splitext(file_path)
    return EXT_TO_LANG.get(ext.lower(), f"Other ({ext})" if ext else "Unknown")


def _count_lines(text: str) -> int:
    """Count lines in text (excluding trailing blank lines)"""
    if not text:
        return 0
    return len(text.rstrip("\n").split("\n"))


def _to_int(value: object) -> int:
    """Best-effort integer conversion for defensive JSONL parsing."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


@dataclass
class CodeChange:
    file_path: str
    language: str
    added: int = 0
    removed: int = 0


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


@dataclass
class SkillUsage:
    """Usage statistics for a single Skill"""
    name: str
    call_count: int = 0
    success_count: int = 0
    error_count: int = 0
    unknown_count: int = 0  # calls whose outcome could not be determined
    hourly_dist: dict[int, int] = field(default_factory=dict)  # hour(0-23) -> count
    daily_dist: dict[str, int] = field(default_factory=dict)   # "YYYY-MM-DD" -> count


@dataclass
class SessionStats:
    """Statistics results for a single session"""
    session_id: str
    project_path: str

    # 1. User instruction count
    user_message_count: int = 0

    # 2. Tool calls
    tool_call_total: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)

    # 3. Development duration
    start_time: datetime | None = None
    end_time: datetime | None = None
    total_duration: timedelta = field(default_factory=timedelta)
    ai_duration: timedelta = field(default_factory=timedelta)       # AI processing time
    user_duration: timedelta = field(default_factory=timedelta)     # user active time (review/coding)
    active_duration: timedelta = field(default_factory=timedelta)   # ai + user
    turn_count: int = 0                                             # number of conversation turns

    # 4. Lines of code (AI — from Edit/Write tool calls)
    code_changes: list[CodeChange] = field(default_factory=list)
    lines_by_lang: dict[str, dict[str, int]] = field(default_factory=dict)
    total_added: int = 0
    total_removed: int = 0

    # 4b. Lines of code (Git — all commits during the session)
    git_total_added: int = 0
    git_total_removed: int = 0
    git_lines_by_lang: dict[str, dict[str, int]] = field(default_factory=dict)
    git_commit_count: int = 0
    git_ai_commit_count: int = 0       # number of commits with Co-Authored-By: Claude
    git_ai_added: int = 0              # lines added in AI commits
    git_ai_removed: int = 0            # lines removed in AI commits
    git_available: bool = False

    # 5. Token usage
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    token_by_model: dict[str, TokenUsage] = field(default_factory=dict)

    # 6. Skill usage statistics
    skill_stats: dict[str, SkillUsage] = field(default_factory=dict)

    # 7. Tokens allocated by date (cross-day sessions bucketed by message timestamp)
    # key: "YYYY-MM-DD" local date, value: TokenUsage for that day
    token_by_date: dict[str, TokenUsage] = field(default_factory=dict)

    # 7b. Tokens allocated by date + model, used for model breakdown and cost estimation after date filtering
    # key: "YYYY-MM-DD" -> model -> TokenUsage
    token_by_model_by_date: dict[str, dict[str, TokenUsage]] = field(default_factory=dict)

    # 8. Tokens allocated by minute (used for usage quota forecasting)
    # key: "YYYY-MM-DD HH:MM" local time, value: TokenUsage for that minute
    # only the most recent 30 minutes are retained to control memory usage
    token_by_minute: dict[str, TokenUsage] = field(default_factory=dict)

    # 9. Coding rhythm analysis
    # key: "morning"|"afternoon"|"evening"|"night"
    # value: {"session_count": int, "token_count": int, "active_minutes": float}
    coding_rhythm: dict[str, dict[str, int | float]] = field(default_factory=dict)

    # 10. Work mode distribution
    # key: "Exploration"|"Building"|"Execution", value: session count
    work_mode_distribution: dict[str, int] = field(default_factory=dict)


@dataclass
class CacheStats:
    """Cache hit rate analysis results"""
    hit_rate: float = 0.0           # 0.0 - 1.0
    grade: str = "na"               # "excellent" | "good" | "fair" | "poor" | "na"
    grade_label: str = "N/A"        # display label
    cache_read_tokens: int = 0
    total_input_tokens: int = 0     # input + cache_read (denominator)
    savings_usd: float = 0.0        # estimated savings
    by_model: dict[str, float] = field(default_factory=dict)  # model -> hit_rate


def _cache_grade(hit_rate: float) -> tuple[str, str]:
    """Return (grade, grade_label) based on hit rate"""
    if hit_rate >= 0.80:
        return "excellent", "Excellent"
    if hit_rate >= 0.60:
        return "good", "Good"
    if hit_rate >= 0.40:
        return "fair", "Fair"
    return "poor", "Poor"


def compute_cache_stats(
    token_usage: TokenUsage,
    token_by_model: dict[str, TokenUsage],
) -> CacheStats:
    """Compute cache hit rate statistics from TokenUsage"""
    cache_read = token_usage.cache_read_input_tokens
    inp = token_usage.input_tokens
    total_input = inp + cache_read

    # No cache data → N/A
    if cache_read == 0:
        return CacheStats()

    hit_rate = cache_read / total_input if total_input > 0 else 0.0
    grade, grade_label = _cache_grade(hit_rate)

    # Savings estimate: only calculated for Claude models (based on actual model price difference)
    # savings = cache_read_tokens * (input_price - cache_read_price) / 1M
    savings_usd = 0.0
    for model, usage in token_by_model.items():
        if not is_claude_model(model):
            continue
        pricing = match_model_pricing(model)
        savings_per_million = pricing["input"] - pricing["cache_read"]
        savings_usd += usage.cache_read_input_tokens * savings_per_million / 1_000_000

    # Hit rate by model
    by_model: dict[str, float] = {}
    for model, usage in token_by_model.items():
        m_total = usage.input_tokens + usage.cache_read_input_tokens
        if m_total > 0 and usage.cache_read_input_tokens > 0:
            by_model[model] = usage.cache_read_input_tokens / m_total

    return CacheStats(
        hit_rate=hit_rate,
        grade=grade,
        grade_label=grade_label,
        cache_read_tokens=cache_read,
        total_input_tokens=total_input,
        savings_usd=savings_usd,
        by_model=by_model,
    )


@dataclass
class GitStats:
    total_added: int = 0
    total_removed: int = 0
    commit_count: int = 0
    ai_commit_count: int = 0
    ai_added: int = 0
    ai_removed: int = 0
    lines_by_lang: dict[str, dict[str, int]] = field(default_factory=dict)


# AI commit detection keywords (presence in commit message indicates AI involvement)
_AI_COMMIT_MARKERS = [
    "co-authored-by: claude",
    "co-authored-by: cursor",
    "co-authored-by: github copilot",
    "co-authored-by: codex",
    "co-authored-by: gemini",
    "generated by ai",
    "generated with claude",
    "generated by claude",
]


def _collect_git_stats(
    project_path: str,
    start_time: datetime,
    end_time: datetime,
) -> GitStats:
    """Collect commit change statistics during session time window via git log, distinguishing AI/human commits"""
    repo_dir = Path(project_path)
    if not (repo_dir / ".git").exists() and not (repo_dir / ".git").is_file():
        return GitStats()

    # Convert to local time, extending by 1 minute on each side to avoid boundary issues
    local_start = (start_time - timedelta(minutes=1)).astimezone()
    local_end = (end_time + timedelta(minutes=1)).astimezone()
    since = local_start.strftime("%Y-%m-%dT%H:%M:%S")
    until = local_end.strftime("%Y-%m-%dT%H:%M:%S")

    # Use --format to separate hash and commit body (using %x00 as delimiter)
    try:
        result = subprocess.run(
            [
                "git", "log",
                "--numstat",
                "--format=%x00%H%n%B%x00",
                f"--since={since}",
                f"--until={until}",
            ],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return GitStats()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return GitStats()

    stats = GitStats()
    lang_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"added": 0, "removed": 0}
    )

    # Parse commits segment by segment
    is_ai_commit = False
    commit_added = 0
    commit_removed = 0

    for line in result.stdout.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Detect commit delimiter (\x00HASH\n...body...\x00)
        if "\x00" in line:
            # Settle stats for the previous commit first
            if stats.commit_count > 0 and is_ai_commit:
                stats.ai_added += commit_added
                stats.ai_removed += commit_removed

            # Extract commit body and determine if it is an AI commit
            clean = line.replace("\x00", "")
            if len(clean) >= 40:
                stats.commit_count += 1
                commit_added = 0
                commit_removed = 0
                is_ai_commit = False
            # Check for AI markers in the body
            lower = line.lower()
            if any(marker in lower for marker in _AI_COMMIT_MARKERS):
                is_ai_commit = True
                stats.ai_commit_count += 1
            continue

        # Check for AI markers in non-delimiter lines (commit body may span multiple lines)
        lower = line_stripped.lower()
        if any(marker in lower for marker in _AI_COMMIT_MARKERS):
            if not is_ai_commit:
                is_ai_commit = True
                stats.ai_commit_count += 1

        # numstat line: added\tremoved\tfile_path
        parts = line_stripped.split("\t")
        if len(parts) == 3:
            added_str, removed_str, file_path = parts
            if added_str == "-" or removed_str == "-":
                continue
            try:
                added = int(added_str)
                removed = int(removed_str)
            except ValueError:
                continue
            stats.total_added += added
            stats.total_removed += removed
            commit_added += added
            commit_removed += removed
            lang = _detect_lang(file_path)
            lang_stats[lang]["added"] += added
            lang_stats[lang]["removed"] += removed

    # Settle the last commit
    if is_ai_commit:
        stats.ai_added += commit_added
        stats.ai_removed += commit_removed

    stats.lines_by_lang = dict(lang_stats)
    return stats


def _time_period(hour: int) -> str:
    """Map hour to period name"""
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 24:
        return "evening"
    return "night"


def classify_work_mode(user_message_count: int, total_added: int, total_removed: int) -> str:
    """Classify work mode based on session characteristics"""
    code_per_msg = (total_added + total_removed) / max(user_message_count, 1)
    if code_per_msg > 50:
        return "Execution"
    if code_per_msg < 5:
        return "Exploration"
    return "Building"


def analyze_session(session: Session) -> SessionStats:
    """Analyze a single session and return statistics"""
    stats = SessionStats(
        session_id=session.session_id,
        project_path=session.project_path,
    )

    # Build tool_use_id → is_error mapping (used for Skill success rate statistics)
    tool_result_errors: dict[str, bool] = {}
    for msg in session.messages:
        if msg.role == "user" and msg.tool_results:
            tool_result_errors.update(msg.tool_results)

    # Build time-stamped message sequence for duration analysis
    # timed_msgs: list of (datetime, role)
    # role: "user_real" = real user message, "user_tool" = tool result, "assistant"
    timed_msgs: list[tuple[datetime, str]] = []

    for msg in session.messages:
        ts = _parse_ts(msg.timestamp)
        if not ts:
            continue

        if msg.role == "user":
            if msg.is_tool_result or msg.is_meta:
                timed_msgs.append((ts, "user_tool"))
            else:
                timed_msgs.append((ts, "user_real"))
        elif msg.role == "assistant":
            timed_msgs.append((ts, "assistant"))

        # -------- 1. User instruction count --------
        if msg.role == "user" and not msg.is_tool_result and not msg.is_meta:
            stats.user_message_count += 1

        # -------- 2. Tool calls --------
        if msg.role == "assistant":
            for tc in msg.tool_calls:
                stats.tool_call_total += 1

                # Expand Skill and MCP tool calls to specific names
                display_name = tc.name
                if tc.name == "Skill":
                    skill_name = tc.input.get("skill", "")
                    if skill_name:
                        display_name = f"Skill:{skill_name}"
                elif tc.name.startswith("mcp__"):
                    # mcp__server__method → MCP:server/method
                    parts = tc.name.split("__")
                    if len(parts) >= 3:
                        display_name = f"MCP:{parts[1]}/{parts[2]}"

                stats.tool_call_counts[display_name] = (
                    stats.tool_call_counts.get(display_name, 0) + 1
                )

                # -------- 6. Skill usage statistics --------
                if tc.name == "Skill":
                    skill_name = tc.input.get("skill", "") or "unknown"
                    if skill_name not in stats.skill_stats:
                        stats.skill_stats[skill_name] = SkillUsage(name=skill_name)
                    su = stats.skill_stats[skill_name]
                    su.call_count += 1

                    # Success/failure determination
                    if tc.tool_use_id and tc.tool_use_id in tool_result_errors:
                        if tool_result_errors[tc.tool_use_id]:
                            su.error_count += 1
                        else:
                            su.success_count += 1
                    else:
                        su.unknown_count += 1

                    # Time distribution
                    call_ts = _parse_ts(tc.timestamp)
                    if call_ts:
                        local_ts = call_ts.astimezone()
                        hour = local_ts.hour
                        day = local_ts.strftime("%Y-%m-%d")
                        su.hourly_dist[hour] = su.hourly_dist.get(hour, 0) + 1
                        su.daily_dist[day] = su.daily_dist.get(day, 0) + 1

                # -------- 4. Lines of code (extracted from Edit/Write tools) --------
                if tc.name == "Write":
                    # Claude: file_path/content; Gemini: file_path/content
                    fp = tc.input.get("file_path", "")
                    content = tc.input.get("content", "")
                    lang = _detect_lang(fp)
                    added = _count_lines(content)
                    change = CodeChange(
                        file_path=fp, language=lang, added=added, removed=0
                    )
                    stats.code_changes.append(change)

                elif tc.name == "Edit":
                    # Claude: file_path/old_string/new_string
                    # Gemini: target_file/code_edit (no old/new split)
                    fp = (
                        tc.input.get("file_path", "")
                        or tc.input.get("target_file", "")
                    )
                    old = tc.input.get("old_string", "")
                    new = tc.input.get("new_string", "")
                    if not old and not new:
                        # Gemini edit_file: only code_edit, estimate as additions
                        code_edit = tc.input.get("code_edit", "")
                        new = code_edit
                    lang = _detect_lang(fp)
                    old_lines = _count_lines(old)
                    new_lines = _count_lines(new)
                    change = CodeChange(
                        file_path=fp,
                        language=lang,
                        added=new_lines,
                        removed=old_lines,
                    )
                    stats.code_changes.append(change)

            # -------- 5. Token usage --------
            usage = msg.usage
            if usage:
                inp = _to_int(usage.get("input_tokens", 0))
                out = _to_int(usage.get("output_tokens", 0))
                cache_read = _to_int(usage.get("cache_read_input_tokens", 0))
                cache_create = _to_int(usage.get("cache_creation_input_tokens", 0))

                stats.token_usage.input_tokens += inp
                stats.token_usage.output_tokens += out
                stats.token_usage.cache_read_input_tokens += cache_read
                stats.token_usage.cache_creation_input_tokens += cache_create

                model = msg.model or ""
                if not model or model.startswith("<"):
                    model = "unknown"
                if model not in stats.token_by_model:
                    stats.token_by_model[model] = TokenUsage()
                m = stats.token_by_model[model]
                m.input_tokens += inp
                m.output_tokens += out
                m.cache_read_input_tokens += cache_read
                m.cache_creation_input_tokens += cache_create

                # -------- 7. Bucket tokens by message timestamp date --------
                local_date = _get_local_date(msg.timestamp)
                if local_date:
                    if local_date not in stats.token_by_date:
                        stats.token_by_date[local_date] = TokenUsage()
                    d = stats.token_by_date[local_date]
                    d.input_tokens += inp
                    d.output_tokens += out
                    d.cache_read_input_tokens += cache_read
                    d.cache_creation_input_tokens += cache_create

                    if local_date not in stats.token_by_model_by_date:
                        stats.token_by_model_by_date[local_date] = {}
                    by_model_for_day = stats.token_by_model_by_date[local_date]
                    if model not in by_model_for_day:
                        by_model_for_day[model] = TokenUsage()
                    dm = by_model_for_day[model]
                    dm.input_tokens += inp
                    dm.output_tokens += out
                    dm.cache_read_input_tokens += cache_read
                    dm.cache_creation_input_tokens += cache_create

                # -------- 8. Bucket tokens by minute (for usage quota forecasting) --------
                local_minute = _get_local_minute(msg.timestamp)
                if local_minute:
                    if local_minute not in stats.token_by_minute:
                        stats.token_by_minute[local_minute] = TokenUsage()
                    m = stats.token_by_minute[local_minute]
                    m.input_tokens += inp
                    m.output_tokens += out
                    m.cache_read_input_tokens += cache_read
                    m.cache_creation_input_tokens += cache_create

    # Trim token_by_minute to retain only the most recent 30 minutes
    if stats.token_by_minute:
        sorted_keys = sorted(stats.token_by_minute.keys())
        if len(sorted_keys) > 30:
            for k in sorted_keys[:-30]:
                del stats.token_by_minute[k]

    # -------- 3. Duration calculation (based on conversation turns) --------
    # One turn = user sends message → AI processes (may involve multiple tool calls) → AI final reply
    # AI duration = from user message to AI's last response within each turn
    # User duration = from previous turn's last AI response to this turn's user message (exceeding threshold = away)
    # Sort by timestamp to avoid negative values from out-of-order messages in resumed sessions or subagent messages
    timed_msgs.sort(key=lambda x: x[0])
    if timed_msgs:
        stats.start_time = timed_msgs[0][0]
        stats.end_time = timed_msgs[-1][0]

        ai_total = timedelta()
        user_total = timedelta()
        turn_count = 0

        # Split message stream into turns: each user_real message starts a new turn
        # turn_start: timestamp of this turn's user message
        # last_ai_end: timestamp of the previous turn's last AI response
        turn_start: datetime | None = None
        turn_last_ai: datetime | None = None
        last_ai_end: datetime | None = None  # end of previous turn

        for ts, role in timed_msgs:
            if role == "user_real":
                # Settle AI duration for the previous turn
                if turn_start is not None and turn_last_ai is not None:
                    delta = turn_last_ai - turn_start
                    if delta.total_seconds() > 0:
                        ai_total += delta
                    turn_count += 1

                # Calculate user duration (previous turn AI end → this turn user message)
                if last_ai_end is not None:
                    gap = ts - last_ai_end
                    if timedelta() < gap <= IDLE_THRESHOLD:
                        user_total += gap

                # End of previous turn
                if turn_last_ai is not None:
                    last_ai_end = turn_last_ai

                turn_start = ts
                turn_last_ai = None
            elif role in ("assistant", "user_tool"):
                # AI response or tool result, both count as AI working
                turn_last_ai = ts

        # Settle the last turn
        if turn_start is not None and turn_last_ai is not None:
            delta = turn_last_ai - turn_start
            if delta.total_seconds() > 0:
                ai_total += delta
            turn_count += 1

        stats.ai_duration = ai_total
        stats.user_duration = user_total
        stats.active_duration = ai_total + user_total
        # total_duration = active duration (not first-to-last gap), to avoid inflated values from cross-day resumed sessions
        stats.total_duration = ai_total + user_total
        stats.turn_count = turn_count

    # -------- 4. Aggregate by language --------
    lang_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"added": 0, "removed": 0}
    )
    for change in stats.code_changes:
        lang_stats[change.language]["added"] += change.added
        lang_stats[change.language]["removed"] += change.removed
        stats.total_added += change.added
        stats.total_removed += change.removed
    stats.lines_by_lang = dict(lang_stats)

    # -------- 4b. Git change statistics --------
    if stats.start_time and stats.end_time and session.project_path:
        git = _collect_git_stats(
            session.project_path, stats.start_time, stats.end_time
        )
        if git.commit_count > 0:
            stats.git_available = True
            stats.git_total_added = git.total_added
            stats.git_total_removed = git.total_removed
            stats.git_commit_count = git.commit_count
            stats.git_ai_commit_count = git.ai_commit_count
            stats.git_ai_added = git.ai_added
            stats.git_ai_removed = git.ai_removed
            stats.git_lines_by_lang = git.lines_by_lang

    # -------- 9. Coding rhythm analysis --------
    if stats.start_time:
        period = _time_period(stats.start_time.astimezone().hour)
        active_mins = stats.active_duration.total_seconds() / 60.0
        stats.coding_rhythm = {
            period: {
                "session_count": 1,
                "token_count": stats.token_usage.total,
                "active_minutes": round(active_mins, 1),
            }
        }

    # -------- 10. Work mode classification --------
    mode = classify_work_mode(
        stats.user_message_count, stats.total_added, stats.total_removed
    )
    stats.work_mode_distribution = {mode: 1}

    return stats


def merge_stats(all_stats: list[SessionStats]) -> SessionStats:
    """Merge statistics from multiple sessions"""
    merged = SessionStats(session_id="merged", project_path="all")

    all_starts = []
    all_ends = []

    for s in all_stats:
        merged.user_message_count += s.user_message_count
        merged.tool_call_total += s.tool_call_total

        for name, count in s.tool_call_counts.items():
            merged.tool_call_counts[name] = merged.tool_call_counts.get(name, 0) + count

        merged.ai_duration += s.ai_duration
        merged.user_duration += s.user_duration
        merged.active_duration += s.active_duration
        merged.turn_count += s.turn_count

        if s.start_time:
            all_starts.append(s.start_time)
        if s.end_time:
            all_ends.append(s.end_time)

        merged.code_changes.extend(s.code_changes)
        merged.total_added += s.total_added
        merged.total_removed += s.total_removed

        for lang, counts in s.lines_by_lang.items():
            if lang not in merged.lines_by_lang:
                merged.lines_by_lang[lang] = {"added": 0, "removed": 0}
            merged.lines_by_lang[lang]["added"] += counts["added"]
            merged.lines_by_lang[lang]["removed"] += counts["removed"]

        # Git changes
        if s.git_available:
            merged.git_available = True
            merged.git_total_added += s.git_total_added
            merged.git_total_removed += s.git_total_removed
            merged.git_commit_count += s.git_commit_count
            merged.git_ai_commit_count += s.git_ai_commit_count
            merged.git_ai_added += s.git_ai_added
            merged.git_ai_removed += s.git_ai_removed
            for lang, counts in s.git_lines_by_lang.items():
                if lang not in merged.git_lines_by_lang:
                    merged.git_lines_by_lang[lang] = {"added": 0, "removed": 0}
                merged.git_lines_by_lang[lang]["added"] += counts["added"]
                merged.git_lines_by_lang[lang]["removed"] += counts["removed"]

        # Skill usage statistics
        for name, su in s.skill_stats.items():
            if name not in merged.skill_stats:
                merged.skill_stats[name] = SkillUsage(name=name)
            m_su = merged.skill_stats[name]
            m_su.call_count += su.call_count
            m_su.success_count += su.success_count
            m_su.error_count += su.error_count
            m_su.unknown_count += su.unknown_count
            for h, c in su.hourly_dist.items():
                m_su.hourly_dist[h] = m_su.hourly_dist.get(h, 0) + c
            for d, c in su.daily_dist.items():
                m_su.daily_dist[d] = m_su.daily_dist.get(d, 0) + c

        merged.token_usage.input_tokens += s.token_usage.input_tokens
        merged.token_usage.output_tokens += s.token_usage.output_tokens
        merged.token_usage.cache_read_input_tokens += s.token_usage.cache_read_input_tokens
        merged.token_usage.cache_creation_input_tokens += s.token_usage.cache_creation_input_tokens

        # Merge token_by_date
        for date_key, tu in s.token_by_date.items():
            if date_key not in merged.token_by_date:
                merged.token_by_date[date_key] = TokenUsage()
            d = merged.token_by_date[date_key]
            d.input_tokens += tu.input_tokens
            d.output_tokens += tu.output_tokens
            d.cache_read_input_tokens += tu.cache_read_input_tokens
            d.cache_creation_input_tokens += tu.cache_creation_input_tokens

        # Merge token_by_model_by_date
        for date_key, model_map in s.token_by_model_by_date.items():
            if date_key not in merged.token_by_model_by_date:
                merged.token_by_model_by_date[date_key] = {}
            merged_model_map = merged.token_by_model_by_date[date_key]
            for model, usage in model_map.items():
                if model not in merged_model_map:
                    merged_model_map[model] = TokenUsage()
                dm = merged_model_map[model]
                dm.input_tokens += usage.input_tokens
                dm.output_tokens += usage.output_tokens
                dm.cache_read_input_tokens += usage.cache_read_input_tokens
                dm.cache_creation_input_tokens += usage.cache_creation_input_tokens

        for model, usage in s.token_by_model.items():
            if model not in merged.token_by_model:
                merged.token_by_model[model] = TokenUsage()
            m = merged.token_by_model[model]
            m.input_tokens += usage.input_tokens
            m.output_tokens += usage.output_tokens
            m.cache_read_input_tokens += usage.cache_read_input_tokens
            m.cache_creation_input_tokens += usage.cache_creation_input_tokens

        # Merge token_by_minute
        for minute_key, tu in s.token_by_minute.items():
            if minute_key not in merged.token_by_minute:
                merged.token_by_minute[minute_key] = TokenUsage()
            m = merged.token_by_minute[minute_key]
            m.input_tokens += tu.input_tokens
            m.output_tokens += tu.output_tokens
            m.cache_read_input_tokens += tu.cache_read_input_tokens
            m.cache_creation_input_tokens += tu.cache_creation_input_tokens

        # Merge coding rhythm
        for period, data in s.coding_rhythm.items():
            if period not in merged.coding_rhythm:
                merged.coding_rhythm[period] = {
                    "session_count": 0, "token_count": 0, "active_minutes": 0.0,
                }
            mr = merged.coding_rhythm[period]
            mr["session_count"] += data["session_count"]
            mr["token_count"] += data["token_count"]
            mr["active_minutes"] = round(
                float(mr["active_minutes"]) + float(data["active_minutes"]), 1
            )

        # Merge work mode
        for mode, count in s.work_mode_distribution.items():
            merged.work_mode_distribution[mode] = (
                merged.work_mode_distribution.get(mode, 0) + count
            )

    # After merging, trim token_by_minute to retain only the most recent 30 minutes
    if merged.token_by_minute:
        sorted_keys = sorted(merged.token_by_minute.keys())
        if len(sorted_keys) > 30:
            for k in sorted_keys[:-30]:
                del merged.token_by_minute[k]

    if all_starts:
        merged.start_time = min(all_starts)
    if all_ends:
        merged.end_time = max(all_ends)
    # total_duration = sum of active durations, not the first-to-last gap
    merged.total_duration = merged.active_duration

    return merged
