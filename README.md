# DAVE — SageWire Vet AI Service

**DAVE** = *Don't Always Visit the Emergency vet.*
A cattle-health field assistant. Reads live animal records from a Google
Sheet, searches a veterinary knowledge base (180+ references), and answers
in plain language for working ranchers.

Built to the **SageWire Service Creation Playbook** standard.

---

## What DAVE is (and isn't)

- **A SERVICE.** Products (like the DAVE chat UI `vet.html`, or HerdMate)
  depend on DAVE. DAVE never depends on a product.
- DAVE **owns its vet knowledge** (vet docs + conversation memory) — that's
  Dave's brain, specific to being a vet.
- DAVE **reads** the operation's Google Sheet for animal records.
- DAVE **may call** the general-purpose RAG service (`RAG_SERVICE_URL`) for
  non-vet documents (manuals, SOPs, labels). Service→service is allowed.

---

## Service facts (Server Atlas)

```
SERVICE:  DAVE
DOMAIN:   https://dave.sagewire.dev
SERVER:   159.203.129.179
PORT:     5005
REPO:     voidwalker-sagewire/sagewire-dave-service
HEALTH:   https://dave.sagewire.dev/health
STATUS:   (set when deployed)
```

---

## Endpoints

- `GET  /health` — SageWire-standard health check.
- `GET  /vet/health` — legacy health (kept for the existing frontend).
- `GET  /vet/status` — knowledge-base + memory doc counts.
- `POST /vet/ask` — main endpoint. Ask a question about an animal.

### `/vet/ask` request (JSON)
```json
{
  "question": "what's going on with 765?",
  "tag_epc": "765",
  "herdmate_sheet_id": "1ziqvEJRYmqf4IvYLa4Ij3z4I-swln4nAMJI6gLlzlGI",
  "operation": "DCC",
  "pasture": "North",
  "weather": "hot",
  "image_base64": null,
  "image_type": "image/jpeg",
  "conversation_history": []
}
```

---

## How DAVE reads the sheet (v4 — dynamic)

DAVE does **not** hardcode column names. It searches the configured
per-animal tabs, matches the tag against any identifier-looking column, and
hands the AI the **entire matching row** (all columns, whatever they're named
today). Rename/add/move columns in the sheet — no code change needed.

Searchable tabs: `Ranch Tracker`, `Calf Tracker`, `Palpation Log`,
`ScrotalExamLog`, `WatchList`, `Bovine Beacon`, `Animals`.

Duplicate tags (reused across years) are ranked: primary animal records
(Ranch/Calf Tracker) beat event logs (Palpation/Scrotal), active beats
sold/inactive, newest beats oldest. Others are flagged so DAVE can ask which
animal the rancher means.

The LIN (Livestock Identification Number — the GS1 provenance anchor) is
surfaced whenever present.

---

## DAVE's brain is a PERSISTENT VOLUME

The vet-knowledge ChromaDB is **data, not code**. It must live on a mounted
volume so it survives redeploys and reboots.

**Coolify persistent storage:** mount a volume at `/data/herdmate_vet_db`.
Set `CHROMA_DB_PATH=/data/herdmate_vet_db`.

Build the knowledge base ONCE (then it persists):
```bash
# inside the container or on a box with the volume mounted:
python herdmate_vet_ingest.py --dir /path/to/vet_pdfs
# or a single source:
python herdmate_vet_ingest.py --pdf /path/to/MSD_manual.pdf
```

---

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key. Never hardcode. |
| `CREDENTIALS_FILE` | ✅ | Path to Google service-account JSON (mount at `/data/credentials.json`). |
| `CHROMA_DB_PATH` | ✅ (prod) | Persistent path to the vet-knowledge DB. |
| `CLAUDE_MODEL` | optional | Defaults to `claude-haiku-4-5-20251001`. |
| `RAG_SERVICE_URL` | optional | General RAG service for non-vet docs. |
| `PORT` | optional | Defaults to `5005`. |

The service account must be shared on the Google Sheet (read access), and —
for photo reading later — on the AppSheet image Drive folder.

---

## Deploy ritual (per playbook)

1. Repo ✅ (this)
2. Files ✅ (`herdmate_vet_api.py`, `herdmate_vet_ingest.py`, `requirements.txt`, `Dockerfile`, `README.md`)
3. DNS — Porkbun A record: `dave` → `159.203.129.179`
4. Coolify — Build Pack: Dockerfile, Port Exposes: 5005, Domain: `https://dave.sagewire.dev`, add persistent volume + env vars + credentials mount
5. Deploy
6. `curl -I https://dave.sagewire.dev/health` → `HTTP/2 200`
7. Main endpoint test (`/vet/ask`)
8. Add to Server Atlas
