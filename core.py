"""
Shared Illudium-Q36 logic: secrets, prompts, Claude calls, intel loading.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Iterable

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
INTEL_DIR = ROOT / "intel"
BRIEFINGS_DIR = ROOT / "briefings"

ALLOWED_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".ioc",
    ".stix",
}

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB per file
MAX_TOTAL_CONTEXT_CHARS = 120_000

BRIEFING_SYSTEM_PROMPT = """You are the one that is presenting the executive briefings.
They know how to run businesses, they want to know what the impact to the business could be.
This means how much money what you are telling them about will be lost, or need to be spent
on fixing the issue.

Be concise, precise, and actionable. Avoid jargon without explanation.

Structure every briefing exactly as follows:

## Threat Actor / Attribution
Who is behind this?
Known group, nation-state, or unknown?
Confidence level?
-- EG: This is Mustang Panda, a Chinese Advance Persisten Threat. We can say with a High Degree of Confidence
the potential impact to the business is $100,000.00 at this time. The steps I am going  to outline will mitigate
the costs and fix (remediate) the known compromised systems. If nothing it it will cost the business more money.
-- EG Cont.: Now that you know the buttom line up front, the following will explain the technical who's and hows.

## MITRE ATT&CK Mapping
List relevant ATT&CK Tactics and Techniques with IDs (e.g., T1566 - Phishing).
Map only what is supported by the provided intelligence.

## MITRE ATLAS Mapping
Map AI/ML-relevant adversary behavior using MITRE ATLAS IDs (AML.T#### / AML.TA####) when the
intelligence supports it. Explicitly call out:
- Overlap: techniques that exist in both ATT&CK and ATLAS (or ATLAS entries that cite ATT&CK)
- ATT&CK-only: enterprise/infra behaviors with no ATLAS equivalent in the intel
- ATLAS-only: AI-specific behaviors with no ATT&CK equivalent
If the intel has no AI/ML dimension, state that clearly and keep ATT&CK as the primary mapping.

## Recommended Remediation Steps
Prioritised, actionable steps. Lead with the highest impact actions.
Flag anything requiring immediate attention.
For **each** remediation recommendation include:
- The action
- **Level of effort:** Low / Medium / High, with a rough estimate (e.g., hours, person-days)
- Owner hint when obvious (SOC, IT, IR, AppSec, ML Eng)

## Risk If No Action Is Taken
Describe concrete business, operational, and financial risk if leadership defers remediation.
Include likely near-term cost/impact range when the intel supports an estimate, and what gets worse
with delay (credential theft, lateral movement, regulatory exposure, model integrity, etc.).

## Executive Summary
Two to three sentences maximum. What happened, who is at risk, and what must be done now.

## Diagrams (when supported by the intel)
When the intelligence describes networks, OS/connectivity changes, application calls,
or privilege escalation, include one or more fenced Mermaid diagrams:

1. **Networks** — Mermaid `flowchart` using Cisco-style device roles as node labels
   (e.g. `Internet`, `Edge Firewall`, `Core Router`, `L3 Switch`, `Access Switch`,
   `ASA/FTD`, `WLC`, `Endpoint`, `DC Server`). Show zones and trust boundaries.
2. **OS change / connectivity relationships** — Mermaid `flowchart` of hosts, OS
   versions or change events, and connectivity edges (what talks to what).
3. **Application calls / communication** — Mermaid `sequenceDiagram` (client → API →
   service → data store) with protocols when known.
4. **Privilege escalation** — Mermaid `flowchart LR` with nodes for the **lower
   privilege account**, the **higher privilege account**, and an edge labeled with
   the **TTP / ATT&CK technique** used to escalate (e.g. `T1078`, `T1548`).

Use ```mermaid fenced blocks. Only diagram what the intel supports; label uncertainty.
"""

ANALYST_SYSTEM_PROMPT = """You are Illudium-Q36, an analyst assistant for enterprise security teams.
You help analysts explore uploaded threat intelligence (CVEs, IOC dumps, reports, notes).

Rules:
- Ground answers in the provided intel corpus. If something is not supported by the intel, say so.
- Prefer concrete IOCs, ATT&CK IDs, ATLAS IDs (when AI/ML-relevant), timelines, and remediation when the intel supports them.
- Be concise and operational. Flag uncertainty and confidence clearly.
- When asked for an executive briefing, use the standard Illudium-Q36 briefing structure:
  Threat Actor / Attribution, MITRE ATT&CK Mapping, MITRE ATLAS Mapping (separation & overlap),
  Recommended Remediation Steps (with level of effort per item), Risk If No Action Is Taken,
  Executive Summary, plus Mermaid diagrams when applicable.
- Diagrams (use ```mermaid fences when intel supports them):
  * Networks → flowchart with Cisco-style roles (Router, L3 Switch, Firewall/ASA, Access Switch, etc.)
  * OS change / connectivity → flowchart of hosts/changes and relationships
  * Application calls → sequenceDiagram for request/response paths
  * Privilege escalation → flowchart showing lower account → TTP/ATT&CK ID → higher account
