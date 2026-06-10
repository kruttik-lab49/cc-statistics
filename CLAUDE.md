# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

cc-statistics — CLI tool for computing AI coding engineering metrics from Claude Code sessions.

Data source: JSONL session files under `~/.claude/projects/`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```bash
cc-stats --list              # List all projects
cc-stats                     # Analyze the current directory project
cc-stats <project-keyword>   # Match project by keyword
cc-stats <path/to/file.jsonl> # Analyze a specific JSONL file
cc-stats --all               # Analyze all projects
cc-stats --last N            # Only show the most recent N sessions
```

## Architecture

- `cc_stats/parser.py` — Parse JSONL into Session/Message data structures
- `cc_stats/analyzer.py` — Compute 5 engineering metrics from a Session (instruction count, tool calls, duration, lines of code, tokens)
- `cc_stats/formatter.py` — Format statistics results as terminal table output
- `cc_stats/cli.py` — argparse CLI entry point, handles file discovery and argument processing
- `cc_stats_web/server.py` — ThreadingHTTPServer serving the web dashboard and JSON API
- `cc_stats_web/web/index.html` — Single-file dark-themed web dashboard (vanilla JS, no build step)

## Key conventions

- Pure Python stdlib, no third-party dependencies
- User message criteria: `type == "user"` AND `is_tool_result == False` AND `is_meta == False`
- Active time: message gaps <= 5 minutes count as active
- Lines of code come from the `input` parameters of Edit/Write tool calls

## Code Review Standards

All code changes (including community PRs) must pass the following security and performance checks before merging.

### Security Checklist

- [ ] **External input is untrusted**: File names, project names, paths, and other values from users or the filesystem must not be concatenated directly into commands or scripts. They must be sanitized or escaped first.
- [ ] **Path traversal**: Query parameters used to construct filesystem paths (e.g. `?project=` in the web API) must be validated to reject `..`, `/`, and `\` before joining with `pathlib`.
- [ ] **Defensive JSONL parsing**: When parsing external JSONL files, handle missing fields, type mismatches, and malformed data defensively. A single dirty record must not crash the entire process.
- [ ] **No hardcoded sensitive information**: Code must not contain API keys, tokens, passwords, or other sensitive data.

### Performance Checklist

- [ ] **No synchronous subprocesses in loops**: `Process()` / `subprocess` and similar synchronous external commands must not be called inside `for` loops. Use batch processing or async approaches for bulk execution.
- [ ] **Time complexity evaluation**: For logic that iterates over all sessions/messages, evaluate the time complexity. Avoid O(N²) or higher nested loops; prefer single-pass + bucketing/indexing.
- [ ] **Async or caching for IO-heavy operations**: Disk reads, file traversals, and similar IO operations should use a caching strategy to avoid repeated reads. Large file operations should run on a background thread.
- [ ] **Avoid unnecessary full recomputation**: When filter conditions change but the data source has not, reuse cached results rather than reloading from scratch.

### PR Review Process

1. **Automated checks**: CI must pass compilation checks after a PR is submitted.
2. **Security review**: Changes involving string concatenation, external input handling, or command execution must be reviewed line-by-line to confirm escaping and sanitization.
3. **Performance review**: Changes involving data traversal, subprocess calls, or IO operations must be evaluated for behavior under large data volumes.
4. **Additional requirements for community PRs**: PRs from community contributors require project maintainer approval before merging, with a focus on the security and performance items above.
