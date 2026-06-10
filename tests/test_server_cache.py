"""Tests for the session stats cache and project list cache in server.py.

Each test case targets a specific failure mode:
- Cache hit returns identical result to a fresh parse
- Cache invalidates when file mtime changes (stale data never served)
- Cache is keyed per file (two different files stay independent)
- Project list cache respects TTL (expires and re-fetches)
- Project list cache is not stale within TTL
- Thread safety: concurrent reads on the same file don't produce duplicate parses
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

import cc_stats_web.server as srv
from cc_stats_web.server import (
    _get_cached_session_stats,
    _get_projects_cached,
    _session_cache,
    _session_cache_lock,
    _projects_cache,
    _projects_cache_lock,
    _PROJECTS_CACHE_TTL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_jsonl(cwd: str, tokens: int = 1000) -> str:
    """Return the text of a minimal valid session JSONL with one assistant turn."""
    session_id = "test-session-id"
    lines = [
        json.dumps({"type": "mode", "mode": "normal", "sessionId": session_id}),
        # User message
        json.dumps({
            "parentUuid": None,
            "isSidechain": False,
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "uuid": "u1",
            "timestamp": "2026-06-01T10:00:00.000Z",
            "cwd": cwd,
            "sessionId": session_id,
        }),
        # Assistant message with token usage
        json.dumps({
            "parentUuid": "u1",
            "isSidechain": False,
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {
                    "input_tokens": tokens,
                    "output_tokens": tokens // 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
            "uuid": "a1",
            "timestamp": "2026-06-01T10:00:05.000Z",
            "cwd": cwd,
            "sessionId": session_id,
        }),
    ]
    return "\n".join(lines) + "\n"


def _clear_session_cache():
    with _session_cache_lock:
        _session_cache.clear()


def _clear_projects_cache():
    with _projects_cache_lock:
        _projects_cache["data"] = None
        _projects_cache["ts"] = 0.0


# ---------------------------------------------------------------------------
# Session cache — correctness
# ---------------------------------------------------------------------------

class TestSessionCacheCorrectness:

    def test_cached_result_matches_fresh_parse(self, tmp_path):
        """A cache hit must return the same stats as parsing the file from scratch."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=500))

        session1, stats1 = _get_cached_session_stats(f)
        session2, stats2 = _get_cached_session_stats(f)

        assert stats1.token_usage.total == stats2.token_usage.total
        assert stats1.token_usage.input_tokens == stats2.token_usage.input_tokens
        # Same object — second call is a cache hit
        assert stats1 is stats2

    def test_cache_hit_skips_reparse(self, tmp_path, monkeypatch):
        """_parse_session_file must not be called on a cache hit."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj"))

        parse_calls = []
        real_parse = srv._parse_session_file

        def tracking_parse(path):
            parse_calls.append(path)
            return real_parse(path)

        monkeypatch.setattr(srv, "_parse_session_file", tracking_parse)

        _get_cached_session_stats(f)   # cold — should call parse
        _get_cached_session_stats(f)   # warm — should NOT call parse

        assert len(parse_calls) == 1


# ---------------------------------------------------------------------------
# Session cache — invalidation
# ---------------------------------------------------------------------------

class TestSessionCacheInvalidation:

    def test_stale_cache_not_served_after_mtime_change(self, tmp_path):
        """When a file is modified (mtime changes), the old cached result is NOT returned."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=100))

        _, stats_before = _get_cached_session_stats(f)
        assert stats_before.token_usage.input_tokens == 100

        # Simulate file update: write new content and bump mtime
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=999))
        new_mtime = f.stat().st_mtime + 1
        os.utime(f, (new_mtime, new_mtime))

        _, stats_after = _get_cached_session_stats(f)
        assert stats_after.token_usage.input_tokens == 999
        assert stats_before is not stats_after

    def test_old_cache_entry_remains_after_mtime_change(self, tmp_path):
        """The old (mtime, path) key stays in the cache dict; only the new key is used."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=100))

        mtime_before = f.stat().st_mtime_ns
        _get_cached_session_stats(f)

        f.write_text(_minimal_jsonl("/tmp/proj", tokens=999))
        new_mtime = f.stat().st_mtime + 1
        os.utime(f, (new_mtime, new_mtime))
        mtime_after = f.stat().st_mtime_ns

        _get_cached_session_stats(f)

        with _session_cache_lock:
            keys = list(_session_cache.keys())

        assert (str(f), mtime_before) in keys
        assert (str(f), mtime_after) in keys

    def test_unchanged_file_mtime_returns_cache_hit(self, tmp_path):
        """A file whose mtime hasn't changed must always be a cache hit (no re-parse)."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=42))

        # Force a known mtime so it can't accidentally change
        fixed_mtime = 1_700_000_000.0
        os.utime(f, (fixed_mtime, fixed_mtime))

        _, s1 = _get_cached_session_stats(f)
        _, s2 = _get_cached_session_stats(f)
        _, s3 = _get_cached_session_stats(f)

        assert s1 is s2 is s3


# ---------------------------------------------------------------------------
# Session cache — isolation between files
# ---------------------------------------------------------------------------