- Do not invent attribution or IOCs that are not in the corpus.
"""

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6")
DIAGRAM_HINT = """

When relevant, include Mermaid diagrams (```mermaid) for: Cisco-style network topology,
OS/connectivity relationship changes, application call sequences, and privilege-escalation
paths (lower account → TTP → higher account).
"""


def demo_mode_enabled() -> bool:
    """When true, return realistic sample briefing/chat without calling Claude."""
    return os.environ.get("DEMO_MODE", "false").lower() in {"1", "true", "yes"}


SAMPLE_BRIEFING = """## Threat Actor / Attribution
Suspected **APT29** (nation-state aligned), based on tooling overlap called out in the loaded reporting. Confidence: **medium**.
Bottom line: if this campaign reaches domain-joined workstations unchecked, expected near-term response and containment spend is roughly **$75,000–$150,000** (IR retainers, overtime, endpoint rebuilds). Leaving known C2 reachability unblocked raises the odds of credential theft and lateral movement that push impact higher.

## MITRE ATT&CK Mapping
- **T1566** — Phishing (spearphishing / helpdesk-spoofed credential prompts)
- **T1021.002** — Remote Services: SMB/Windows Admin Shares (lateral movement)
- **T1003** — OS Credential Dumping (Mimikatz observed in reporting)
- **T1071** — Application Layer Protocol (C2 beaconing to listed infrastructure)

## MITRE ATLAS Mapping
ATLAS is modelled on ATT&CK but is not a subset — use both to see where enterprise and AI-adversary behaviors separate vs overlap ([ATLAS](https://atlas.mitre.org/)).
- **Overlap (ATT&CK technique cited by an ATLAS technique):**
  - `T1566` ↔ `AML.T0052` — Phishing
  - `T1003` ↔ `AML.T0090` — OS Credential Dumping
- **ATT&CK-only:** `T1021.002`, `T1071` (SMB lateral movement, application-layer C2) — no ATLAS attack-reference match in current bridge data
- **ATLAS-only:** None evidenced in this corpus. If ML-based detection or LLM assistants are in the blast radius, revisit ATLAS after containment for AI-specific follow-on (e.g., ML detector evasion).

## Recommended Remediation Steps
1. **Block egress IOCs** — Block `185.234.10.22` and `update-cdn.evil.example`; hunt 14 days of DNS/HTTP telemetry.
   - **Level of effort:** Low (~2–4 SOC hours)
   - **Owner:** SOC / Network
2. **Contain compromised hosts** — Quarantine/rebuild hosts with hash `a1b2c3d4e5f6789012345678901234567890abcd` or Mimikatz artifacts; reset affected credentials.
   - **Level of effort:** Medium (~1–3 person-days, scales with host count)
   - **Owner:** IR / Desktop Eng
3. **User channel hard-stop** — Alert on spoofed IT/helpdesk credential prompts; force MFA re-registration where prompts were followed.
   - **Level of effort:** Low–Medium (~4–8 hours + helpdesk surge)
   - **Owner:** IT / Identity
4. **Patch Apache 2.4.x (CVE-2024-1234)** — Upgrade internet-facing instances; WAF/virtual patch where upgrade is delayed.
   - **Level of effort:** Medium–High (~2–5 person-days including change windows)
   - **Owner:** App / Platform Eng

## Risk If No Action Is Taken
Deferred blocking leaves active C2 and payload infrastructure reachable, raising likelihood of continued credential theft and SMB lateral movement across domain-joined workstations. Near-term reactive IR cost already estimated at **$75,000–$150,000**; delay commonly expands that through additional host rebuilds, broader password resets, and business downtime. Unpatched Apache 2.4.x keeps an independent RCE path open even if phishing is contained.

## Executive Summary
A medium-confidence APT29-linked campaign combines phishing, SMB lateral movement, and credential dumping with active C2 infrastructure already listed in the corpus. Block the IOCs now, contain any beaconing hosts, and remediate exposed Apache 2.4.x to limit business impact.
"""

SAMPLE_DIAGRAMS = """
## Diagrams

### Network topology (Cisco-style roles)
```mermaid
flowchart TB
  subgraph Untrusted["Untrusted"]
    Internet((Internet))
    C2["C2 185.234.10.22"]
  end
  subgraph Edge["Edge / Perimeter"]
    FW["Edge Firewall / ASA-FTD"]
    WAF["WAF"]
  end
  subgraph Campus["Campus"]
    RTR["Core Router"]
    L3["L3 Switch"]
    ACC["Access Switch"]
    WLC["WLC"]
  end
  subgraph Endpoints["Endpoints"]
    EP["Endpoint FINANCE-WS03"]
  end
  subgraph DC["Data Center"]
    DCSRV["DC Server / AD"]
    WEB["Apache 2.4.x"]
  end
  Internet --> FW
  C2 -.->|beacon T1071| EP
  FW --> RTR
  FW --> WAF
  WAF --> WEB
  RTR --> L3
  L3 --> ACC
  L3 --> WLC
  ACC --> EP
  RTR --> DCSRV
```

### OS change / connectivity relationships
```mermaid
flowchart LR
  WS03["FINANCE-WS03\\nWin10 22H2"] -->|"SMB T1021.002"| FILE["File Server\\nWin Server 2019"]
  WS03 -->|"LDAP / Kerberos"| AD["Domain Controller\\nWin Server 2022"]
  WS03 -->|"HTTPS C2"| C2H["update-cdn.evil.example"]
  WEB2["Internet Apache 2.4.x\\nCVE-2024-1234"] -->|"change: unpatched"| RISK["Exposed RCE path"]
```

### Application calls / communication
```mermaid
sequenceDiagram
  participant User as User browser
  participant Help as Spoofed helpdesk page
  participant EP as FINANCE-WS03
  participant C2 as C2 185.234.10.22
  participant AD as Domain Controller
  User->>Help: Credential prompt (phishing)
  Help->>EP: Dropper delivered
  EP->>C2: HTTPS beacon (T1071)
  EP->>AD: Auth with stolen creds
  AD-->>EP: Ticket / session
```

### Privilege escalation
```mermaid
flowchart LR
  Low["Lower: finance.user\\n(standard domain user)"]
  High["Higher: Domain Admin\\n(DA group)"]
  Low -->|"T1003 OS Credential Dumping\\n(Mimikatz)"| High
  Low -->|"T1078 Valid Accounts"| High
```
"""


def sample_chat_reply(user_message: str, intel_corpus: str) -> str:
    """Deterministic demo reply grounded in the sample campaign intel."""
    lower = user_message.lower()
    if any(k in lower for k in ("ioc", "indicator", "hash", "ip", "domain")):
        return (
            "From the loaded corpus, the concrete IOCs are:\n\n"
            "- **IP:** `185.234.10.22` — noted as C2 beacon\n"
            "- **Domain:** `update-cdn.evil.example` — payload host\n"
            "- **SHA256:** `a1b2c3d4e5f6789012345678901234567890abcd` — dropper\n\n"
            "Reporting also ties this to spearphishing / helpdesk-spoofed prompts, "
            "SMB lateral movement, and Mimikatz credential dumping. "
            "I can turn this into a full executive briefing when you are ready."
        )
    if "brief" in lower or "executive" in lower:
        return SAMPLE_BRIEFING + SAMPLE_DIAGRAMS
    if any(
        k in lower
        for k in (
            "diagram",
            "mermaid",
            "network",
            "topology",
            "cisco",
            "priv",
            "escalat",
            "connectivity",
            "sequence",
            "application call",
        )
    ):
        return (
            "Here are Mermaid diagrams grounded in the loaded demo intel "
            "(Cisco-style network roles, OS/connectivity, app calls, and privilege escalation):\n"
            + SAMPLE_DIAGRAMS
        )
    if "atlas" in lower or "overlap" in lower:
        return (
            "MITRE **ATLAS** (Adversarial Threat Landscape for AI Systems) is modelled on "
            "ATT&CK but is not a subset of it. Illudium-Q36 uses MITRE's published "
            "attack-reference links to show **overlap**, **ATT&CK-only**, and **ATLAS-only** "
            "techniques in the executive briefing.\n\n"
            "Use **Generate briefing** to see remediation steps with **level of effort**, "
            "plus **Risk If No Action Is Taken**."
        )
    loaded = "yes" if intel_corpus.strip() else "no"
    return (
        "I am running in demo mode (no live model call). "
        f"Intel corpus loaded: **{loaded}**. "
        "Ask about IOCs or ATLAS overlap, or use **Generate briefing** for the full report."
    )


def get_api_key() -> str:
    use_vault = os.environ.get("USE_VAULT", "false").lower() == "true"

    if use_vault:
        try:
            import hvac

            vault_addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
            vault_token = os.environ.get("VAULT_TOKEN")

            if not vault_token:
                raise ValueError("VAULT_TOKEN environment variable not set.")

            client = hvac.Client(url=vault_addr, token=vault_token)

            if not client.is_authenticated():
                raise ValueError("Vault authentication failed. Check your VAULT_TOKEN.")

            secret = client.secrets.kv.v2.read_secret_version(
                path="anthropic/api_key",
                mount_point="secret",
            )
            api_key = secret["data"]["data"]["value"]
            print("API key loaded from HashiCorp Vault.")
            return api_key

        except ImportError as exc:
            raise ImportError("hvac not installed. Run: pip install hvac") from exc
        except Exception as e:
            raise RuntimeError(f"Vault error: {e}") from e

    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not found. Check your .env file or set USE_VAULT=true."
        )
    print("API key loaded from .env file.")
    return api_key


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_api_key())


def ensure_dirs() -> None:
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def safe_intel_path(filename: str) -> Path:
    """Resolve a filename under INTEL_DIR; reject path traversal."""
    ensure_dirs()
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename.")
    path = (INTEL_DIR / name).resolve()
    if path.parent != INTEL_DIR.resolve():
        raise ValueError("Invalid filename.")
    return path


def list_intel_files() -> list[dict]:
    ensure_dirs()
    files = []
    for path in sorted(INTEL_DIR.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file() and path.name != ".gitkeep":
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified": datetime.datetime.fromtimestamp(
                        path.stat().st_mtime
                    ).isoformat(timespec="seconds"),
                }
            )
    return files


def read_intel_file(filename: str) -> str:
    path = safe_intel_path(filename)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Intel file not found: {filename}")
    return path.read_text(encoding="utf-8", errors="replace")


def save_intel_bytes(filename: str, data: bytes) -> str:
    if not allowed_file(filename):
        raise ValueError(
            f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")

    path = safe_intel_path(filename)
    # Avoid clobbering: append counter if needed
    if path.exists():
        stem, suffix = path.stem, path.suffix
        n = 1
        while True:
            candidate = INTEL_DIR / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                path = candidate
                break
            n += 1

    path.write_bytes(data)
    return path.name


def save_intel_text(filename: str, text: str) -> str:
    return save_intel_bytes(filename, text.encode("utf-8"))


def delete_intel_file(filename: str) -> None:
    path = safe_intel_path(filename)
    if path.exists() and path.is_file():
        path.unlink()


def load_intel_corpus(
    filenames: Iterable[str] | None = None,
    extra_text: str | None = None,
) -> str:
    """Load selected (or all) intel files into a single context block."""
    ensure_dirs()
    selected = list(filenames) if filenames is not None else [
        f["name"] for f in list_intel_files()
    ]

    parts: list[str] = []
    total = 0

    for name in selected:
        try:
            content = read_intel_file(name)
        except (FileNotFoundError, ValueError):
            continue
        block = f"===== INTEL FILE: {name} =====\n{content.strip()}\n"
        if total + len(block) > MAX_TOTAL_CONTEXT_CHARS:
            remaining = MAX_TOTAL_CONTEXT_CHARS - total
            if remaining > 200:
                parts.append(block[:remaining] + "\n...[truncated]")
            break
        parts.append(block)
        total += len(block)

    if extra_text and extra_text.strip():
        block = f"===== PASTED INTEL =====\n{extra_text.strip()}\n"
        if total + len(block) <= MAX_TOTAL_CONTEXT_CHARS:
            parts.append(block)
        else:
            remaining = MAX_TOTAL_CONTEXT_CHARS - total
            if remaining > 200:
                parts.append(block[:remaining] + "\n...[truncated]")

    if not parts:
        return ""

    return "\n".join(parts)


def generate_briefing(raw_intel: str, model: str | None = None) -> str:
    briefing, _analysis, _usage = generate_briefing_with_atlas(raw_intel, model=model)
    return briefing


def generate_briefing_with_atlas(
    raw_intel: str, model: str | None = None
) -> tuple[str, dict, dict]:
    """Produce briefing text, ATLAS analysis, and token/cost usage."""
    import atlas
    import pricing

    model_id = pricing.resolve_model(model, DEFAULT_MODEL)
    usage = pricing.empty_usage(model_id)

    if demo_mode_enabled():
        briefing = SAMPLE_BRIEFING + SAMPLE_DIAGRAMS
        usage = {
            **usage,
            "input_tokens": 1200,
            "output_tokens": 900,
            "total_tokens": 2100,
            "cost_usd": round(
                pricing.estimate_cost_usd(model_id, 1200, 900), 6
            ),
        }
    else:
        client = get_client()
        message = client.messages.create(
            model=model_id,
            max_tokens=4096,
            system=BRIEFING_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following threat intelligence and produce a "
                        "structured executive briefing, including Mermaid diagrams "
                        "when networks, OS/connectivity changes, application calls, "
                        "or privilege escalation appear in the intel.\n\n"
                        f"{raw_intel}"
                    ),
                }
            ],
        )
        briefing = message.content[0].text
        usage = pricing.usage_from_response(model_id, message)

    analysis = atlas.analyze_overlap(source_text=f"{briefing}\n{raw_intel}")
    briefing, analysis = atlas.ensure_atlas_section(
        briefing, analysis=analysis
    )
    return briefing, analysis, usage


def chat_with_intel(
    user_message: str,
    history: list[dict],
    intel_corpus: str,
    model: str | None = None,
) -> tuple[str, list[dict], dict]:
    """
    Multi-turn analyst chat grounded in the intel corpus.
    Returns (assistant_text, updated_history, usage).
    """
    import pricing

    model_id = pricing.resolve_model(model, DEFAULT_MODEL)
    messages = list(history) + [{"role": "user", "content": user_message}]
    if len(messages) > 40:
        messages = messages[-40:]

    if demo_mode_enabled():
        assistant_text = sample_chat_reply(user_message, intel_corpus)
        updated = messages + [{"role": "assistant", "content": assistant_text}]
        usage = {
            **pricing.empty_usage(model_id),
            "input_tokens": 400,
            "output_tokens": 350,
            "total_tokens": 750,
            "cost_usd": round(pricing.estimate_cost_usd(model_id, 400, 350), 6),
        }
        return assistant_text, updated, usage

    client = get_client()

    system = ANALYST_SYSTEM_PROMPT + DIAGRAM_HINT
    if intel_corpus.strip():
        system += (
            "\n\n--- LOADED THREAT INTELLIGENCE CORPUS ---\n"
            f"{intel_corpus}\n"
            "--- END CORPUS ---"
        )
    else:
        system += (
            "\n\nNo intel files are currently loaded. Ask the analyst to upload "
            "or paste intelligence before drawing firm conclusions."
        )

    response = client.messages.create(
        model=model_id,
        max_tokens=4096,
        system=system,
        messages=messages,
    )
    assistant_text = response.content[0].text
    updated = messages + [{"role": "assistant", "content": assistant_text}]
    usage = pricing.usage_from_response(model_id, response)
    return assistant_text, updated, usage


def _chunk_text(text: str, size: int = 24):
    """Yield small string chunks (demo / progressive UI)."""
    if not text:
        return
    for i in range(0, len(text), size):
        yield text[i : i + size]


def iter_chat_tokens(
    user_message: str,
    history: list[dict],
    intel_corpus: str,
    model: str | None = None,
):
    """
    Yield ("token", text_fragment) then ("done", assistant_text, updated_history, usage).
    """
    import time

    import pricing

    model_id = pricing.resolve_model(model, DEFAULT_MODEL)
    messages = list(history) + [{"role": "user", "content": user_message}]
    if len(messages) > 40:
        messages = messages[-40:]

    if demo_mode_enabled():
        assistant_text = sample_chat_reply(user_message, intel_corpus)
        for piece in _chunk_text(assistant_text, 28):
            yield ("token", piece)
            time.sleep(0.012)
        updated = messages + [{"role": "assistant", "content": assistant_text}]
        usage = {
            **pricing.empty_usage(model_id),
            "input_tokens": 400,
            "output_tokens": 350,
            "total_tokens": 750,
            "cost_usd": round(pricing.estimate_cost_usd(model_id, 400, 350), 6),
        }
        yield ("done", assistant_text, updated, usage)
        return

    client = get_client()
    system = ANALYST_SYSTEM_PROMPT + DIAGRAM_HINT
    if intel_corpus.strip():
        system += (
            "\n\n--- LOADED THREAT INTELLIGENCE CORPUS ---\n"
            f"{intel_corpus}\n"
            "--- END CORPUS ---"
        )
    else:
        system += (
            "\n\nNo intel files are currently loaded. Ask the analyst to upload "
            "or paste intelligence before drawing firm conclusions."
        )

    parts: list[str] = []
    with client.messages.stream(
        model=model_id,
        max_tokens=4096,
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            if not text:
                continue
            parts.append(text)
            yield ("token", text)
        final = stream.get_final_message()

    assistant_text = "".join(parts) or (
        final.content[0].text if final and final.content else ""
    )
    updated = messages + [{"role": "assistant", "content": assistant_text}]
    usage = pricing.usage_from_response(model_id, final) if final else pricing.empty_usage(model_id)
    yield ("done", assistant_text, updated, usage)


def iter_briefing_tokens(raw_intel: str, model: str | None = None):
    """
    Yield ("token", text) then ("done", briefing, atlas_analysis, usage).
    ATLAS section is applied after the model stream completes.
    """
    import time

    import atlas
    import pricing

    model_id = pricing.resolve_model(model, DEFAULT_MODEL)

    if demo_mode_enabled():
        briefing = SAMPLE_BRIEFING + SAMPLE_DIAGRAMS
        for piece in _chunk_text(briefing, 36):
            yield ("token", piece)
            time.sleep(0.01)
        usage = {
            **pricing.empty_usage(model_id),
            "input_tokens": 1200,
            "output_tokens": 900,
            "total_tokens": 2100,
            "cost_usd": round(pricing.estimate_cost_usd(model_id, 1200, 900), 6),
        }
        analysis = atlas.analyze_overlap(source_text=f"{briefing}\n{raw_intel}")
        briefing, analysis = atlas.ensure_atlas_section(briefing, analysis=analysis)
        yield ("done", briefing, analysis, usage)
        return

    client = get_client()
    parts: list[str] = []
    with client.messages.stream(
        model=model_id,
        max_tokens=4096,
        system=BRIEFING_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Analyze the following threat intelligence and produce a "
                    "structured executive briefing, including Mermaid diagrams "
                    "when networks, OS/connectivity changes, application calls, "
                    "or privilege escalation appear in the intel.\n\n"
                    f"{raw_intel}"
                ),
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            if not text:
                continue
            parts.append(text)
            yield ("token", text)
        final = stream.get_final_message()

    briefing = "".join(parts) or (
        final.content[0].text if final and final.content else ""
    )
    usage = (
        pricing.usage_from_response(model_id, final)
        if final
        else pricing.empty_usage(model_id)
    )
    analysis = atlas.analyze_overlap(source_text=f"{briefing}\n{raw_intel}")
    briefing, analysis = atlas.ensure_atlas_section(briefing, analysis=analysis)
    yield ("done", briefing, analysis, usage)


def save_briefing(briefing: str) -> str:
    ensure_dirs()
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = BRIEFINGS_DIR / f"briefing_{timestamp}.md"

    header = (
        "# Illudium-Q36 Intelligence Briefing\n"
        f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "---\n\n"
    )

    filename.write_text(header + briefing, encoding="utf-8")
    return str(filename.relative_to(ROOT))
