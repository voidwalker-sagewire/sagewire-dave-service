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

### Conversation and evidence behavior

Clients may send `session_id` (a fresh random ID for each new animal
conversation). Current-session messages are never returned as prior field
conversations. Memory is namespaced by user, operation, and sheet. Shared
default identities and clients without session IDs do not read or write
cross-session memory. These namespaces are caller-supplied, not authentication;
the existing API still needs an authenticated identity boundary before this
can be treated as multi-user isolation.

Previously stored `field_case` records remain on disk but are excluded from
recall: they contain unverified AI replies, not confirmed diagnoses/outcomes.
New records are `field_conversation`. No historical records are deleted.

Reference searches include user observations from the conversation, not just
the last short follow-up. The model returns a JSON answer with explicit passage
IDs. The service validates those IDs, renumbers inline citations, and returns
only the sources actually cited. Invalid citation output returns HTTP 502 and
is not saved to memory. Existing clients continue to receive an ordinary
`answer` string and `sources` list.

`confidence` is now `unverified`; retrieving a nearest-neighbor passage is not
a measurement of clinical confidence. `evidence_status` is `cited_references`
or `no_supporting_reference`. **Citation validation proves provenance, not
semantic support or clinical correctness.** Retrieval quality and adherence to
the revised triage/medication instructions require live-model evaluation and
veterinary review before clinical reliability claims.

Deploy `dave_conversation.py` alongside `herdmate_vet_api.py`. The Dockerfile
copies both. Deploy the updated Vet Station client to enable session-aware
memory there. Older clients retain normal chat but need a session-ID update
to participate in cross-session memory.

Validation: `python -m unittest discover -s tests -v` (31 offline tests).
These test orchestration and citation/memory rules with external dependencies
stubbed; they do not assess model medical advice or a live Chroma deployment.

### Replaying field cases before a field release

`tests/field_case_replay.py` accepts the original exported Markdown conversations
and saved MHT snapshot. It extracts exact user turns, old answers where present,
source-file hashes, and review criteria. Private exports and generated reports
belong outside this public repository; do not commit them or their photos.

```
python tests/field_case_replay.py prepare --input-dir /private/exports --output /private/suite.json
python tests/field_case_replay.py audit --suite /private/suite.json --output /private/baseline-audit.json
python tests/field_case_replay.py replay --suite /private/suite.json --api-base http://127.0.0.1:5006 --repeats 3 --output /private/revised-replay.json
```

Run the revised service separately on staging (for example PORT=5006), with
access to the intended veterinary corpus and model credential. Do not point it
at a writable production memory collection: use a separate Chroma data copy.
The replay uses no animal sheet or real user identity, which disables memory
read/write under the revised policy. It refuses known production URLs and
checks `conversation_policy_revision=field-evidence-1` before sending questions.

Each conversation starts fresh and uses the **newly generated** answers as its
history. Old AI answers are retained only for side-by-side review. Repeated runs
check for inconsistent behavior. Any failed turn stops that case so missing
context cannot silently distort later turns. Historic dates in filenames are
not evidence of the deployment's actual date or a diagnosis/outcome.

The supplied batch extracts to 8 conversations and 52 user turns; 47 recorded
answers are present. The bloat-photo export contains debug questions only,
so its 5 baseline answers are marked missing. Image bytes and contemporaneous
animal-sheet snapshots are not supplied by these text exports. Text-only runs
must ask for missing photos/labels rather than claim to have read them.

The baseline audit flags known failure signatures for review. An absence of
flags is **not a clinical pass**. Medication review criteria link to exact
manufacturer labels on DailyMed and the manufacturer site; other clinical
criteria still need veterinary review. Every new model answer must be assessed
for factual consistency, urgency, relevant evidence, and useful next actions.

### Existing Docker server launcher

The private server-test bundle adds `server_case_launcher.py` (packaged as
`start_test.py`), `export_vet_corpus.py`, `seed_vet_corpus.py`, and
`server_case_worker.py`. It includes the exact private suite and revised source;
private transcripts are not committed here.

The launcher inspects the selected production DAVE container and requires the
expected deployed revision. It reuses that exact installed image, reads only
the veterinary reference collection, exports its text/metadata/vectors, and
seeds a brand-new database in a new private directory. No production memory,
Google credentials, network configuration, or volume mounts are inherited.
Model access stays on the server: the launcher copies only the model credential
and model settings into a temporary mode-600 env file and unlinks that file
after container creation. Docker's normal root/admin access to container
environments still applies.

The separate test container has no published ports or proxy labels, uses the
default bridge for model API access, and is limited to one CPU and 2 GiB RAM.
It runs in the background and stops its test API after the replay finishes.
Output records full answers, sources, errors, model/revision details, source
hashes, and review criteria. A successful HTTP reply is never a clinical pass.
Normal model API usage applies (52 maximum question requests for one complete
pass of this supplied suite; errors stop the affected conversation).

The launcher tests are offline. Docker/image compatibility, reference export,
and the live model replay still need to be exercised on the existing server.

`tests/start_case_test_from_drive.py` can fetch the exact private bundle on the
server using DAVE's existing service-account credential. Grant only that verified
account reader access to the exact Drive file first. The bootstrap checks the
credential's account email and the expected archive SHA-256 before extracting
or starting anything. No public sharing or phone storage access is needed.
The download uses the Drive API; its project must have that API enabled.

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