class TestSessionCacheIsolation:

    def test_two_files_cached_independently(self, tmp_path):
        """Cache entries for different files don't interfere with each other."""
        _clear_session_cache()
        f1 = tmp_path / "s1.jsonl"
        f2 = tmp_path / "s2.jsonl"
        f1.write_text(_minimal_jsonl("/tmp/proj1", tokens=111))
        f2.write_text(_minimal_jsonl("/tmp/proj2", tokens=222))

        _, stats1 = _get_cached_session_stats(f1)
        _, stats2 = _get_cached_session_stats(f2)

        assert stats1.token_usage.input_tokens == 111
        assert stats2.token_usage.input_tokens == 222
        assert stats1 is not stats2

    def test_modifying_one_file_does_not_affect_other(self, tmp_path):
        """Modifying file A must not evict or alter the cache entry for file B."""
        _clear_session_cache()
        f1 = tmp_path / "s1.jsonl"
        f2 = tmp_path / "s2.jsonl"
        f1.write_text(_minimal_jsonl("/tmp/proj1", tokens=111))
        f2.write_text(_minimal_jsonl("/tmp/proj2", tokens=222))

        _, stats2_before = _get_cached_session_stats(f2)

        # Mutate f1 only
        f1.write_text(_minimal_jsonl("/tmp/proj1", tokens=999))
        new_mtime = f1.stat().st_mtime + 1
        os.utime(f1, (new_mtime, new_mtime))
        _get_cached_session_stats(f1)

        _, stats2_after = _get_cached_session_stats(f2)
        assert stats2_before is stats2_after  # f2 cache untouched


# ---------------------------------------------------------------------------
# Session cache — thread safety
# ---------------------------------------------------------------------------

class TestSessionCacheThreadSafety:

    def test_concurrent_reads_return_consistent_results(self, tmp_path):
        """Multiple threads reading the same file simultaneously must all get identical stats."""
        _clear_session_cache()
        f = tmp_path / "session.jsonl"
        f.write_text(_minimal_jsonl("/tmp/proj", tokens=77))

        results = []
        errors = []

        def worker():
            try:
                _, stats = _get_cached_session_stats(f)
                results.append(stats.token_usage.input_tokens)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        assert all(r == 77 for r in results)


# ---------------------------------------------------------------------------
# Project list cache — TTL and freshness
# ---------------------------------------------------------------------------

class TestProjectsCacheTTL:

    def test_returns_data_within_ttl(self, monkeypatch):
        """A second call within the TTL window must return cached data without re-calling _get_projects."""
        _clear_projects_cache()
        call_count = []

        def fake_get_projects():
            call_count.append(1)
            return [{"dir_name": "proj-a", "display_name": "Project A", "session_count": 1, "source": "claude"}]

        monkeypatch.setattr(srv, "_get_projects", fake_get_projects)

        result1 = _get_projects_cached()
        result2 = _get_projects_cached()

        assert call_count == [1]  # only one real fetch
        assert result1 is result2  # same object

    def test_cache_expires_after_ttl(self, monkeypatch):
        """After TTL elapses, the next call must re-fetch from _get_projects."""
        _clear_projects_cache()
        call_count = []

        def fake_get_projects():
            call_count.append(1)
            return [{"dir_name": f"proj-{len(call_count)}", "display_name": "P", "session_count": 1, "source": "claude"}]

        monkeypatch.setattr(srv, "_get_projects", fake_get_projects)

        _get_projects_cached()  # first fetch
        assert call_count == [1]

        # Expire the cache by backdating its timestamp
        with _projects_cache_lock:
            _projects_cache["ts"] = time.monotonic() - _PROJECTS_CACHE_TTL - 1

        _get_projects_cached()  # should trigger re-fetch
        assert len(call_count) == 2

    def test_stale_cache_not_served_after_ttl(self, monkeypatch):
        """Data returned after TTL expiry must come from a fresh _get_projects call, not the old value."""
        _clear_projects_cache()

        responses = [
            [{"dir_name": "old-proj", "display_name": "Old", "session_count": 1, "source": "claude"}],
            [{"dir_name": "new-proj", "display_name": "New", "session_count": 2, "source": "claude"}],
        ]
        call_idx = [0]

        def fake_get_projects():
            result = responses[call_idx[0]]
            call_idx[0] += 1
            return result

        monkeypatch.setattr(srv, "_get_projects", fake_get_projects)

        result1 = _get_projects_cached()
        assert result1[0]["dir_name"] == "old-proj"

        # Expire cache
        with _projects_cache_lock:
            _projects_cache["ts"] = time.monotonic() - _PROJECTS_CACHE_TTL - 1

        result2 = _get_projects_cached()
        assert result2[0]["dir_name"] == "new-proj"
        assert result1 is not result2

    def test_cache_warm_on_first_call(self, monkeypatch):
        """Before any call, the cache is cold and _get_projects must be called exactly once."""
        _clear_projects_cache()
        call_count = []

        monkeypatch.setattr(srv, "_get_projects", lambda: call_count.append(1) or [])

        _get_projects_cached()
        assert call_count == [1]
