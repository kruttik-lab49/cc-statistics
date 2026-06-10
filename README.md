<div align="center">
  <h1>cc-statistics</h1>
  <p><strong>AI coding session tracker for Claude Code, Gemini CLI, and Codex CLI.</strong></p>
  <p><em>Track every token, cost, and session across your AI tools. 100% local. Zero dependencies.</em></p>

  <p>
    <a href="https://pypi.org/project/cc-statistics/"><img src="https://img.shields.io/pypi/v/cc-statistics?color=blue&style=flat-square&logo=python" alt="PyPI"></a>
    <a href="https://pypi.org/project/cc-statistics/"><img src="https://img.shields.io/pypi/dm/cc-statistics?color=green&style=flat-square" alt="Downloads"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/androidZzT/cc-statistics?style=flat-square" alt="License"></a>
    <img src="https://img.shields.io/badge/zero--dependencies-stdlib%20only-orange?style=flat-square" alt="Zero Dependencies">
    <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square" alt="Platform">
  </p>

  <p>
    <a href="#-features">Features</a> &bull;
    <a href="#-quick-start">Quick Start</a> &bull;
    <a href="#-cli-reference">CLI</a>
  </p>
</div>

---

## 🤔 Why cc-statistics?

You're using multiple AI coding tools. But do you actually know:

- 💸 **What your combined spend is** across Claude Code, Gemini CLI, and Codex?
- 🔧 **Which MCP tools are being called most** — and whether they're worth the tokens?
- ⏱️ **How much time Claude is actually working** versus waiting for you?
- 📈 **Which projects are consuming the most** — and across which models?

> cc-statistics is a community project, not affiliated with Anthropic, Google, or OpenAI.

---

## 🚀 Features

### 🌐 Multi-Platform Unified View
> Claude Code · Gemini CLI · Codex CLI — switch between platforms or aggregate all into a single report. Each source is read entirely from local files; no API keys, no accounts, no network requests.

### 📊 Usage Quota Predictor
> Real-time prediction of when you'll hit your usage quota based on current burn rate. Displays estimated time remaining and risk level — so you can pace your usage and avoid unexpected throttling.

### 🔍 Session Search & Export
> Search your entire session history by keyword across all platforms. Export individual session conversations as clean Markdown.

### 📊 Multi-Dimensional Analytics
> Instructions count · Tool calls Top 10 (Skill and MCP tools broken out by name) · AI processing time vs user active time · Code changes by language (via `git log --numstat`) · Token breakdown by model · Cost estimation with built-in pricing for Opus / Sonnet / Haiku / Gemini 2.5 Pro / Flash / GPT-4o

### 📋 Weekly & Monthly Reports
> Auto-generate Markdown summaries for any period: total tokens, cost by model, most active projects, top tool calls, code changes by language. Push directly to your team channel:
> ```bash
> cc-stats --report week
> cc-stats --notify https://hooks.slack.com/services/xxx
> ```
> Slack, Feishu, and DingTalk webhooks supported.

### ⚡ Project Comparison
> See which projects are consuming the most resources side by side:
> ```bash
> cc-stats --compare --since 1w
> ```

### 🌐 Web Dashboard
> Browser-based dark-themed dashboard — run `cc-stats-web` and open in any browser.

### 🔒 100% Local & Zero Dependencies
> All data is read from local files. Nothing is sent over the network. Pure Python standard library — no npm, no Docker.

---

## 🖼️ Screenshots

<table>
  <tr>
    <td align="center"><strong>🌐 Web Dashboard</strong></td>
    <td align="center"><strong>🔧 Tool Call Analytics</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/cc-stat-web.png" alt="Web Dashboard" width="100%"></td>
    <td><img src="docs/screenshots/cc-stat-tools.png" alt="Tool Call Analytics" width="100%"></td>
  </tr>
</table>

### CLI Demo

<img src="docs/screenshots/cc-stat-cli.png" width="680" alt="CC Stats CLI Demo">

---

## ⚡ Quick Start

### Prerequisites

- Python 3.8+
- At least one of: Claude Code CLI, Gemini CLI, Codex CLI, or Cursor installed and used

### 3 steps

```bash
# 1. Install
uv tool install cc-statistics   # or: pipx install cc-statistics

# 2. Run your first report (all platforms, last 7 days)
cc-stats --all --since 7d

# 3. Open the web dashboard
cc-stats-web
```

That's it. No configuration file needed.

---

## 📖 CLI Reference

```bash
cc-stats                      # Analyze current directory sessions
cc-stats --list               # List all detected projects (all platforms)
cc-stats --all --since 3d     # Last 3 days, all projects, all platforms
cc-stats --all --since 1w     # Last week
cc-stats myproject --last 3   # Last 3 sessions for a specific project
cc-stats --report week        # Generate weekly Markdown report
cc-stats --report month       # Generate monthly Markdown report
cc-stats --compare --since 1w # Side-by-side project comparison
cc-stats --notify <url>       # Push report to Slack / Feishu / DingTalk webhook
cc-stats-web                  # Open web dashboard in browser
```

---

## 🗂️ Data Sources

All data is read from local files. Nothing is sent over the network.

| Source | Local path |
|--------|-----------|
| Claude Code | `~/.claude/projects/<project>/<session>.jsonl` |
| Gemini CLI | `~/.gemini/tmp/<project>/chats/<session>.json` |
| Codex CLI | `~/.codex/sessions/*.jsonl` |
| Git Changes | `git log --numstat` in project directory |

---

## Support

If cc-statistics saves you money on your AI coding bills, consider [sponsoring](https://github.com/sponsors/androidZzT) the project.
