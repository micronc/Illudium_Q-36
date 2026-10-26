"""
Token usage accounting and rough USD cost estimates for Anthropic models.

Rates are approximate defaults (USD per 1M tokens) and can be overridden with env:
  PRICE_<MODEL_SLUG>_IN / PRICE_<MODEL_SLUG>_OUT
  e.g. PRICE_CLAUDE_SONNET_4_6_IN=3
"""

from __future__ import annotations

import os
import re
from typing import Any

# Display name, API model id, default $/MTok input, default $/MTok output
AVAILABLE_MODELS: list[dict[str, Any]] = [
    {
        "id": "claude-opus-4-6",
        "label": "Claude Opus 4.6",
        "input_per_mtok": 15.0,
        "output_per_mtok": 75.0,
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "input_per_mtok": 3.0,
        "output_per_mtok": 15.0,
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5",
        "input_per_mtok": 1.0,
        "output_per_mtok": 5.0,
    },
    {
        "id": "claude-sonnet-4-5-20250929",
        "label": "Claude Sonnet 4.5",
        "input_per_mtok": 3.0,
        "output_per_mtok": 15.0,
    },
]

_MODEL_INDEX = {m["id"]: m for m in AVAILABLE_MODELS}


def _env_slug(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", model_id).upper().strip("_")


def rate_for(model_id: str) -> tuple[float, float]:
    meta = _MODEL_INDEX.get(model_id) or {
        "input_per_mtok": 3.0,
        "output_per_mtok": 15.0,
    }
    slug = _env_slug(model_id)
    inn = float(os.environ.get(f"PRICE_{slug}_IN", meta["input_per_mtok"]))
    out = float(os.environ.get(f"PRICE_{slug}_OUT", meta["output_per_mtok"]))
    return inn, out


def allowed_model_ids() -> set[str]:
    return set(_MODEL_INDEX.keys())


def resolve_model(requested: str | None, default: str) -> str:
    if requested and requested in _MODEL_INDEX:
        return requested
    if default in _MODEL_INDEX:
        return default
    return AVAILABLE_MODELS[0]["id"]


def estimate_cost_usd(
    model_id: str, input_tokens: int, output_tokens: int
) -> float:
    inn, out = rate_for(model_id)
    return (input_tokens / 1_000_000.0) * inn + (output_tokens / 1_000_000.0) * out


def usage_from_response(model_id: str, response) -> dict[str, Any]:
    """Build a usage dict from an Anthropic Messages API response."""
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cost = estimate_cost_usd(model_id, input_tokens, output_tokens)
    return {
        "model": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(cost, 6),
    }


def empty_usage(model_id: str) -> dict[str, Any]:
    return {
        "model": model_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def add_usage(total: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any]:
    if not delta:
        return total
    total = dict(total)
    total["input_tokens"] = int(total.get("input_tokens", 0)) + int(
        delta.get("input_tokens", 0)
    )
    total["output_tokens"] = int(total.get("output_tokens", 0)) + int(
        delta.get("output_tokens", 0)
    )
    total["total_tokens"] = total["input_tokens"] + total["output_tokens"]
    total["cost_usd"] = round(
        float(total.get("cost_usd", 0.0)) + float(delta.get("cost_usd", 0.0)), 6
    )
    if delta.get("model"):
        total["last_model"] = delta["model"]
    return total


def list_models_public() -> list[dict[str, Any]]:
    out = []
    for m in AVAILABLE_MODELS:
        inn, o = rate_for(m["id"])
        out.append(
            {
                "id": m["id"],
                "label": m["label"],
                "input_per_mtok_usd": inn,
                "output_per_mtok_usd": o,
            }
        )
    return out
