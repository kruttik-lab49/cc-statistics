"""Git integration: attribute Claude Code sessions to git commits by time, compute AI cost per commit"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Claude model pricing (USD per 1M tokens) — kept in sync with analyzer.py
_INPUT_PRICE = 3.0        # $3/1M input tokens (Sonnet baseline)
_OUTPUT_PRICE = 15.0      # $15/1M output tokens
_CACHE_READ_PRICE = 0.30  # $0.30/1M cache read tokens


@dataclass(frozen=True)
class CommitInfo:
    """Basic info for a single git commit"""
    hash: str
    timestamp: datetime
    author: str
    message: str          # first line
    added: int = 0
    removed: int = 0


@dataclass
class CommitCost:
    """AI session cost attributed to a single commit"""
    commit: CommitInfo
    session_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class GitIntegrationResult:
    """Complete result of Git integration analysis"""
    repo_path: str
    commit_costs: list[CommitCost] = field(default_factory=list)
    total_commits: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    sessions_matched: int = 0


def parse_git_log(
    repo_path: str | Path,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[CommitInfo]:
    """Parse commit list (with numstat) via git log

    Args:
        repo_path: path to the git repository
        since: start time (optional)
        until: end time (optional)

    Returns:
        List of CommitInfo sorted in ascending time order
    """
    repo = Path(repo_path)
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        return []

    cmd = [
        "git", "log",
        "--numstat",
        "--format=%x00%H|%aI|%an|%s",
    ]
    if since:
        cmd.append(f"--since={since.strftime('%Y-%m-%dT%H:%M:%S%z')}")
    if until:
        cmd.append(f"--until={until.strftime('%Y-%m-%dT%H:%M:%S%z')}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    commits: list[CommitInfo] = []
    current_hash = ""
    current_ts: datetime | None = None
    current_author = ""
    current_message = ""
    current_added = 0
    current_removed = 0

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # commit header line: \x00HASH|ISO_DATE|AUTHOR|MESSAGE
        if stripped.startswith("\x00"):
            # Save the previous commit first
            if current_hash and current_ts:
                commits.append(CommitInfo(
                    hash=current_hash,
                    timestamp=current_ts,
                    author=current_author,
                    message=current_message,
                    added=current_added,
                    removed=current_removed,
                ))
            # Parse new commit
            parts = stripped[1:].split("|", 3)
            if len(parts) < 4:
                current_hash = ""
                continue
            current_hash = parts[0]
            try:
                current_ts = datetime.fromisoformat(parts[1])
            except ValueError:
                current_hash = ""
                continue
            current_author = parts[2]
            current_message = parts[3]
            current_added = 0
            current_removed = 0
            continue

        # numstat line: added\tremoved\tfile_path
        tab_parts = stripped.split("\t")
        if len(tab_parts) == 3:
            a_str, r_str, _ = tab_parts
            if a_str == "-" or r_str == "-":
                continue  # binary file
            try:
                current_added += int(a_str)
                current_removed += int(r_str)
            except ValueError:
                continue

    # Save the last commit
    if current_hash and current_ts:
        commits.append(CommitInfo(
            hash=current_hash,
            timestamp=current_ts,
            author=current_author,
            message=current_message,
            added=current_added,
            removed=current_removed,
        ))

    # Sort by time ascending
    commits.sort(key=lambda c: c.timestamp)
    return commits


def _estimate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
) -> float:
    """Estimate token cost (USD)"""
    return (
        input_tokens * _INPUT_PRICE / 1_000_000
        + output_tokens * _OUTPUT_PRICE / 1_000_000
        + cache_read_tokens * _CACHE_READ_PRICE / 1_000_000
    )


def attribute_sessions_to_commits(
    commits: list[CommitInfo],
    sessions: list[dict],
) -> list[CommitCost]:
    """Attribute sessions to commits by time and compute token/cost per commit

    Attribution rules:
    - Commit time window = (previous commit time, current commit time]
    - First commit's window = (commit_time - 24h, commit_time]
    - If a session's time range overlaps with the window, tokens are allocated
      proportionally based on the overlap fraction of the session's total duration

    Args:
        commits: List of CommitInfo in ascending time order
        sessions: List of session info dicts, each containing:
            - start_time: datetime
            - end_time: datetime
            - input_tokens: int
            - output_tokens: int
            - cache_read_tokens: int

    Returns:
        List of CommitCost (in the same order as commits)
    """
    if not commits:
        return []

    results: list[CommitCost] = []

    for i, commit in enumerate(commits):
        # Determine the commit window
        if i == 0:
            window_start = commit.timestamp - timedelta(hours=24)
        else:
            window_start = commits[i - 1].timestamp
        window_end = commit.timestamp

        cost = CommitCost(commit=commit)
        matched_sessions: set[int] = set()

        for j, sess in enumerate(sessions):
            s_start = sess["start_time"]
            s_end = sess["end_time"]

            # Skip invalid sessions
            if s_start is None or s_end is None:
                continue

            # Ensure timezone-aware comparison
            if s_start.tzinfo is None:
                s_start = s_start.replace(tzinfo=timezone.utc)
            if s_end.tzinfo is None:
                s_end = s_end.replace(tzinfo=timezone.utc)

            w_start = window_start
            w_end = window_end
            if w_start.tzinfo is None:
                w_start = w_start.replace(tzinfo=timezone.utc)
            if w_end.tzinfo is None:
                w_end = w_end.replace(tzinfo=timezone.utc)

            # Compute overlap
            # Zero-duration session: only check if the point is within the window
            session_duration = (s_end - s_start).total_seconds()
            if session_duration <= 0:
                if w_start <= s_start <= w_end:
                    ratio = 1.0
                else:
                    continue
            else:
                overlap_start = max(s_start, w_start)
                overlap_end = min(s_end, w_end)
                if overlap_start >= overlap_end:
                    continue
                overlap_duration = (overlap_end - overlap_start).total_seconds()
                ratio = overlap_duration / session_duration

            inp = int(sess["input_tokens"] * ratio)
            out = int(sess["output_tokens"] * ratio)
            cache = int(sess["cache_read_tokens"] * ratio)

            cost.input_tokens += inp
            cost.output_tokens += out
            cost.cache_read_tokens += cache
            matched_sessions.add(j)

        cost.session_count = len(matched_sessions)
        cost.total_tokens = cost.input_tokens + cost.output_tokens + cost.cache_read_tokens
        cost.estimated_cost_usd = _estimate_cost(
            cost.input_tokens, cost.output_tokens, cost.cache_read_tokens
        )
        results.append(cost)

    return results


def analyze_git_integration(
    repo_path: str | Path,
    all_stats: list,
    since: datetime | None = None,
    until: datetime | None = None,
) -> GitIntegrationResult:
    """Run a complete Git integration analysis

    Args:
        repo_path: path to the git repository
        all_stats: list of SessionStats (from analyzer.analyze_session)
        since: start time filter
        until: end time filter

    Returns:
        Complete GitIntegrationResult
    """
    repo_path = str(repo_path)

    # 1. Parse git log
    commits = parse_git_log(repo_path, since=since, until=until)
    if not commits:
        return GitIntegrationResult(repo_path=repo_path)

    # 2. Extract session summaries from SessionStats
    sessions: list[dict] = []
    for s in all_stats:
        tu = s.token_usage
        sessions.append({
            "start_time": s.start_time,
            "end_time": s.end_time,
            "input_tokens": tu.input_tokens,
            "output_tokens": tu.output_tokens,
            "cache_read_tokens": tu.cache_read_input_tokens,
        })

    # 3. Attribute sessions to commits
    commit_costs = attribute_sessions_to_commits(commits, sessions)

    # 4. Aggregate
    total_tokens = sum(c.total_tokens for c in commit_costs)
    total_cost = sum(c.estimated_cost_usd for c in commit_costs)
    matched = len({j for c in commit_costs for j in range(len(sessions))
                    if c.session_count > 0})
    # More precise: count unique matched sessions
    matched_set: set[int] = set()
    for i, cc in enumerate(commit_costs):
        if cc.session_count > 0:
            # Look back to see which sessions matched this commit
            for j, sess in enumerate(sessions):
                s_start = sess["start_time"]
                s_end = sess["end_time"]
                if s_start is None or s_end is None:
                    continue
                if s_start.tzinfo is None:
                    s_start = s_start.replace(tzinfo=timezone.utc)
                if s_end.tzinfo is None:
                    s_end = s_end.replace(tzinfo=timezone.utc)
                w_start = (commits[i - 1].timestamp if i > 0
                           else commits[i].timestamp - timedelta(hours=24))
                w_end = commits[i].timestamp
                if w_start.tzinfo is None:
                    w_start = w_start.replace(tzinfo=timezone.utc)
                if w_end.tzinfo is None:
                    w_end = w_end.replace(tzinfo=timezone.utc)
                overlap_start = max(s_start, w_start)
                overlap_end = min(s_end, w_end)
                if overlap_start < overlap_end:
                    matched_set.add(j)

    return GitIntegrationResult(
        repo_path=repo_path,
        commit_costs=commit_costs,
        total_commits=len(commits),
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        sessions_matched=len(matched_set),
    )
