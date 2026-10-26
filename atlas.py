"""
MITRE ATLAS bridge for Illudium-Q36.

Uses a compact extract of MITRE ATLAS data (see data/atlas_bridge.json) to show
where ATT&CK and ATLAS techniques separate vs overlap — the same relationship
MITRE publishes via ATLAS attack-reference links.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRIDGE_PATH = ROOT / "data" / "atlas_bridge.json"

# Negative lookbehind avoids matching the T#### fragment inside AML.T####
ATTACK_ID_RE = re.compile(r"(?<!AML\.)\bT\d{4}(?:\.\d{3})?\b")
ATLAS_ID_RE = re.compile(r"\bAML\.(?:T|TA|M)\d{4}(?:\.\d{3})?\b")


@lru_cache(maxsize=1)
def load_bridge() -> dict:
    if not BRIDGE_PATH.exists():
        return {
            "tactics": [],
            "overlaps": [],
            "atlas_only_highlights": [],
            "notes": "ATLAS bridge data missing.",
        }
    return json.loads(BRIDGE_PATH.read_text(encoding="utf-8"))


def extract_attack_ids(text: str) -> list[str]:
    return sorted(set(ATTACK_ID_RE.findall(text or "")))


def extract_atlas_ids(text: str) -> list[str]:
    return sorted(set(ATLAS_ID_RE.findall(text or "")))


def _parent_attack_id(technique_id: str) -> str:
    return technique_id.split(".", 1)[0]


def analyze_overlap(
    attack_ids: list[str] | None = None,
    atlas_ids: list[str] | None = None,
    source_text: str | None = None,
) -> dict:
    """
    Classify techniques into ATT&CK-only, overlap, and ATLAS-only buckets.

    Overlap comes from MITRE ATLAS techniques that declare an attack-reference.
    """
    bridge = load_bridge()
    text = source_text or ""
    attack_ids = list(attack_ids or []) or extract_attack_ids(text)
    atlas_ids = list(atlas_ids or []) or extract_atlas_ids(text)

    attack_set = set(attack_ids)
    # Also match parent IDs (T1596.005 → T1596)
    attack_parents = {_parent_attack_id(a) for a in attack_set} | attack_set

    overlap_rows = []
    matched_attack = set()
    for row in bridge.get("overlaps") or []:
        aid = row.get("attack_id")
        if not aid:
            continue
        if aid in attack_parents or _parent_attack_id(aid) in attack_parents:
            overlap_rows.append(
                {
                    "attack_id": aid,
                    "atlas_id": row["id"],
                    "atlas_name": row["name"],
                    "tactics": row.get("tactics") or [],
                }
            )
            matched_attack.add(aid)
            matched_attack.add(_parent_attack_id(aid))

    attack_only = sorted(
        a for a in attack_ids if a not in matched_attack and _parent_attack_id(a) not in matched_attack
    )

    # ATLAS IDs cited in text that are not in the overlap set for this case
    overlap_atlas_ids = {r["atlas_id"] for r in overlap_rows}
    atlas_only_cited = [a for a in atlas_ids if a not in overlap_atlas_ids]

    # If no ATLAS IDs were cited but intel is AI-flavored, suggest highlights
    atlas_suggestions = []
    if not atlas_ids:
        blob = text.lower()
        ai_hints = (
            "llm",
            "machine learning",
            "ai model",
            "prompt injection",
            "model poison",
            "adversarial example",
            "rag ",
            "generative ai",
        )
        if any(h in blob for h in ai_hints):
            atlas_suggestions = (bridge.get("atlas_only_highlights") or [])[:8]

    return {
        "source": bridge.get("source", "MITRE ATLAS"),
        "source_url": bridge.get("source_url", "https://atlas.mitre.org/"),
        "data_version": bridge.get("data_version"),
        "notes": bridge.get("notes"),
        "attack_ids": attack_ids,
        "atlas_ids": atlas_ids,
        "overlap": overlap_rows,
        "attack_only": attack_only,
        "atlas_only": atlas_only_cited,
        "atlas_suggestions": [
            {"id": r["id"], "name": r["name"]} for r in atlas_suggestions
        ],
        "tactics": bridge.get("tactics") or [],
    }


def format_overlap_markdown(analysis: dict) -> str:
    """Human-readable ATT&CK ↔ ATLAS separation / overlap block."""
    lines = [
        "## MITRE ATLAS Mapping",
        (
            "ATLAS covers adversary behavior against AI systems. It is modelled on "
            "ATT&CK but is **not a subset** — many ATLAS techniques have no ATT&CK "
            f"equivalent ([ATLAS]({analysis.get('source_url')}))."
        ),
        "",
        "### Separation and overlap",
    ]

    overlap = analysis.get("overlap") or []
    if overlap:
        lines.append("**Overlap (ATT&CK technique cited by an ATLAS technique):**")
        for row in overlap:
            lines.append(
                f"- `{row['attack_id']}` ↔ `{row['atlas_id']}` — {row['atlas_name']}"
            )
    else:
        lines.append(
            "**Overlap:** None of the cited ATT&CK techniques currently map to an "
            "ATLAS technique via MITRE's published attack-reference links."
        )

    attack_only = analysis.get("attack_only") or []
    lines.append("")
    if attack_only:
        lines.append(
            "**ATT&CK-only (enterprise / infra — no ATLAS attack-reference match):** "
            + ", ".join(f"`{a}`" for a in attack_only)
        )
    else:
        lines.append("**ATT&CK-only:** None identified from the cited techniques.")

    atlas_only = analysis.get("atlas_only") or []
    suggestions = analysis.get("atlas_suggestions") or []
    lines.append("")
    if atlas_only:
        lines.append(
            "**ATLAS-only (AI-specific):** " + ", ".join(f"`{a}`" for a in atlas_only)
        )
    elif suggestions:
        lines.append(
            "**ATLAS-only (suggested for AI-relevant intel — not evidenced in corpus):**"
        )
        for s in suggestions:
            lines.append(f"- `{s['id']}` — {s['name']}")
    else:
        lines.append(
            "**ATLAS-only:** No AI-specific ATLAS techniques evidenced in the loaded intelligence."
        )

    if analysis.get("data_version"):
        lines.append("")
        lines.append(f"_ATLAS data version: {analysis['data_version']}_")

    return "\n".join(lines)


def ensure_atlas_section(
    briefing: str, analysis: dict | None = None
) -> tuple[str, dict]:
    """
    Attach a computed ATLAS separation/overlap section when the briefing lacks one.
    Always returns structured analysis for the UI.
    """
    analysis = analysis or analyze_overlap(source_text=briefing)
    if re.search(r"^##\s+MITRE ATLAS Mapping\s*$", briefing, flags=re.MULTILINE):
        return briefing, analysis

    block = format_overlap_markdown(analysis)
    # Insert after ATT&CK section when present; else before Remediation / at end
    attack_match = re.search(
        r"(##\s+MITRE ATT&CK Mapping\b.*?)(?=\n##\s+|\Z)",
        briefing,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if attack_match:
        insert_at = attack_match.end()
        briefing = briefing[:insert_at].rstrip() + "\n\n" + block + "\n\n" + briefing[insert_at:].lstrip()
    else:
        rem = re.search(r"^##\s+Recommended Remediation Steps\b", briefing, flags=re.MULTILINE)
        if rem:
            briefing = briefing[: rem.start()] + block + "\n\n" + briefing[rem.start() :]
        else:
            briefing = briefing.rstrip() + "\n\n" + block + "\n"

    return briefing, analysis
