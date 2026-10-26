"""
Per-analyst session helpers: size accounting, limit config, and continuity capture.
Chat history remains in-process (sticky affinity keeps a client on one worker/replica).
"""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from typing import Any

import core

DEFAULT_LIMIT_MB = 5.0
MIN_LIMIT_MB = 1.0
MAX_LIMIT_MB = 100.0


def clamp_limit_mb(value: float | int | str | None) -> float:
    try:
        mb = float(value)
    except (TypeError, ValueError):
        mb = DEFAULT_LIMIT_MB
    return max(MIN_LIMIT_MB, min(MAX_LIMIT_MB, mb))


def history_bytes(history: list[dict]) -> int:
    total = 0
    for turn in history or []:
        total += len((turn.get("role") or "").encode("utf-8"))
        total += len((turn.get("content") or "").encode("utf-8"))
    return total


def intel_bytes(filenames: list[str] | None = None) -> int:
    total = 0
    files = core.list_intel_files()
    wanted = set(filenames) if filenames is not None else None
    for f in files:
        if wanted is not None and f["name"] not in wanted:
            continue
        total += int(f.get("size") or 0)
    return total


def session_usage(
    history: list[dict],
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    h = history_bytes(history)
    i = intel_bytes(filenames)
    return {
        "history_bytes": h,
        "intel_bytes": i,
        "used_bytes": h + i,
    }


def usage_status(
    history: list[dict],
    limit_mb: float,
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    limit_bytes = int(limit_mb * 1024 * 1024)
    usage = session_usage(history, filenames=filenames)
    used = usage["used_bytes"]
    percent = (used / limit_bytes * 100.0) if limit_bytes else 100.0
    return {
        **usage,
        "limit_mb": limit_mb,
        "limit_bytes": limit_bytes,
        "percent": round(min(percent, 100.0), 1),
        "limit_reached": used >= limit_bytes,
        "remaining_bytes": max(0, limit_bytes - used),
    }


def _briefing_snippets(limit: int = 5) -> list[dict[str, str]]:
    core.ensure_dirs()
    paths = sorted(
        core.BRIEFINGS_DIR.glob("briefing_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out = []
    for path in paths[:limit]:
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(
            {
                "name": path.name,
                "preview": text[:2000],
            }
        )
    return out


def build_continuity_capture(
    history: list[dict],
    limit_mb: float,
    filenames: list[str] | None = None,
) -> dict[str, Any]:
    """Highly detailed capture + summary/next-steps prompt for seamless continuation."""
    status = usage_status(history, limit_mb, filenames=filenames)
    files = core.list_intel_files()
    if filenames is not None:
        selected = [f for f in files if f["name"] in set(filenames)]
    else:
        selected = files

    user_turns = [t for t in history if t.get("role") == "user"]
    assistant_turns = [t for t in history if t.get("role") == "assistant"]
    topics = []
    for t in user_turns[-12:]:
        content = (t.get("content") or "").strip().replace("\n", " ")
        if content:
            topics.append(content[:180])

    accomplishments = [
        f"Loaded {len(selected)} intel source(s) into the working corpus.",
        f"Completed {len(user_turns)} analyst prompt(s) and {len(assistant_turns)} model response(s).",
        f"Session storage used {status['used_bytes']} bytes of {status['limit_bytes']} "
        f"({status['percent']}% of {limit_mb:g} MB limit).",
    ]
    if any("briefing" in (t.get("content") or "").lower() for t in history):
        accomplishments.append(
            "At least one executive briefing was requested or produced in this session."
        )

    briefings = _briefing_snippets()
    for b in briefings[:3]:
        accomplishments.append(f"Briefing artifact available: {b['name']}")

    summary = (
        "Illudium-Q36 session reached the configured storage limit. "
        f"The analyst worked with {len(selected)} intel file(s) across "
        f"{len(user_turns)} turn(s). Key asks included: "
        + ("; ".join(topics[-5:]) if topics else "n/a")
        + "."
    )

    intel_names = [f["name"] for f in selected]
    next_steps_prompt = f"""# Illudium-Q36 continuation prompt
Generated: {datetime.datetime.utcnow().isoformat(timespec="seconds")}Z
Prior session storage limit: {limit_mb:g} MB (reached)

## Mission continuity
You are continuing an Illudium-Q36 threat-analysis session that hit its storage limit.
Preserve prior conclusions; do not discard attribution, IOCs, ATT&CK/ATLAS mappings,
remediation LOE, or risk-if-no-action findings already established unless new intel contradicts them.

## Session summary
{summary}

## Accomplishments
{chr(10).join(f"- {a}" for a in accomplishments)}

## Intel corpus to reload
{chr(10).join(f"- {n}" for n in intel_names) if intel_names else "- (none listed — re-import from export package)"}

## Recent analyst asks
{chr(10).join(f"- {t}" for t in topics[-8:]) if topics else "- (none)"}

## Next steps for seamless operations
1. Re-import the intel files from the session export (or the list above).
2. Paste this entire continuation prompt as the first analyst message in a new session.
3. Confirm outstanding remediation items and residual risk still open.
4. Resume with the highest-priority unanswered question from the prior session.
5. Generate a fresh executive briefing only after the corpus is reloaded.

## Standing output requirements
When briefing, include: Threat Actor / Attribution; MITRE ATT&CK; MITRE ATLAS
(separation & overlap); Recommended Remediation Steps with level of effort each;
Risk If No Action Is Taken; Executive Summary.
"""

    detailed = {
        "generated_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "storage": status,
        "summary": summary,
        "accomplishments": accomplishments,
        "intel_files": selected,
        "chat_turns": len(history),
        "recent_topics": topics,
        "briefings": [{"name": b["name"]} for b in briefings],
        "next_steps_prompt": next_steps_prompt,
        "history": history,
    }
    return detailed


def capture_markdown(capture: dict[str, Any]) -> str:
    lines = [
        "# Illudium-Q36 session capture",
        f"**Generated:** {capture.get('generated_at')}",
        "",
        "## Summary",
        capture.get("summary", ""),
        "",
        "## Accomplishments",
    ]
    for a in capture.get("accomplishments") or []:
        lines.append(f"- {a}")
    lines.extend(["", "## Storage", "```json", json.dumps(capture.get("storage") or {}, indent=2), "```"])
    lines.extend(["", "## Intel files"])
    for f in capture.get("intel_files") or []:
        lines.append(f"- `{f.get('name')}` ({f.get('size')} bytes)")
    lines.extend(["", "## Recent topics"])
    for t in capture.get("recent_topics") or []:
        lines.append(f"- {t}")
    lines.extend(
        [
            "",
            "## Next steps / continuation prompt",
            "",
            capture.get("next_steps_prompt") or "",
            "",
        ]
    )
    return "\n".join(lines)


def export_session_zip(capture: dict[str, Any]) -> bytes:
    """Compressed session package: capture, continuation prompt, history, intel copies."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("capture.json", json.dumps(capture, indent=2))
        zf.writestr("CONTINUATION_PROMPT.md", capture.get("next_steps_prompt") or "")
        zf.writestr("SESSION_CAPTURE.md", capture_markdown(capture))
        zf.writestr(
            "history.json",
            json.dumps(capture.get("history") or [], indent=2),
        )
        for f in capture.get("intel_files") or []:
            name = f.get("name")
            if not name:
                continue
            try:
                content = core.read_intel_file(name)
            except (OSError, ValueError, FileNotFoundError):
                continue
            zf.writestr(f"intel/{name}", content)
        for b in _briefing_snippets(limit=10):
            zf.writestr(f"briefings/{b['name']}", b["preview"])
    return buf.getvalue()
