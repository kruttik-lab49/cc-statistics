"""Webhook notifications: push daily stats to Feishu/DingTalk/Slack"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from .analyzer import SessionStats, analyze_session, merge_stats
from .parser import (
    find_codex_sessions,
    find_gemini_sessions,
    find_sessions,
    parse_session_file,
)
from .pricing import estimate_cost_from_token_by_model


def _collect_today_stats() -> SessionStats | None:
    """Collect today's statistics (Claude + Codex + Gemini)

    Uses token_by_date grouped by message timestamp: any session that has
    a message falling on today will be included in the statistics.
    """
    today_key = datetime.now().strftime("%Y-%m-%d")
    all_files: list = list(find_sessions())
    all_files.extend(find_codex_sessions())
    all_files.extend(find_gemini_sessions())
    today_stats = []

    for f in all_files:
        try:
            session = parse_session_file(f)
            stats = analyze_session(session)
            # Group by message timestamp: token_by_date contains today's key
            has_today_tokens = today_key in stats.token_by_date
            # Fallback: use end_time when token_by_date is unavailable
            if not has_today_tokens and not stats.token_by_date:
                today_start = datetime.now(tz=timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                has_today_tokens = (
                    stats.end_time is not None and stats.end_time >= today_start
                )
            if has_today_tokens:
                today_stats.append(stats)
        except Exception:
            continue

    if not today_stats:
        return None
    return merge_stats(today_stats) if len(today_stats) > 1 else today_stats[0]


def _estimate_cost(stats: SessionStats) -> float:
    return estimate_cost_from_token_by_model(stats.token_by_model)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


def _fmt_duration(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _build_message(stats: SessionStats) -> dict:
    """Build notification message content"""
    cost = _estimate_cost(stats)
    today = datetime.now().strftime("%Y-%m-%d")
    active = stats.active_duration.total_seconds()
    ai_pct = (
        round(stats.ai_duration.total_seconds() / active * 100)
        if active > 0 else 0
    )

    # Efficiency score
    total_tokens = stats.token_usage.total
    total_code = stats.total_added + stats.total_removed
    code_per_1k = round(total_code / max(total_tokens / 1000, 1), 2) if total_tokens > 0 else 0
    avg_tpm = total_tokens // max(stats.user_message_count, 1)
    code_score = min(40, int(code_per_1k / 0.5 * 40))
    precision_score = max(0, min(30, int((1 - min(avg_tpm, 200_000) / 200_000) * 30)))
    util_score = min(30, int(ai_pct / 70 * 30))
    total_score = code_score + precision_score + util_score
    grade = "S" if total_score >= 90 else "A" if total_score >= 75 else "B" if total_score >= 60 else "C" if total_score >= 40 else "D"

    return {
        "date": today,
        "sessions": len([s for s in [stats]]),  # merged = 1
        "instructions": stats.user_message_count,
        "active_time": _fmt_duration(active),
        "ai_ratio": f"{ai_pct}%",
        "tokens": _fmt_tokens(total_tokens),
        "cost": f"${cost:.2f}",
        "code_added": stats.total_added + stats.git_total_added,
        "code_removed": stats.total_removed + stats.git_total_removed,
        "grade": grade,
        "score": total_score,
        "git_commits": stats.git_commit_count,
    }


def send_feishu(webhook_url: str, stats: SessionStats) -> bool:
    """Send Feishu bot notification"""
    msg = _build_message(stats)
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"Claude Code Daily Report {msg['date']}"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Sessions**\n{msg['instructions']} instructions"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Active Time**\n{msg['active_time']} (AI {msg['ai_ratio']})"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Token**\n{msg['tokens']}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Cost**\n{msg['cost']}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Code**\n+{msg['code_added']} / -{msg['code_removed']}"}},
                        {"is_short": True, "text": {"tag": "lark_md", "content": f"**Efficiency**\n{msg['grade']} ({msg['score']}/100)"}},
                    ],
                },
            ],
        },
    }
    return _post_json(webhook_url, payload)


def send_dingtalk(webhook_url: str, stats: SessionStats) -> bool:
    """Send DingTalk bot notification"""
    msg = _build_message(stats)
    text = (
        f"### Claude Code Daily Report {msg['date']}\n\n"
        f"| Metric | Value |\n"
        f"|------|------|\n"
        f"| Instructions | {msg['instructions']} |\n"
        f"| Active Time | {msg['active_time']} (AI {msg['ai_ratio']}) |\n"
        f"| Token | {msg['tokens']} |\n"
        f"| Cost | {msg['cost']} |\n"
        f"| Code | +{msg['code_added']} / -{msg['code_removed']} |\n"
        f"| Efficiency | {msg['grade']} ({msg['score']}/100) |\n"
        f"| Git | {msg['git_commits']} commits |"
    )
    payload = {"msgtype": "markdown", "markdown": {"title": "Claude Code Daily Report", "text": text}}
    return _post_json(webhook_url, payload)


def send_slack(webhook_url: str, stats: SessionStats) -> bool:
    """Send Slack notification"""
    msg = _build_message(stats)
    text = (
        f"*Claude Code Daily Report {msg['date']}*\n"
        f"• Instructions: {msg['instructions']} | Active: {msg['active_time']} (AI {msg['ai_ratio']})\n"
        f"• Tokens: {msg['tokens']} | Cost: {msg['cost']}\n"
        f"• Code: +{msg['code_added']} / -{msg['code_removed']} | {msg['git_commits']} commits\n"
        f"• Efficiency: {msg['grade']} ({msg['score']}/100)"
    )
    payload = {"text": text}
    return _post_json(webhook_url, payload)


def _post_json(url: str, payload: dict) -> bool:
    """POST JSON to webhook URL"""
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        print(f"Webhook send failed: {e}")
        return False


def send_notification(webhook_url: str, platform: str = "auto") -> bool:
    """Send today's statistics notification

    Args:
        webhook_url: Webhook URL
        platform: feishu/dingtalk/slack/auto (auto-detect)
    """
    stats = _collect_today_stats()
    if not stats:
        print("No session data for today")
        return False

    # Auto-detect platform
    if platform == "auto":
        if "feishu.cn" in webhook_url or "larksuite.com" in webhook_url:
            platform = "feishu"
        elif "dingtalk.com" in webhook_url or "oapi.dingtalk" in webhook_url:
            platform = "dingtalk"
        elif "hooks.slack.com" in webhook_url:
            platform = "slack"
        else:
            print("Cannot auto-detect platform, please specify --platform feishu/dingtalk/slack")
            return False

    senders = {
        "feishu": send_feishu,
        "dingtalk": send_dingtalk,
        "slack": send_slack,
    }
    sender = senders.get(platform)
    if not sender:
        print(f"Unsupported platform: {platform}")
        return False

    if sender(webhook_url, stats):
        print(f"Sent to {platform}")
        return True
    return False
