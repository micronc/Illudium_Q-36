"""
Persistent API-key budget vs Illudium-estimated spend.

Anthropic does not expose prepaid credit balance on a normal API key.
Analysts set how much USD they allocated to the key; Illudium tracks
estimated spend from token usage against that allocation.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_LEDGER = ROOT / "briefings" / ".illudium_api_spend.json"
_LOCK = threading.Lock()


def ledger_path() -> Path:
    raw = os.environ.get("SPEND_LEDGER_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_LEDGER


def _default_state() -> dict[str, Any]:
    budget = os.environ.get("API_BUDGET_USD", "").strip()
    budget_val = float(budget) if budget else None
    return {
        "budget_usd": budget_val,
        "budget_configured": budget_val is not None,
        "spent_usd": 0.0,
        "source": "illudium-estimate",
        "note": (
            "Anthropic does not publish remaining credit balance via the "
            "standard API key. Spent is estimated from token usage × model rates."
        ),
    }


def _read() -> dict[str, Any]:
    path = ledger_path()
    state = _default_state()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                state.update(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    # Env budget wins only when file never configured a budget
    if not state.get("budget_configured"):
        env_budget = os.environ.get("API_BUDGET_USD", "").strip()
        if env_budget:
            try:
                state["budget_usd"] = float(env_budget)
                state["budget_configured"] = True
            except ValueError:
                pass
    state["spent_usd"] = round(float(state.get("spent_usd") or 0.0), 6)
    if state.get("budget_usd") is not None:
        state["budget_usd"] = round(float(state["budget_usd"]), 4)
    return state


def _write(state: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "budget_usd": state.get("budget_usd"),
        "budget_configured": bool(state.get("budget_configured")),
        "spent_usd": round(float(state.get("spent_usd") or 0.0), 6),
        "source": "illudium-estimate",
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def status(session_cost_usd: float = 0.0) -> dict[str, Any]:
    with _LOCK:
        state = _read()
    budget = state.get("budget_usd")
    spent = float(state.get("spent_usd") or 0.0)
    remaining = None if budget is None else round(float(budget) - spent, 6)
    pct = None
    if budget and float(budget) > 0:
        pct = round(min(100.0, max(0.0, (spent / float(budget)) * 100.0)), 1)
    return {
        "budget_usd": budget,
        "budget_configured": bool(state.get("budget_configured") and budget is not None),
        "spent_usd": round(spent, 6),
        "remaining_usd": remaining,
        "percent_used": pct,
        "session_cost_usd": round(float(session_cost_usd or 0.0), 6),
        "source": state.get("source") or "illudium-estimate",
        "note": state.get("note")
        or _default_state()["note"],
    }


def set_budget(usd: float | int | str) -> dict[str, Any]:
    value = float(usd)
    if value < 0:
        raise ValueError("Budget must be zero or positive.")
    if value > 1_000_000:
        raise ValueError("Budget exceeds allowed maximum ($1,000,000).")
    with _LOCK:
        state = _read()
        state["budget_usd"] = round(value, 4)
        state["budget_configured"] = True
        _write(state)
    return status()


def add_spend(delta_usd: float | None) -> dict[str, Any]:
    amount = float(delta_usd or 0.0)
    if amount <= 0:
        return status()
    with _LOCK:
        state = _read()
        state["spent_usd"] = round(float(state.get("spent_usd") or 0.0) + amount, 6)
        _write(state)
        spent = float(state["spent_usd"])
        budget = state.get("budget_usd")
    remaining = None if budget is None else round(float(budget) - spent, 6)
    return {
        "budget_usd": budget,
        "budget_configured": bool(state.get("budget_configured") and budget is not None),
        "spent_usd": spent,
        "remaining_usd": remaining,
    }


def reset_spend() -> dict[str, Any]:
    """Zero tracked spend (does not change allocated budget)."""
    with _LOCK:
        state = _read()
        state["spent_usd"] = 0.0
        _write(state)
    return status()
