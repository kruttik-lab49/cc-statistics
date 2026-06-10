"""Background version check module

Periodically checks PyPI for the latest cc-statistics version and caches the result locally.
Network failures are silently handled and do not affect normal usage.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__

# ── Constants ──────────────────────────────────────────────────────────

PACKAGE_NAME = "cc-statistics"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
CACHE_DIR = Path.home() / ".cc-stats"
CACHE_FILE = CACHE_DIR / "version_cache.json"
CONFIG_FILE = CACHE_DIR / "config.json"

DEFAULT_CHECK_INTERVAL = 4 * 3600  # 4 hours (seconds)
REQUEST_TIMEOUT = 5  # seconds


# ── Install method detection ─────────────────────────────────────────────────

def _path_contains(path: str, needle: str) -> bool:
    return needle in path.replace(os.sep, "/")


def detect_install_manager(prefix: str | None = None) -> str:
    """Best-effort detection of which tool installed the current package.

    The return value is used to select the upgrade command. Inferred from the current
    Python environment path only; no external commands are executed.
    """
    install_prefix = os.path.realpath(prefix or sys.prefix)

    if _path_contains(install_prefix, "/uv/tools/") or _path_contains(
        install_prefix, "/.local/share/uv/tools/"
    ):
        return "uv-tool"

    if _path_contains(install_prefix, "/pipx/venvs/"):
        return "pipx"

    return "pip"


def _quote_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def get_upgrade_command() -> str:
    """Return the upgrade command text appropriate for the current installation method.

    This is the command shown to the user; actual upgrade execution should still use
    an argument array rather than a shell string.
    """
    manager = detect_install_manager()
    if manager == "uv-tool":
        return "uv tool upgrade cc-statistics"
    if manager == "pipx":
        return "pipx upgrade cc-statistics"
    return _quote_command([sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME])


def get_install_info() -> dict[str, str]:
    """Export installation metadata readable by the app."""
    return {
        "version": __version__,
        "manager": detect_install_manager(),
        "python_executable": sys.executable,
        "python_prefix": sys.prefix,
        "entrypoint": shutil.which("cc-stats-app") or "",
        "upgrade_command": get_upgrade_command(),
    }


# ── Data structures ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class VersionCache:
    """Version cache (immutable)"""
    latest_version: str
    checked_at: float  # Unix timestamp

    def to_dict(self) -> dict:
        return {
            "latest_version": self.latest_version,
            "checked_at": self.checked_at,
        }

    @staticmethod
    def from_dict(data: dict) -> VersionCache:
        return VersionCache(
            latest_version=str(data.get("latest_version", "")),
            checked_at=float(data.get("checked_at", 0)),
        )


@dataclass(frozen=True)
class CheckResult:
    """Version check result (immutable)"""
    has_update: bool
    current_version: str
    latest_version: str
    upgrade_command: str = "pip install --upgrade cc-statistics"


# ── Configuration ──────────────────────────────────────────────────────────

def load_config() -> dict:
    """Read user configuration. Returns a new dict without modifying any external state."""
    try:
        if CONFIG_FILE.exists():
            text = CONFIG_FILE.read_text(encoding="utf-8")
            return json.loads(text)
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def is_auto_check_enabled() -> bool:
    """Determine whether automatic version checking is enabled (enabled by default)"""
    config = load_config()
    return bool(config.get("auto_check_update", True))


def get_check_interval() -> int:
    """Get check interval (seconds)"""
    config = load_config()
    interval = config.get("check_interval", DEFAULT_CHECK_INTERVAL)
    try:
        return max(300, int(interval))  # minimum 5 minutes
    except (TypeError, ValueError):
        return DEFAULT_CHECK_INTERVAL


# ── Cache ──────────────────────────────────────────────────────────

def _read_cache() -> Optional[VersionCache]:
    """Read cache file. Returns None on failure without raising exceptions."""
    try:
        if CACHE_FILE.exists():
            text = CACHE_FILE.read_text(encoding="utf-8")
            data = json.loads(text)
            return VersionCache.from_dict(data)
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        pass
    return None


def _write_cache(cache: VersionCache) -> None:
    """Write cache file. Failures are silently handled."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(cache.to_dict(), indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# ── Version comparison ──────────────────────────────────────────────────────

def parse_version(version: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for comparison.

    e.g. "0.10.3" → (0, 10, 3)
    Non-numeric parts are treated as 0.
    """
    parts: list[int] = []
    for part in version.strip().split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    """Determine whether the remote version is newer than the local version"""
    return parse_version(remote) > parse_version(local)


# ── Network requests ──────────────────────────────────────────────────────

def fetch_latest_version() -> Optional[str]:
    """Fetch the latest version number from PyPI. Returns None on network failure."""
    try:
        req = urllib.request.Request(
            PYPI_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            version = data.get("info", {}).get("version")
            return str(version) if version else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError,
            KeyError, TypeError, ValueError):
        return None


# ── Main logic ──────────────────────────────────────────────────────

def check_for_update(force: bool = False) -> Optional[CheckResult]:
    """Check whether a new version is available.

    - Returns None if auto-check is disabled and not forced
    - Uses cached result if cache has not expired and not forced
    - Silently returns None on network failure
    - Returns CheckResult (immutable) or None
    """
    if not force and not is_auto_check_enabled():
        return None

    current = __version__
    now = time.time()
    interval = get_check_interval()

    # Check cache
    cache = _read_cache()
    if cache is not None and not force:
        elapsed = now - cache.checked_at
        if elapsed < interval:
            # Cache not expired, use directly
            if is_newer(cache.latest_version, current):
                return CheckResult(
                    has_update=True,
                    current_version=current,
                    latest_version=cache.latest_version,
                    upgrade_command=get_upgrade_command(),
                )
            return None

    # Cache expired or forced refresh, request PyPI
    latest = fetch_latest_version()
    if latest is None:
        return None

    # Write new cache
    new_cache = VersionCache(latest_version=latest, checked_at=now)
    _write_cache(new_cache)

    if is_newer(latest, current):
        return CheckResult(
            has_update=True,
            current_version=current,
            latest_version=latest,
            upgrade_command=get_upgrade_command(),
        )

    return None


def get_cached_update() -> Optional[CheckResult]:
    """Read update information from cache only (no network request).

    Suitable for quick prompts at CLI startup to avoid blocking.
    """
    cache = _read_cache()
    if cache is None:
        return None

    current = __version__
    if is_newer(cache.latest_version, current):
        return CheckResult(
            has_update=True,
            current_version=current,
            latest_version=cache.latest_version,
            upgrade_command=get_upgrade_command(),
        )
    return None


def format_update_message(result: CheckResult) -> str:
    """Format update notification message"""
    return (
        f"cc-statistics v{result.latest_version} is available, "
        f"run {result.upgrade_command} to upgrade"
    )
