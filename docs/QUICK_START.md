# Quick Start & Restart

Four things. That’s it.

---

## 0. Pull the update (do this first)

New files and fixes are **not** on your PC until you pull.

From your clone root:

```bash
cd ..../Illumidium_Q36
git checkout main
git pull origin main
```

After that pull, sample intel packs are here:

| Pack | Folder | Zip |
|------|--------|-----|
| Generic APT/Apache demo | `..../samples/intel/` | `..../samples/illudium-sample-intel.zip` |
| **Neo4j / GraphRAG demo** | `..../samples/intel-neo4j/` | `..../samples/illudium-sample-intel-neo4j.zip` |

Or download the Neo4j zip from GitHub (sign in if private):  
`..../raw/main/samples/illudium-sample-intel-neo4j.zip` on the private repo.

Upload the **seven intel files** into the console (skip any `README` / `LOAD_THESE` notes).  
Uploads are scanned for IPs / PII and replaced with sequential placeholders.

**Prompt that should name Neo4j** (use with the Neo4j pack):

> Based only on the loaded intel, what product or platform do these artifacts belong to, and what evidence shows they are Neo4j-related?

---

## 1. Update the `.env`

From the repo root:

```bash
cp .env.example .env
```

Edit `.env`:

- **LLM API key** — set `ANTHROPIC_API_KEY=sk-ant-…`
- **Session secret** — set `FLASK_SECRET_KEY=` to a long random string  
  (example: `openssl rand -hex 32`)
- **Uncomment what you want to use**, for example:
  - `# DEMO_MODE=true` — only if you want sample replies (no live API)
  - `# ANTHROPIC_MODEL=…` — optional model override
  - `# API_BUDGET_USD=25` — optional USD allocated to this key
  - `# HOST_PORT=8081` — only if 8080 is already taken
  - `# OPENAI_API_KEY=` / `# GROQ_API_KEY=` — Firefox voice (Whisper)
  - Vault lines (`USE_VAULT`, `VAULT_ADDR`, `VAULT_TOKEN`) — only for Vault
- **Save** the file  
  Do **not** commit `.env`.

---

## 2. Get Docker started

Always detached (`-d`). Pull first if you have not already:

```bash
git checkout main
git pull origin main

export BUILD_SHA=$(git rev-parse --short HEAD)
sudo BUILD_SHA=$BUILD_SHA docker compose up --build -d
```

Open: **http://127.0.0.1:8080**  
Health check: **http://127.0.0.1:8080/healthz**

Useful:

```bash
sudo docker compose ps
sudo docker compose logs -f
```

This is the step you’ll use most days — start or rebuild while keeping volumes (intel + briefings).

---

## 3. Kill Docker and get a fresh instance

Full purge (containers, volumes, local image), then a clean start — always detached:

```bash
sudo docker compose down -v --rmi local

git pull origin main
export BUILD_SHA=$(git rev-parse --short HEAD)
sudo BUILD_SHA=$BUILD_SHA docker compose up --build -d
```

That removes uploaded intel, briefings, and the spend ledger volume data for this project, then builds a new container.

---

That’s the whole loop: **pull → configure `.env` → `up -d` → (when needed) purge and `up -d` again**.
