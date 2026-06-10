"""Unified model pricing and matching logic."""

from __future__ import annotations

from typing import Any, TypedDict


class Pricing(TypedDict):
    """USD per 1M tokens."""

    input: float
    output: float
    cache_read: float
    cache_create: float


# Pricing sources (calibrated 2026-04-16):
# - OpenAI: https://developers.openai.com/api/docs/pricing
# - Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
# - Gemini: https://ai.google.dev/gemini-api/docs/pricing
#
# Notes:
# - Gemini 2.5 Pro/Flash priced at <=200k context tier (request size not determinable from logs).
# - OpenAI has no separate “cache write” price; cache_create falls back to input price.
MODEL_PRICING: dict[str, Pricing] = {
    # Claude
    "claude-opus-4.6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4.5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4.1": {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_create": 18.75},
    "claude-sonnet-4.6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
    "claude-sonnet-4.5": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku-4.5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
    # Legacy model compatibility
    "claude-haiku-legacy": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_create": 1.0},
    # OpenAI (GPT/Codex)
    "gpt-5.4": {"input": 2.50, "output": 15.00, "cache_read": 0.25, "cache_create": 2.50},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cache_read": 0.075, "cache_create": 0.75},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "cache_read": 0.020, "cache_create": 0.20},
    "gpt-5.3-codex": {"input": 1.75, "output": 14.00, "cache_read": 0.175, "cache_create": 1.75},
    "gpt-5.3-chat-latest": {"input": 1.75, "output": 14.00, "cache_read": 0.175, "cache_create": 1.75},
    # Legacy model compatibility
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25, "cache_create": 2.50},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_create": 0.15},
    "o1": {"input": 15.00, "output": 60.00, "cache_read": 7.50, "cache_create": 15.00},
    "o3": {"input": 10.00, "output": 40.00, "cache_read": 2.50, "cache_create": 10.00},
    "o3-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.55, "cache_create": 1.10},
    "o4-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.55, "cache_create": 1.10},
    # Gemini
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cache_read": 0.125, "cache_create": 1.25},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_read": 0.03, "cache_create": 0.30},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cache_read": 0.01, "cache_create": 0.10},
    # Legacy model compatibility
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025, "cache_create": 0.10},
}


def match_model_pricing(model: str) -> Pricing:
    """Match model pricing by name; unknown models fall back to the primary model for the same vendor."""
    lower = model.lower()

    # OpenAI / Codex
    if "gpt-5.4-mini" in lower:
        return MODEL_PRICING["gpt-5.4-mini"]
    if "gpt-5.4-nano" in lower:
        return MODEL_PRICING["gpt-5.4-nano"]
    if "gpt-5.4" in lower:
        return MODEL_PRICING["gpt-5.4"]
    if "gpt-5.3-chat-latest" in lower:
        return MODEL_PRICING["gpt-5.3-chat-latest"]
    if "gpt-5.3-codex" in lower:
        return MODEL_PRICING["gpt-5.3-codex"]
    if "gpt-5" in lower and "codex" in lower:
        return MODEL_PRICING["gpt-5.3-codex"]
    if "gpt-4o-mini" in lower:
        return MODEL_PRICING["gpt-4o-mini"]
    if "gpt-4o" in lower:
        return MODEL_PRICING["gpt-4o"]
    if "o4-mini" in lower:
        return MODEL_PRICING["o4-mini"]
    if "o3-mini" in lower:
        return MODEL_PRICING["o3-mini"]
    if "o3" in lower:
        return MODEL_PRICING["o3"]
    if "o1" in lower:
        return MODEL_PRICING["o1"]

    # Gemini
    if "gemini-2.5-pro" in lower:
        return MODEL_PRICING["gemini-2.5-pro"]
    if "gemini-2.5-flash-lite" in lower:
        return MODEL_PRICING["gemini-2.5-flash-lite"]
    if "gemini-2.5-flash" in lower:
        return MODEL_PRICING["gemini-2.5-flash"]
    if "gemini-2.0-flash" in lower:
        return MODEL_PRICING["gemini-2.0-flash"]
    if "gemini" in lower:
        return MODEL_PRICING["gemini-2.5-flash"]

    # Claude
    if "opus" in lower:
        if "4.6" in lower or "4-6" in lower:
            return MODEL_PRICING["claude-opus-4.6"]
        if "4.5" in lower or "4-5" in lower:
            return MODEL_PRICING["claude-opus-4.5"]
        return MODEL_PRICING["claude-opus-4.1"]
    if "haiku" in lower:
        if "4.5" in lower or "4-5" in lower:
            return MODEL_PRICING["claude-haiku-4.5"]
        return MODEL_PRICING["claude-haiku-legacy"]
    if "sonnet" in lower:
        if "4.6" in lower or "4-6" in lower:
            return MODEL_PRICING["claude-sonnet-4.6"]
        if "4.5" in lower or "4-5" in lower:
            return MODEL_PRICING["claude-sonnet-4.5"]
        return MODEL_PRICING["claude-sonnet-4"]

    # Vendor fallback (prevents historical dirty data from causing missing cost estimates)
    if "gpt" in lower or lower.startswith("o"):
        return MODEL_PRICING["gpt-5.3-codex"]
    if "gemini" in lower:
        return MODEL_PRICING["gemini-2.5-flash"]
    return MODEL_PRICING["claude-sonnet-4.6"]


def is_claude_model(model: str) -> bool:
    lower = model.lower()
    return "claude" in lower or "sonnet" in lower or "opus" in lower or "haiku" in lower


def estimate_cost_from_token_by_model(token_by_model: dict[str, Any]) -> float:
    """Estimate total cost from token_by_model."""
    total = 0.0
    for model, usage in token_by_model.items():
        p = match_model_pricing(model)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_create_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)

        total += input_tokens / 1_000_000 * p["input"]
        total += output_tokens / 1_000_000 * p["output"]
        total += cache_read_tokens / 1_000_000 * p["cache_read"]
        total += cache_create_tokens / 1_000_000 * p["cache_create"]
    return total
