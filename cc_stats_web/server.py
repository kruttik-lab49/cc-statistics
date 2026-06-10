"""HTTP server: static files + JSON API"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cc_stats.analyzer import (
    SessionStats,
    TokenUsage,
    analyze_session,
    compute_cache_stats,
    merge_stats,
)
from cc_stats.parser import (
    find_gemini_sessions,
    find_sessions,
    parse_gemini_json,
    parse_jsonl,
)

_web_dir = os.path.join(os.path.dirname(__file__), "web")

# --- Session stats cache ---
# Key: (file_path_str, mtime_ns) → SessionStats
# Past session files are immutable; only the active session changes.
# This makes cache hits essentially free (dict lookup) for all completed sessions.
_session_cache: dict[tuple[str, int], object] = {}
_session_cache_lock = threading.Lock()


def _get_cached_session_stats(f):
    """Parse and analyze a session file, returning cached result if mtime unchanged."""
    mtime = f.stat().st_mtime_ns
    key = (str(f), mtime)
    with _session_cache_lock:
        if key in _session_cache:
            return _session_cache[key]
    session = _parse_session_file(f)
    stats = analyze_session(session)
    with _session_cache_lock:
        _session_cache[key] = (session, stats)
    return session, stats


# --- Project list cache ---
# Short TTL (10 s) — new projects appear when a new session starts, which is rare.
_projects_cache: dict = {"data": None, "ts": 0.0}
_projects_cache_lock = threading.Lock()
_PROJECTS_CACHE_TTL = 10.0


def _get_projects_cached():
    with _projects_cache_lock:
        if _projects_cache["data"] is not None and (time.monotonic() - _projects_cache["ts"]) < _PROJECTS_CACHE_TTL:
            return _projects_cache["data"]
    data = _get_projects()
    with _projects_cache_lock:
        _projects_cache["data"] = data
        _projects_cache["ts"] = time.monotonic()
    return data


# Model pricing ($/M tokens)
_PRICING = {
    "opus": {"input": 15, "output": 75, "cache_read": 1.5, "cache_create": 18.75},
    "sonnet": {"input": 3, "output": 15, "cache_read": 0.3, "cache_create": 3.75},
    "haiku": {"input": 0.8, "output": 4, "cache_read": 0.08, "cache_create": 1.0},
    "gpt-4o": {"input": 2.5, "output": 10, "cache_read": 1.25, "cache_create": 2.5},
    "o1": {"input": 15, "output": 60, "cache_read": 7.5, "cache_create": 15},
    "o3": {"input": 10, "output": 40, "cache_read": 2.5, "cache_create": 10},
    "gemini-2.5-pro": {"input": 1.25, "output": 10, "cache_read": 0.31, "cache_create": 1.25},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.04, "cache_create": 0.15},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025, "cache_create": 0.10},
}


def _match_pricing(model: str) -> dict:
    lower = model.lower()
    # Gemini models (exact match first)
    for key in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"):
        if key in lower:
            return _PRICING[key]
    if "gemini" in lower:
        return _PRICING["gemini-2.5-flash"]
    for key in ["opus", "haiku", "sonnet", "gpt-4o", "o1", "o3"]:
        if key in lower:
            return _PRICING[key]
    return _PRICING["sonnet"]


def _estimate_cost(tu: TokenUsage, model: str = "") -> float:
    p = _match_pricing(model)
    cost = 0.0
    cost += tu.input_tokens / 1e6 * p["input"]
    cost += tu.output_tokens / 1e6 * p["output"]
    cost += tu.cache_read_input_tokens / 1e6 * p["cache_read"]
    cost += tu.cache_creation_input_tokens / 1e6 * p["cache_create"]
    return cost


def _resolve_project_cwd(proj_dir, jsonl_files) -> str:
    """Return the cwd path from the first JSONL line that has one, else ''."""
    for jf in jsonl_files:
        try:
            with open(jf, encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        obj = json.loads(ln)
                        if obj.get("cwd"):
                            return obj["cwd"]
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        except OSError:
            continue
    return ""


def _canonical_project_root(cwd: str) -> str:
    """Strip /.claude/worktrees/<name> suffix to get the real project root.
    A worktree cwd looks like /path/to/project/.claude/worktrees/branch-name.
    We want /path/to/project so that worktrees group with their parent project."""
    from pathlib import PurePosixPath
    p = PurePosixPath(cwd)
    parts = p.parts
    try:
        idx = parts.index(".claude")
        if idx > 0 and len(parts) > idx + 1 and parts[idx + 1] == "worktrees":
            return str(PurePosixPath(*parts[:idx]))
    except ValueError:
        pass
    return cwd


def _display_name_from_path(path: str) -> str:
    """Return the last path component as the human-readable project name."""
    from pathlib import PurePosixPath
    if not path:
        return ""
    return PurePosixPath(path).name


def _stats_to_dict(stats: SessionStats, session_count: int = 1) -> dict:
    def _td_seconds(td):
        return td.total_seconds()

    def _fmt_duration(td):
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

    def _token_dict(tu):
        return {
            "input_tokens": tu.input_tokens,
            "output_tokens": tu.output_tokens,
            "cache_read": tu.cache_read_input_tokens,
            "cache_creation": tu.cache_creation_input_tokens,
            "total": tu.total,
        }

    sorted_tools = sorted(stats.tool_call_counts.items(), key=lambda x: x[1], reverse=True)
    sorted_langs = sorted(stats.lines_by_lang.items(), key=lambda x: x[1]["added"], reverse=True)

    # Cost per model
    total_cost = 0.0
    model_tokens = []
    for model, usage in sorted(stats.token_by_model.items(), key=lambda x: x[1].total, reverse=True):
        cost = _estimate_cost(usage, model)
        total_cost += cost
        model_tokens.append({
            "model": model,
            **_token_dict(usage),
            "cost": round(cost, 4),
        })

    cache = compute_cache_stats(stats.token_usage, stats.token_by_model)

    return {
        "session_count": session_count,
        "user_message_count": stats.user_message_count,
        "tool_call_total": stats.tool_call_total,
        "tool_calls": [{"name": n, "count": c} for n, c in sorted_tools],
        "total_duration": _td_seconds(stats.total_duration),
        "total_duration_fmt": _fmt_duration(stats.total_duration),
        "ai_duration": _td_seconds(stats.ai_duration),
        "ai_duration_fmt": _fmt_duration(stats.ai_duration),
        "user_duration": _td_seconds(stats.user_duration),
        "user_duration_fmt": _fmt_duration(stats.user_duration),
        "active_duration": _td_seconds(stats.active_duration),
        "active_duration_fmt": _fmt_duration(stats.active_duration),
        "turn_count": stats.turn_count,
        "total_added": stats.total_added,
        "total_removed": stats.total_removed,
        "lines_by_lang": [{"lang": l, **c} for l, c in sorted_langs],
        "git_available": stats.git_available,
        "git_total_added": stats.git_total_added,
        "git_total_removed": stats.git_total_removed,
        "git_commit_count": stats.git_commit_count,
        "token_usage": _token_dict(stats.token_usage),
        "token_by_model": model_tokens,
        "estimated_cost": round(total_cost, 2),
        "cache_stats": {
            "hit_rate": round(cache.hit_rate, 4),
            "grade": cache.grade,
            "grade_label": cache.grade_label,
            "cache_read_tokens": cache.cache_read_tokens,
            "total_input_tokens": cache.total_input_tokens,
            "savings_usd": round(cache.savings_usd, 4),
            "by_model": cache.by_model,
        },
    }


def _get_projects():
    from pathlib import Path
    projects = []

    # Claude projects — group worktrees with their parent project by canonical root
    claude_projects = Path.home() / ".claude" / "projects"
    if claude_projects.exists():
        # canonical_root → {dir_names, display_name, cwd, session_count}
        root_to_group: dict[str, dict] = {}
        for proj in sorted(claude_projects.iterdir()):
            if not proj.is_dir():
                continue
            jsonl_files = [f for f in proj.glob("*.jsonl") if not f.name.startswith("agent-")]
            if not jsonl_files:
                continue
            cwd = _resolve_project_cwd(proj, jsonl_files)
            root = _canonical_project_root(cwd) if cwd else ""
            name = _display_name_from_path(root) if root else proj.name
            key = root or proj.name
            if key in root_to_group:
                root_to_group[key]["dir_names"].append(proj.name)
                root_to_group[key]["session_count"] += len(jsonl_files)
            else:
                root_to_group[key] = {
                    "dir_names": [proj.name],
                    "display_name": name,
                    "cwd": root,
                    "session_count": len(jsonl_files),
                }
        for key, g in root_to_group.items():
            projects.append({
                # dir_name is the primary folder; dir_names covers all grouped folders
                "dir_name": g["dir_names"][0],
                "dir_names": g["dir_names"],
                "display_name": g["display_name"],
                "cwd": g["cwd"],
                "session_count": g["session_count"],
                "source": "claude",
            })

    # Gemini projects
    gemini_files = find_gemini_sessions()
    if gemini_files:
        gemini_by_dir: dict[str, list] = {}
        for gf in gemini_files:
            dir_key = gf.parent.parent.name  # project hash
            gemini_by_dir.setdefault(dir_key, []).append(gf)
        for dir_key, files in gemini_by_dir.items():
            # Try to get project path from first session
            display_name = dir_key
            try:
                session = parse_gemini_json(files[0])
                if session.project_path:
                    display_name = session.project_path
            except Exception:
                pass
            projects.append({
                "dir_name": f"gemini:{dir_key}",
                "display_name": display_name,
                "session_count": len(files),
                "source": "gemini",
            })

    projects.sort(key=lambda x: x["session_count"], reverse=True)
    return projects


def _collect_session_files(project_dir_name=None):
    """Collect session files (Claude JSONL + Gemini JSON)"""
    from pathlib import Path
    files = []

    if project_dir_name and project_dir_name.startswith("gemini:"):
        # Gemini project
        dir_key = project_dir_name[7:]
        for gf in find_gemini_sessions():
            if gf.parent.parent.name == dir_key:
                files.append(gf)
    elif project_dir_name:
        # Claude project — guard against path traversal in query parameter
        if ".." in project_dir_name or "/" in project_dir_name or "\\" in project_dir_name:
            return []
        claude_projects = Path.home() / ".claude" / "projects"
        proj_dir = claude_projects / project_dir_name
        files = sorted(f for f in proj_dir.glob("*.jsonl") if not f.name.startswith("agent-"))
    else:
        # All sources
        files = [f for f in find_sessions() if not f.name.startswith("agent-")]
        files.extend(find_gemini_sessions())

    return files


def _parse_session_file(f):
    """Parse a session file based on its extension"""
    if f.suffix == ".json":
        return parse_gemini_json(f)
    return parse_jsonl(f)


def _fmt_duration_td(td) -> str:
    """Format a timedelta as a human-readable duration string."""
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


def _get_stats(project_dir_name=None, since_days=None, project_dir_names=None):
    """Get aggregated stats. Supports single project_dir_name or a list via project_dir_names."""
    names = project_dir_names or ([project_dir_name] if project_dir_name else [None])

    since_dt = None
    if since_days:
        since_dt = datetime.now(tz=timezone.utc) - timedelta(days=since_days)

    all_known = _get_projects_cached()
    # dir_name → project metadata (one entry per physical folder, including worktrees)
    dir_meta: dict[str, dict] = {}
    for p in all_known:
        for dn in p.get("dir_names", [p["dir_name"]]):
            dir_meta[dn] = p  # all dir_names in a group point to same metadata

    per_project_stats: dict[str, list] = defaultdict(list)

    all_files = []
    # Map file path → canonical project key (= cwd root path, or dir_name for Gemini).
    # Grouping by canonical root collapses worktree folders with their parent project.
    file_to_project: dict[str, str] = {}
    # Also keep file → metadata for session_list display name / tooltip
    file_to_meta: dict[str, dict] = {}

    for name in names:
        project_files = _collect_session_files(name)
        for f in project_files:
            fkey = str(f)
            if fkey in file_to_project:
                continue
            if name:
                meta = dir_meta.get(name, {})
                canonical = meta.get("cwd") or name
            else:
                # No filter — derive from path
                if f.suffix == ".json":
                    # Gemini: use dir_name key
                    canonical = f.parent.parent.name
                    meta = dir_meta.get(canonical, {})
                else:
                    dir_name = f.parent.name
                    meta = dir_meta.get(dir_name, {})
                    canonical = meta.get("cwd") or dir_name
            file_to_project[fkey] = canonical
            file_to_meta[fkey] = meta
        all_files.extend(project_files)

    # Deduplicate files by path
    seen: set[str] = set()
    files = []
    for f in all_files:
        fkey = str(f)
        if fkey not in seen:
            seen.add(fkey)
            files.append(f)

    if not files:
        return {"error": "No sessions found"}

    files.sort(key=lambda f: f.stat().st_mtime)

    all_stats = []   # active sessions only (tokens > 0) — used for aggregation
    session_list = []
    for f in files:
        try:
            session, stats = _get_cached_session_stats(f)
            if since_dt and stats.end_time and stats.end_time < since_dt:
                continue
            # Skip sessions with no timestamp or no tokens — treat as inactive
            if not stats.start_time or stats.token_usage.total == 0:
                continue
            all_stats.append(stats)

            proj_key = file_to_project.get(str(f), "")
            per_project_stats[proj_key].append(stats)

            meta = file_to_meta.get(str(f), {})
            display = meta.get("display_name") or _display_name_from_path(proj_key) or proj_key
            cost = sum(_estimate_cost(u, m) for m, u in stats.token_by_model.items())
            session_list.append({
                "date": stats.start_time.astimezone().strftime("%Y-%m-%d %H:%M"),
                "project": display,
                "project_path": meta.get("cwd") or proj_key,
                "duration_fmt": _fmt_duration_td(stats.active_duration),
                "estimated_cost": round(cost, 4),
                "total_tokens": stats.token_usage.total,
                "lines_added": stats.total_added,
                "lines_removed": stats.total_removed,
            })
        except Exception:
            continue

    if not all_stats:
        return {"error": "No valid sessions"}

    result = all_stats[0] if len(all_stats) == 1 else merge_stats(all_stats)
    d = _stats_to_dict(result, session_count=len(all_stats))

    session_list.sort(key=lambda x: x["estimated_cost"], reverse=True)
    d["session_list"] = session_list

    # Build project breakdown — one row per canonical root
    breakdown = []
    for proj_key, proj_stats in per_project_stats.items():
        if not proj_stats:
            continue
        merged = proj_stats[0] if len(proj_stats) == 1 else merge_stats(proj_stats)
        proj_cost = sum(_estimate_cost(u, m) for m, u in merged.token_by_model.items())
        display = _display_name_from_path(proj_key) or proj_key
        breakdown.append({
            "dir_name": proj_key,
            "display_name": display,
            "sessions": len(proj_stats),
            "active_duration_fmt": _fmt_duration_td(merged.active_duration),
            "active_duration": merged.active_duration.total_seconds(),
            "estimated_cost": round(proj_cost, 4),
            "total_tokens": merged.token_usage.total,
            "lines_added": merged.total_added,
            "lines_removed": merged.total_removed,
        })
    breakdown.sort(key=lambda x: x["estimated_cost"], reverse=True)
    d["project_breakdown"] = breakdown

    return d


def _get_daily_stats(project_dir_name=None, days=14, project_dir_names=None):
    names = project_dir_names or ([project_dir_name] if project_dir_name else [None])
    all_files = []
    for name in names:
        all_files.extend(_collect_session_files(name))
    seen = set()
    files = []
    for f in all_files:
        key = str(f)
        if key not in seen:
            seen.add(key)
            files.append(f)

    since_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
    daily: dict[str, list] = defaultdict(list)

    for f in files:
        try:
            _, stats = _get_cached_session_stats(f)
            if stats.end_time and stats.end_time < since_dt:
                continue
            if not stats.start_time:
                continue
            day_key = stats.start_time.astimezone().strftime("%Y-%m-%d")
            daily[day_key].append(stats)
        except Exception:
            continue

    result = []
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        day_key = d.strftime("%Y-%m-%d")
        day_stats = daily.get(day_key, [])
        if day_stats:
            merged = merge_stats(day_stats) if len(day_stats) > 1 else day_stats[0]
            cost = sum(_estimate_cost(u, m) for m, u in merged.token_by_model.items())
            result.append({
                "date": day_key,
                "sessions": len(day_stats),
                "messages": merged.user_message_count,
                "tool_calls": merged.tool_call_total,
                "active_minutes": round(merged.active_duration.total_seconds() / 60, 1),
                "lines_added": merged.total_added,
                "lines_removed": merged.total_removed,
                "tokens": merged.token_usage.total,
                "cost": round(cost, 2),
            })
        else:
            result.append({
                "date": day_key, "sessions": 0, "messages": 0, "tool_calls": 0,
                "active_minutes": 0, "lines_added": 0, "lines_removed": 0, "tokens": 0, "cost": 0,
            })
    return result


def _get_skill_stats(project_dir_name=None, since_days=None, project_dir_names=None):
    """Return skill usage statistics as a list sorted by call_count.

    Skill stats always cover ALL sessions (ignoring since_days) because
    skill usage patterns are more meaningful at the all-time level.
    """
    names = project_dir_names or ([project_dir_name] if project_dir_name else [None])
    all_files = []
    for name in names:
        all_files.extend(_collect_session_files(name))
    seen = set()
    files = []
    for f in all_files:
        key = str(f)
        if key not in seen:
            seen.add(key)
            files.append(f)
    if not files:
        return []

    files.sort(key=lambda f: f.stat().st_mtime)

    all_stats = []
    for f in files:
        try:
            _, stats = _get_cached_session_stats(f)
            all_stats.append(stats)
        except Exception:
            continue

    if not all_stats:
        return []

    result = all_stats[0] if len(all_stats) == 1 else merge_stats(all_stats)

    skills = []
    for name, su in sorted(
        result.skill_stats.items(), key=lambda x: x[1].call_count, reverse=True
    ):
        resolved = su.success_count + su.error_count
        success_rate = (
            round(su.success_count / resolved * 100) if resolved > 0 else None
        )
        skills.append({
            "name": name,
            "call_count": su.call_count,
            "success_count": su.success_count,
            "error_count": su.error_count,
            "unknown_count": su.unknown_count,
            "success_rate": success_rate,
        })
    return skills


def _get_version_update():
    """Check for version updates (used by the Web API)"""
    try:
        from cc_stats.version_checker import check_for_update
        result = check_for_update()
        if result is not None:
            return {
                "has_update": True,
                "current_version": result.current_version,
                "latest_version": result.latest_version,
                "upgrade_command": result.upgrade_command,
            }
    except Exception:
        pass
    return {"has_update": False}


class ApiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=_web_dir, **kwargs)

    @staticmethod
    def _parse_projects(params) -> tuple[list[str] | None, str | None]:
        """Parse ?project= param(s).

        Supports:
          - single:   ?project=foo
          - repeated: ?project=foo&project=bar
          - comma-separated: ?project=foo,bar

        Returns (project_dir_names, single_project_dir_name):
          - If no project param → (None, None)  → all projects
          - If 1 project       → (None, "foo")  → single project path
          - If 2+              → (["foo","bar"], None)
        """
        raw = params.get("project", [])
        names: list[str] = []
        for val in raw:
            for part in val.split(","):
                part = part.strip()
                if part:
                    names.append(part)
        if not names:
            return None, None
        if len(names) == 1:
            return None, names[0]
        return names, None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/projects":
            self._json(_get_projects_cached())
        elif path == "/api/stats":
            project_dir_names, project = self._parse_projects(params)
            days = params.get("days", [None])[0]
            self._json(_get_stats(
                project_dir_name=project,
                since_days=int(days) if days and days != "0" else None,
                project_dir_names=project_dir_names,
            ))
        elif path == "/api/daily_stats":
            project_dir_names, project = self._parse_projects(params)
            days = params.get("days", ["14"])[0]
            self._json(_get_daily_stats(
                project_dir_name=project,
                days=int(days),
                project_dir_names=project_dir_names,
            ))
        elif path == "/api/skills":
            project_dir_names, project = self._parse_projects(params)
            days = params.get("days", [None])[0]
            self._json(_get_skill_stats(
                project_dir_name=project,
                since_days=int(days) if days and days != "0" else None,
                project_dir_names=project_dir_names,
            ))
        elif path == "/api/version_check":
            self._json(_get_version_update())
        else:
            super().do_GET()

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server() -> tuple[ThreadingHTTPServer, int]:
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), ApiHandler)
    return server, port
