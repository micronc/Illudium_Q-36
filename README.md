# Illudium-Q36 Intelligence Briefing Generator

GitHub repository: **`Illudium_Q-36_dev`**

A Python tool that transforms raw threat intelligence — CVEs, IOC dumps, threat reports — into structured executive briefings using the Anthropic Claude API.

Built as a practical demonstration of Claude API integration for enterprise security workflows.

---

## What It Does

Ingest **multiple intel sources** (files and pasted text), then:

- **Generate executive briefings** — attribution, MITRE ATT&CK + **ATLAS** (separation & overlap), remediation with **level of effort**, **risk if no action**, summary
- **Chat with the model** (text) about the loaded corpus — CLI or web console; **choose Claude model**; see **token + $ cost**
- **Create report** download as **Markdown / PDF / Word**
- **Mermaid diagrams** for Cisco-style networks, OS/connectivity relationships, application call sequences, and privilege-escalation paths (lower → TTP → higher)
- **Voice chat** in the web console — speak questions; optional spoken replies (browser Web Speech API)

Output briefings are saved as timestamped Markdown files in `briefings/`.

Uploaded / imported intel lives in `intel/`. Starter demo packs (always `git pull origin main` first):
- Generic: [`samples/intel/`](samples/intel/) · [`samples/illudium-sample-intel.zip`](samples/illudium-sample-intel.zip)
- Neo4j / GraphRAG: [`samples/intel-neo4j/`](samples/intel-neo4j/) · [`samples/illudium-sample-intel-neo4j.zip`](samples/illudium-sample-intel-neo4j.zip)

---

## What It Does NOT Do

- **Your taxes**
- **Tell you what in your network is impacted** — The assumption is that you know what this connects to and are not trying to leak that to the world.

---

## Setup

**Requirements**

- Python 3.8+
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))
- For voice: Chrome/Edge (built-in recognition), or Firefox with `OPENAI_API_KEY` / `GROQ_API_KEY` (Whisper record fallback)

**Install**

```bash
pip install -r requirements.txt
```

**Demo mode** (sample chat + briefing without an API key — useful for walkthroughs):

```bash
DEMO_MODE=true python app.py
```

**Docker Compose** (local env build; same image for AWS + GCP):

```bash
# Demo without an API key
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build

# With secrets from .env
cp .env.example .env   # set ANTHROPIC_API_KEY and FLASK_SECRET_KEY
docker compose up --build
```

Open http://127.0.0.1:8080 — day-to-day steps: [`docs/QUICK_START.md`](docs/QUICK_START.md). More detail: [`docs/DOCKER.md`](docs/DOCKER.md), [`deploy/aws/`](deploy/aws/), [`deploy/gcp/`](deploy/gcp/).

On first load the console asks for a **session storage limit (MB)**. When that limit is hit you get a detailed capture plus a **continuation prompt** (save as markdown) or a **compressed session export** (`.zip`). Scaled Docker uses sticky affinity (`docker-compose.sticky.yml`; `WEB_CONCURRENCY=1` per replica).

For HashiCorp Vault support (recommended for production):

```bash
pip install hvac
```

**Secrets**

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY=…`. Never commit `.env`.

Or use Vault (`USE_VAULT=true`, `VAULT_ADDR`, `VAULT_TOKEN`) — see `claud_prompt.md` for a local Vault walkthrough. On AWS/GCP, inject the same variables from Secrets Manager / Secret Manager instead of a file.

---

## Usage

### Analyst console (multi-file + text + voice)

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

1. Drop or select multiple intel files (or paste text into the corpus)
2. Toggle which files are in context via the checkboxes
3. Chat in the right panel — Enter to send
4. Click the mic for voice input; enable **Speak replies** for TTS
5. **Generate briefing** for the structured executive deliverable
6. Optional: enable **508 Mode** for high contrast / larger text (VPAT-oriented)
7. Blind researchers: open **Keyboard help** (or `Alt+/`) for read-aloud shortcuts — see [`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md)

### Blind / screen-reader researchers

Use a screen reader (NVDA, JAWS, or VoiceOver) plus:

| Shortcut | Action |
|----------|--------|
| `Alt+R` | Read last answer / briefing aloud |
| `Alt+A` | Read ATT&CK ↔ ATLAS panel aloud |
| `Alt+I` | Read intel file list aloud |
| `Alt+S` / `Esc` | Stop speech |
| `Alt+1` / `2` / `3` | Jump to intel / chat / message box |
| `Alt+/` | Keyboard help |

Full setup notes: [`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md).

### CLI — one-shot briefing

```bash
python illudium-q36.py
```

Paste intel, then type `END` on its own line.

### CLI — multiple files

Place files in `./intel`, or pass paths (external files are imported into `intel/`):

```bash
python illudium-q36.py --files report.md iocs.csv notes.txt
python illudium-q36.py -f intel/report.md --paste   # files + stdin paste
python illudium-q36.py --list
```

### CLI — text chat

```bash
python illudium-q36.py --chat
python illudium-q36.py --chat --files report.md
```

Commands inside chat: `/files`, `/briefing`, `/quit`.

---

## Architecture

```
Intel files (./intel) + optional paste
              |
              v
     core.load_intel_corpus()
              |
      +-------+--------+
      |                |
      v                v
  Briefing API     Analyst chat API
  (structured)     (multi-turn)
      |                |
      v                v
  ./briefings/     CLI  |  Flask UI (+ voice in browser)
```

| Piece | Role |
|--------|------|
| `core.py` | Shared secrets, corpus loading, Claude briefing & chat |
| `atlas.py` + `data/atlas_bridge.json` | MITRE ATLAS bridge — ATT&CK overlap / separation |
| `illudium-q36.py` | CLI briefing + text chat |
| `app.py` | Flask analyst console |
| `templates/` + `static/` | Multi-file UI, ATLAS panel, text/voice composer |
| `Dockerfile` + `docker-compose.yml` | Container env build (Gunicorn on :8080) |
| `deploy/aws`, `deploy/gcp` | ECR/ECS and Artifact Registry/Cloud Run sketches |

Briefings include **Recommended Remediation Steps** (each with level of effort), **Risk If No Action Is Taken**, and an **ATT&CK ↔ ATLAS** view derived from [MITRE ATLAS](https://atlas.mitre.org/) attack-reference links.

Spoken replies (TTS) run in the browser. Voice *input* uses the browser Web Speech API on Chrome/Edge; on Firefox it records audio and transcribes with Whisper (`OPENAI_API_KEY` or `GROQ_API_KEY`). Chat and briefings still use your Anthropic key.

---

## Extending This

- Connect input to a SIEM API (Splunk, Elastic)
- Integrate with a ticketing system to auto-create incidents
- Add a vector database to compare new threats against historical briefings (RAG)

---

## Author

Built to demonstrate practical Claude API integration for enterprise security use cases. Treat secrets carefully; this is a lab / learning tool unless you harden it for your environment.

---

## License

MIT
