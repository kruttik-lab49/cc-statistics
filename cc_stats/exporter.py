"""Export sessions to Markdown format for easy sharing"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .parser import (
    Message,
    Session,
    find_codex_sessions,
    find_gemini_sessions,
    find_sessions,
    parse_session_file,
)


def _extract_text(content) -> str:
    """Extract plain text from content"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    name = block.get("name", "")
                    parts.append(f"[Tool: {name}]")
                elif block.get("type") == "tool_result":
                    # Skip tool results
                    continue
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _fmt_ts(ts_str: str) -> str:
    """Format timestamp"""
    if not ts_str:
        return ""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ""


def export_session(session: Session, include_tools: bool = False) -> str:
    """Export session to Markdown"""
    lines: list[str] = []

    # Title
    start_ts = ""
    for msg in session.messages:
        if msg.timestamp:
            try:
                dt = datetime.fromisoformat(msg.timestamp.replace("Z", "+00:00"))
                start_ts = dt.astimezone().strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
            break

    lines.append(f"# Claude Code Conversation")
    lines.append(f"")
    if start_ts:
        lines.append(f"**Time:** {start_ts}")
    if session.project_path:
        project_name = Path(session.project_path).name
        lines.append(f"**Project:** {project_name}")
    lines.append(f"**Session ID:** `{session.session_id[:12]}...`")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    for msg in session.messages:
        # Skip tool results and meta messages
        if msg.is_tool_result or msg.is_meta:
            continue

        text = _extract_text(msg.content).strip()
        if not text:
            continue

        # Skip pure tool calls (assistant messages with no text output)
        if msg.role == "assistant" and text.startswith("[Tool:") and "\n" not in text:
            if not include_tools:
                continue

        time_str = _fmt_ts(msg.timestamp)
        time_suffix = f" `{time_str}`" if time_str else ""

        if msg.role == "user":
            lines.append(f"### You{time_suffix}")
            lines.append(f"")
            lines.append(text)
            lines.append(f"")
        elif msg.role == "assistant":
            model = msg.model or ""
            model_suffix = f" ({model})" if model else ""
            lines.append(f"### Claude{model_suffix}{time_suffix}")
            lines.append(f"")
            lines.append(text)
            lines.append(f"")

    lines.append(f"---")
    lines.append(f"*Exported by [cc-statistics](https://github.com/androidZzT/cc-statistics)*")

    return "\n".join(lines)


def find_and_export(keyword: str, output: str | None = None,
                    include_tools: bool = False) -> str | None:
    """Find a session and export it

    Args:
        keyword: Session ID prefix or search keyword
        output: Output file path (None to output to stdout)
        include_tools: Whether to include tool calls
    """
    # Search all sessions (Claude + Codex + Gemini)
    all_files: list[Path] = list(find_sessions())
    all_files.extend(find_codex_sessions())
    all_files.extend(find_gemini_sessions())

    # First match by session ID prefix
    matched = None
    for f in all_files:
        if f.stem.startswith(keyword):
            matched = f
            break

    # Then search by content
    if not matched:
        for f in sorted(all_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                session = parse_session_file(f)
                for msg in session.messages:
                    text = _extract_text(msg.content)
                    if keyword.lower() in text.lower():
                        matched = f
                        break
                if matched:
                    break
            except Exception:
                continue

    if not matched:
        return None

    session = parse_session_file(matched)
    md = export_session(session, include_tools=include_tools)

    if output:
        Path(output).write_text(md, encoding="utf-8")
        return output
    return md
