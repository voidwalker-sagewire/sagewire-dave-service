#!/usr/bin/env python3
"""
HerdMate DAVE Vet AI — FastAPI Backend v4 (dynamic sheet reading)
Uses Google Service Account for permanent server-side auth.
No OAuth tokens. No browser dependency. Works forever.

v3.1 CHANGES FROM v3:
- find_animal() now collects ALL matches for a tag across both tabs
  instead of returning the first hit. Ranches reuse tag numbers across
  years, so "765" can be a sold 2025 bull AND a live 2026 heifer calf.
  We rank matches (active status first, then most recent date) and
  attach the runner-up matches so DAVE can flag ambiguity instead of
  silently guessing and confusing the rancher.
- format_animal_context() now surfaces "_other_matches" so DAVE's
  answer can say "I've got two 765s, which one?" instead of assuming.
- VET_SYSTEM_PROMPT updated with explicit instruction on this behavior.

Run:
    pip install fastapi uvicorn chromadb sentence-transformers anthropic \
        google-auth requests --break-system-packages
    export ANTHROPIC_API_KEY='sk-ant-...'        # required, never hardcode
    export CREDENTIALS_FILE='/root/credentials.json'   # service account JSON
    python3 herdmate_vet_api.py                  # serves on port 8001

Optional env vars: CHROMA_HOST, CHROMA_PORT, CLAUDE_MODEL, PORT.
Front it with HTTPS (Certbot/Cloudflare) so the field app's Bluetooth and
microphone work — browsers block those on plain HTTP.
"""

import os
import json
import hashlib
import requests as http_requests
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import chromadb
from chromadb.utils import embedding_functions
import anthropic
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

app = FastAPI(title="HerdMate DAVE Vet AI", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://scanner.herdmate.ag",
        "https://api.herdmate.ag",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── CONFIG ──
CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
# Persistent path for Dave's vet-knowledge DB. On Coolify this is a mounted
# volume so the knowledge survives redeploys/reboots. Defaults to a local
# folder for dev.
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./herdmate_vet_db")
VET_COLLECTION = "herdmate_vet_knowledge"
MEMORY_COLLECTION = "herdmate_vet_memory"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CREDENTIALS_FILE = os.environ.get("CREDENTIALS_FILE", "/root/credentials.json")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
# Optional: general-purpose RAG SERVICE for non-vet documents (equipment
# manuals, SOPs, labels, etc). Dave OWNS its vet knowledge, but MAY call
# this reusable service for other document types. Service->service is
# allowed by the SageWire rule; a service never depends on a product.
RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "")  # e.g. https://rag.sagewire.dev
SERVER_PORT = int(os.environ.get("PORT", "5005"))

# Fail fast and loud if the key is missing. This is the single guard that
# prevents DAVE from silently degrading into a keyless wrapper that returns
# 500s on every question. The real DAVE always has its key in the environment.
if not ANTHROPIC_API_KEY:
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. Export it before starting DAVE:\n"
        "    export ANTHROPIC_API_KEY='sk-ant-...'\n"
        "Never hardcode the key into this file."
    )

# ── SERVICE ACCOUNT AUTH ──
_service_creds = None

def get_service_token():
    global _service_creds
    try:
        if _service_creds is None:
            _service_creds = Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=SHEETS_SCOPES
            )
        if not _service_creds.valid:
            auth_req = google.auth.transport.requests.Request()
            _service_creds.refresh(auth_req)
        return _service_creds.token
    except Exception as e:
        print(f"Service account auth error: {e}")
        return None

# ── SIMPLE CACHE ──
_animal_cache: dict = {}
CACHE_TTL_SECONDS = 300

def get_cached_animal(key: str):
    import time
    if key in _animal_cache:
        record, ts = _animal_cache[key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return record
    return "MISS"

def set_cached_animal(key: str, record):
    import time
    _animal_cache[key] = (record, time.time())

# ── CHROMA CLIENT ──
def get_chroma():
    # Prefer a standalone Chroma server if one is configured/running.
    # Otherwise read the persistent on-disk DB (the mounted volume in prod).
    try:
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        client.heartbeat()
        return client
    except Exception:
        return chromadb.PersistentClient(path=CHROMA_DB_PATH)

ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

chroma = get_chroma()
vet_collection = chroma.get_or_create_collection(VET_COLLECTION, embedding_function=ef)
memory_collection = chroma.get_or_create_collection(MEMORY_COLLECTION, embedding_function=ef)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── MODELS ──
class VetQuestion(BaseModel):
    question: str
    operation: Optional[str] = "HerdMate"
    tag_epc: Optional[str] = None
    pasture: Optional[str] = None
    weather: Optional[str] = None
    user_id: Optional[str] = "default"
    conversation_history: list = Field(default_factory=list)
    image_base64: Optional[str] = None
    image_type: Optional[str] = "image/jpeg"
    google_access_token: Optional[str] = None   # deprecated - server uses service account
    herdmate_sheet_id: Optional[str] = None
    google_user_email: Optional[str] = None

class VetAnswer(BaseModel):
    answer: str
    sources: list
    similar_past_cases: list
    confidence: str
    timestamp: str
    animal_context: Optional[dict] = None

# ── GOOGLE SHEETS LOOKUP ──
def sheets_get(token: str, sheet_id: str, range_name: str):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_name}"
    try:
        resp = http_requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.ok:
            return resp.json().get("values", [])
        else:
            print(f"Sheets error {resp.status_code}: {resp.text[:200]}")
            return []
    except Exception as e:
        print(f"Sheets request error: {e}")
        return []

def _is_active_status(status: str) -> bool:
    """True if a status string reads as 'currently on the operation'."""
    s = str(status).strip().lower()
    if not s:
        return True  # blank status = assume active, don't punish missing data
    if "inactive" in s or "sold" in s or "dead" in s or "died" in s or "culled" in s:
        return False
    return True

def _best_date(record: dict) -> str:
    """Pull whichever date field a record has for recency sorting."""
    return record.get("birth_date") or record.get("date") or ""

def _norm_tag(v) -> str:
    """Normalize a tag/UHF/RFID value for comparison: string, stripped, upper."""
    return str(v).strip().upper()

def _row_matches_tag(row_dict: dict, tag: str) -> bool:
    """
    Dynamic match: does ANY plausible identifier column in this row equal the tag?

    We do NOT hardcode a single column name. Ranches put the tag/UHF/RFID in
    differently-named columns across tabs and those names change over time.
    Instead we look at every column whose HEADER looks like an identifier
    field, and match against all of them. New id column added later? It gets
    picked up automatically as long as its header contains one of these hints.
    """
    tag_n = _norm_tag(tag)
    if not tag_n:
        return False
    id_hints = ("tag", "uhf", "rfid", "epc", "dual freq", "int", "registration", "lin")
    for header, value in row_dict.items():
        h = str(header).strip().lower()
        if any(hint in h for hint in id_hints):
            if _norm_tag(value) == tag_n:
                return True
    return False

def _status_of(row_dict: dict) -> str:
    """Find a status-like column value without hardcoding which tab names it."""
    for header, value in row_dict.items():
        if str(header).strip().lower() == "status":
            return str(value)
    return ""

def _is_active_status(status: str) -> bool:
    """True if a status reads as 'currently on the operation'."""
    s = str(status).strip().lower()
    if not s:
        return True  # blank = assume active, don't punish missing data
    for dead in ("inactive", "sold", "dead", "died", "culled", "deceased", "disposed"):
        if dead in s:
            return False
    return True

def _best_date_value(row_dict: dict) -> str:
    """
    Grab a date for recency ranking. Prefer birth date, then any created/date
    column. Purely for sorting duplicate tags — newest animal wins.
    """
    # Priority order of header hints
    for want in ("birth date", "date", "createddate", "created date"):
        for header, value in row_dict.items():
            if str(header).strip().lower() == want and str(value).strip():
                return str(value).strip()
    # Fallback: any header containing "date"
    for header, value in row_dict.items():
        if "date" in str(header).strip().lower() and str(value).strip():
            return str(value).strip()
    return ""

def _find_photo_path(row_dict: dict) -> str:
    """
    Return a photo path from this row if present, without hardcoding the
    column name. Different tabs call it Photo, Picture, Photo Album, etc.
    """
    photo_hints = ("photo", "picture", "image", "img")
    for header, value in row_dict.items():
        h = str(header).strip().lower()
        if any(hint in h for hint in photo_hints):
            v = str(value).strip()
            if v and v.lower() not in ("none", "null"):
                return v
    return ""

def _find_lin(row_dict: dict) -> str:
    """Surface the Livestock Identification Number (GS1 provenance anchor)."""
    for header, value in row_dict.items():
        h = str(header).strip().lower()
        # match the LIN value column but not the 'LIN Sequence' helper column
        if "livestock identification number" in h and "sequence" not in h:
            v = str(value).strip()
            if v:
                return v
        if h == "lin":
            v = str(value).strip()
            if v:
                return v
    return ""

def _clean_row(row_dict: dict) -> dict:
    """
    Drop empty cells so the AI sees a compact, readable record instead of
    100 blank columns. Keeps whatever has a value, whatever it's named.
    """
    out = {}
    for header, value in row_dict.items():
        h = str(header).strip()
        v = "" if value is None else str(value).strip()
        if not h:
            continue
        if v == "" or v.lower() in ("none", "null"):
            continue
        # skip obvious internal/noise columns
        if h.lower().startswith("column_"):
            continue
        out[h] = v
    return out

def _parse_date_for_sort(date_str: str):
    """
    Parse a date for ranking. Handles the mixed formats that show up across
    tabs: '2024-09-27 00:00:00' (Ranch Tracker) and '5/27/2026' (Calf Tracker).
    Returns a comparable date; unknown/blank sorts oldest.
    """
    from datetime import datetime as _dt
    s = str(date_str).strip()
    if not s:
        return _dt(1900, 1, 1)
    # strip a trailing time if present
    s = s.split(" ")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return _dt.strptime(s, fmt)
        except ValueError:
            continue
    return _dt(1900, 1, 1)

def _tab_priority(tab: str) -> int:
    """
    Higher = more authoritative as THE animal record.
    Primary animal tabs outrank event-log tabs so a palpation/scrotal entry
    never gets mistaken for the animal itself.
    """
    t = str(tab).strip().lower()
    if t in ("ranch tracker", "animals"):
        return 3          # master animal records
    if t in ("calf tracker",):
        return 2          # calving records (also a primary record of the animal)
    if t in ("watchlist", "bovine beacon"):
        return 1          # monitoring context
    return 0              # event logs: palpation, scrotal, etc.

# Tabs Dave searches for a per-animal match. Confirmed live by the operator.
# Dave reads the FULL row from any tab where the tag matches — column names
# are discovered dynamically, so this list is the only thing that's fixed,
# and even here unknown/missing tabs are skipped gracefully.
SEARCHABLE_TABS = [
    "Ranch Tracker",
    "Calf Tracker",
    "Palpation Log",     # NOTE: the spaced one, not "PalpationLog"
    "ScrotalExamLog",
    "WatchList",
    "Bovine Beacon",     # live sheet only (not always present)
    "Animals",           # live sheet only (not always present)
]

def find_animal(sheet_id: str, tag_identifier: str):
    """
    DYNAMIC animal lookup (v4).

    Old behavior (v3.1 and earlier): hardcoded a specific list of column
    names per tab. Any rename/addition/move in the sheet broke Dave. This
    version instead:
      - searches each configured tab,
      - matches the tag against ANY identifier-looking column (dynamic),
      - grabs the ENTIRE matching row with all its columns/headers,
      - ranks duplicates (active first, then newest),
      - returns the best match plus any other matches for disambiguation,
      - carries a compact "all fields" dict so the AI sees everything.

    The AI reads the real columns, whatever they're named today. Sheet
    changes need no code change here.
    """
    if not tag_identifier or not sheet_id:
        return None

    cache_key = f"{sheet_id}_{tag_identifier}"
    cached = get_cached_animal(cache_key)
    if cached != "MISS":
        return cached

    token = get_service_token()
    if not token:
        return None

    tag = str(tag_identifier).strip()
    all_matches = []

    for tab in SEARCHABLE_TABS:
        try:
            # Pull a wide range; Sheets returns only populated columns.
            data = sheets_get(token, sheet_id, f"{tab}!A:CZ")
            if not data or len(data) < 2:
                continue
            headers = data[0]
            for row in data[1:]:
                if not row:
                    continue
                row_dict = dict(zip(headers, row + [""] * max(0, len(headers) - len(row))))
                if _row_matches_tag(row_dict, tag):
                    fields = _clean_row(row_dict)
                    all_matches.append({
                        "source": tab,
                        "tag": tag,
                        "status": _status_of(row_dict),
                        "_date": _best_date_value(row_dict),
                        "photo": _find_photo_path(row_dict),
                        "lin": _find_lin(row_dict),
                        "fields": fields,  # the full dynamic record
                    })
        except Exception as e:
            # A missing tab (e.g. Bovine Beacon not in this sheet) just gets skipped.
            print(f"[find_animal] tab '{tab}' skipped: {e}")
            continue

    if not all_matches:
        set_cached_animal(cache_key, None)
        return None

    # Rank matches. Priority, highest wins:
    #   1. Is it a primary animal record vs just an event log about the animal?
    #   2. Active status before inactive/sold.
    #   3. Most recent date.
    # A palpation/scrotal log row is an EVENT, not the animal — it must never
    # outrank the actual Ranch Tracker / Calf Tracker animal record.
    def _rank_key(m):
        return (
            _is_active_status(m.get("status", "")),      # live beats sold/dead
            _parse_date_for_sort(m.get("_date", "")),    # newest animal wins (real date compare)
            _tab_priority(m.get("source", "")),          # tie-breaker only
        )
    all_matches.sort(key=_rank_key, reverse=True)

    best = all_matches[0]

    # If multiple records share this tag (across tabs OR reused across years),
    # attach the others so DAVE can disambiguate instead of guessing.
    if len(all_matches) > 1:
        best = dict(best)
        best["_ambiguous"] = True
        best["_other_matches"] = [
            {
                "source": m.get("source"),
                "status": m.get("status") or "unknown",
                "date": m.get("_date") or "unknown date",
                # a couple of human-readable hints pulled dynamically
                "hint": _short_hint(m.get("fields", {})),
            }
            for m in all_matches[1:]
        ]

    set_cached_animal(cache_key, best)
    return best

def _short_hint(fields: dict) -> str:
    """A tiny human descriptor for disambiguation, pulled dynamically."""
    bits = []
    for want in ("Color", "Calf Color", "Sex", "Calf Sex", "Type", "Calf Type", "Breed"):
        for h, v in fields.items():
            if h.strip().lower() == want.lower() and v:
                bits.append(v)
                break
        if len(bits) >= 3:
            break
    return " ".join(bits) if bits else "record"

def format_animal_context(animal: dict) -> str:
    """
    Render the animal record for the AI. DYNAMIC: prints whatever fields the
    row actually had, so new/renamed columns show up automatically. Adds the
    LIN prominently and flags tag ambiguity.
    """
    if not animal:
        return ""

    lines = [f"--- ANIMAL RECORD (from tab: {animal.get('source', 'HerdMate')}) ---"]

    # LIN up top — the provenance anchor.
    if animal.get("lin"):
        lines.append(f"Livestock Identification Number (LIN): {animal['lin']}")

    # The full dynamic field set — everything the row actually contained.
    # Skip the LIN column here since we already printed it at the top.
    fields = animal.get("fields", {})
    if fields:
        for header, value in fields.items():
            h_low = str(header).strip().lower()
            if "livestock identification number" in h_low and "sequence" not in h_low:
                continue  # already shown at top
            lines.append(f"{header}: {value}")

    # If a photo path exists, tell the AI it's available.
    if animal.get("photo"):
        lines.append(f"[Photo on file: {animal['photo']}]")

    # Ambiguity flag — multiple animals share this tag.
    if animal.get("_ambiguous") and animal.get("_other_matches"):
        lines.append("")
        lines.append("⚠️ MULTIPLE RECORDS SHARE THIS TAG. You are shown the most likely")
        lines.append("one (active and/or most recent). Others with the same tag:")
        for m in animal["_other_matches"]:
            lines.append(f"  - {m['source']}: {m['hint']}, status: {m['status']}, date: {m['date']}")
        lines.append("If the conversation doesn't make it obvious which animal the rancher")
        lines.append("means, briefly ask. Tag numbers get reused across years on ranches.")

    return "\n".join(lines)


def search_knowledge(question: str, n_results: int = 5):
    try:
        results = vet_collection.query(
            query_texts=[question],
            n_results=min(n_results, vet_collection.count() or 1)
        )
        return list(zip(results.get("documents", [[]])[0], results.get("metadatas", [[]])[0]))
    except Exception as e:
        print(f"Knowledge search error: {e}")
        return []

def search_memory(question: str, user_id: str, n_results: int = 3):
    try:
        if memory_collection.count() == 0:
            return []
        where_filter = {"user_id": {"$eq": user_id}}
        results = memory_collection.query(
            query_texts=[question],
            n_results=min(n_results, memory_collection.count()),
            where=where_filter
        )
        return list(zip(results.get("documents", [[]])[0], results.get("metadatas", [[]])[0]))
    except Exception as e:
        print(f"Memory search error: {e}")
        return []

def save_to_memory(question: str, answer: str, metadata: dict):
    try:
        doc_id = hashlib.md5(f"{question}_{datetime.now().isoformat()}".encode()).hexdigest()
        memory_collection.add(
            ids=[doc_id],
            documents=[f"Q: {question}\nA: {answer}"],
            metadatas=[{**metadata, "timestamp": datetime.now().isoformat(), "type": "field_case"}]
        )
    except Exception as e:
        print(f"Memory save error: {e}")

# ── SYSTEM PROMPT ──
VET_SYSTEM_PROMPT = """You are DAVE — Don't Always Visit the Emergency Vet.
You are a cattle health assistant built for working ranchers in the field by HerdMate.

You are NOT a replacement for a veterinarian. You are a knowledgeable field reference.

Your style:
- Plain language. Direct. Get to the point fast.
- Practical. What do I do RIGHT NOW.
- Honest about uncertainty.

Only recommend calling a vet when it genuinely warrants it:
- EMERGENCY (say it first, loud): not breathing, severe bleeding, prolapse, broken bones, downer cow that can't rise, bloat with distress, difficult calving over 2 hours
- URGENT (mention once at end): fever over 104, eye cloudiness or corneal ulcer, calf not nursing after 6 hours, signs of BRD
- MONITOR (no vet mention needed): mild lameness, early scours with alert calf, minor wounds, routine questions

The disclaimer at the top of the app already covers liability. Do not repeat it in every response.
If you do recommend a vet call, say it once clearly and move on.

When you have an animal record, use it. Reference specific details — tag number, age, dam, birth weight.
Make your answers personal to that specific animal.

TAG NUMBER AMBIGUITY: Working ranches reuse tag numbers across years — the animal
record you're given may include a note that OTHER animals share this same tag
(look for "MULTIPLE ANIMALS SHARE THIS TAG NUMBER" in the record). When you see
that note, don't silently assume you have the right animal. Briefly confirm which
one the rancher means — mention the other match(es) by status/date/color so they
can correct you in one word if you guessed wrong. Once they confirm or the
conversation makes it obvious, drop it and move on. Don't belabor it.

You have access to:
1. Veterinary knowledge base — MSD Veterinary Manual and beef cattle extension publications
2. The rancher's personal field history — past cases and outcomes
3. Animal records from HerdMate Google Sheet when a tag is provided"""

# ── MAIN ENDPOINT ──
@app.post("/vet/ask", response_model=VetAnswer)
async def ask_vet(q: VetQuestion):
    if not q.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    effective_user_id = q.google_user_email or q.user_id or "default"

    # Look up animal if tag provided
    animal_record = None
    animal_context = ""
    if q.tag_epc and q.herdmate_sheet_id:
        animal_record = find_animal(q.herdmate_sheet_id, q.tag_epc)
        if animal_record:
            animal_context = format_animal_context(animal_record)

    # RAG search
    vet_results = search_knowledge(q.question)
    past_cases = search_memory(q.question, effective_user_id)

    # Build dynamic system prompt
    dynamic_system = VET_SYSTEM_PROMPT

    # Tell DAVE what TODAY is. Without this, the model guesses the year from
    # its training data and can't reason about recently-born animals (e.g. a
    # calf born in 2026 looks impossible if DAVE thinks it's still 2025).
    _now = datetime.now()
    dynamic_system += (
        f"\n\n--- TODAY'S DATE ---\n"
        f"Today is {_now.strftime('%A, %B %d, %Y')}. "
        f"The current year is {_now.year}. "
        f"When you reason about animals' ages, birth years, or what's recent, "
        f"use THIS as the current date. Do not assume any other year."
    )

    if animal_context:
        dynamic_system += "\n\n" + animal_context

    ctx_parts = []
    if q.operation: ctx_parts.append(f"Operation: {q.operation}")
    if q.pasture: ctx_parts.append(f"Pasture: {q.pasture}")
    if q.weather: ctx_parts.append(f"Weather: {q.weather}")
    if q.tag_epc and not animal_record: ctx_parts.append(f"Scanned tag: {q.tag_epc} (no record found)")
    if ctx_parts:
        dynamic_system += "\n\n--- FIELD CONTEXT ---\n" + "\n".join(ctx_parts)

    sources = []
    if vet_results:
        vet_ctx = "\n\n--- VETERINARY KNOWLEDGE ---"
        for doc, meta in vet_results:
            source = meta.get("source", "veterinary reference")
            vet_ctx += f"\n[{source}]\n{doc}\n"
            if source not in sources:
                sources.append(source)
        dynamic_system += vet_ctx

    past_case_summaries = []
    if past_cases:
        mem_ctx = "\n\n--- YOUR PAST FIELD CASES ---"
        for doc, meta in past_cases:
            ts = meta.get("timestamp", "")[:10]
            mem_ctx += f"\n[{ts}] {doc}\n"
            past_case_summaries.append(f"{ts}: {doc[:100]}...")
        dynamic_system += mem_ctx

    # Build conversation
    claude_messages = []
    for msg in q.conversation_history[-8:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content:
            claude_messages.append({"role": role, "content": content})

    if q.image_base64:
        current_content = [
            {"type": "image", "source": {"type": "base64", "media_type": q.image_type or "image/jpeg", "data": q.image_base64}},
            {"type": "text", "text": q.question}
        ]
    else:
        current_content = q.question

    claude_messages.append({"role": "user", "content": current_content})

    try:
        response = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=700,
            system=dynamic_system,
            messages=claude_messages
        )
        text_blocks = [b for b in response.content if b.type == "text"]
        answer = text_blocks[0].text if text_blocks else "DAVE could not generate a response."
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI response failed: {str(e)}")

    save_to_memory(
        question=q.question,
        answer=answer,
        metadata={
            "user_id": effective_user_id,
            "operation": q.operation or "",
            "pasture": q.pasture or "",
            "tag_epc": q.tag_epc or "",
            "weather": q.weather or ""
        }
    )

    return VetAnswer(
        answer=answer,
        sources=sources[:3],
        similar_past_cases=past_case_summaries[:2],
        confidence="high" if vet_results else "low",
        timestamp=datetime.now().isoformat(),
        animal_context=animal_record
    )

# ── SENTINEL ANIMAL LOOKUP ──

class AnimalLookupRequest(BaseModel):
    epc: str
    herdmate_sheet_id: str
    operation: Optional[str] = "HerdMate"


def _normalize_header(value: str) -> str:
    """
    Normalize a Sheet column header for flexible matching.

    Examples:
      "Tag #"                      -> "tag"
      "Birth Weight (lbs)"         -> "birthweightlbs"
      "Body Condition Score (BCS)" -> "bodyconditionscorebcs"
    """
    return "".join(
        character
        for character in str(value).strip().lower()
        if character.isalnum()
    )


def _animal_field(fields: dict, *possible_headers: str) -> str:
    """
    Find the first populated value whose header matches one of the supplied
    names. Header punctuation, spaces and capitalization are ignored.
    """
    if not isinstance(fields, dict):
        return ""

    normalized_fields = {}

    for header, value in fields.items():
        normalized_header = _normalize_header(header)
        clean_value = "" if value is None else str(value).strip()

        if normalized_header and clean_value:
            normalized_fields[normalized_header] = clean_value

    for possible_header in possible_headers:
        normalized_header = _normalize_header(possible_header)

        if normalized_header in normalized_fields:
            return normalized_fields[normalized_header]

    return ""


def _relationship_identity(fields: dict, reference: str) -> dict:
    """
    Return all useful identities for a related animal.

    LIN remains the permanent record identity.
    ID is the internal database key.
    UHF/RFID are machine-readable identifiers.
    Tag is the human-facing field identifier.
    """
    return {
        "reference": str(reference or "").strip(),
        "id": _animal_field(
            fields,
            "ID",
            "Animal ID",
            "Record ID"
        ),
        "tag": _animal_field(
            fields,
            "New Tag #",
            "Tag #",
            "Calf Tag",
            "Tag Number",
            "Tag"
        ),
        "display_id": _animal_field(
            fields,
            "DisplayID",
            "Display ID",
            "IDCalf"
        ),
        "uhf": _animal_field(
            fields,
            "UHF#",
            "UHF #",
            "UHF",
            "EPC",
            "EPC#"
        ),
        "rfid": _animal_field(
            fields,
            "RFID#",
            "RFID #",
            "RFID"
        ),
        "lin": _animal_field(
            fields,
            "Livestock Identification Number (LIN)",
            "Livestock Identification Number",
            "LIN"
        ),
        "status": _animal_field(
            fields,
            "Status"
        ),
        "resolved": True
    }


def _relationship_match_values(fields: dict) -> list:
    """
    Values that may identify a Ranch Tracker row.

    Row ID is checked, but this also survives a future move where the
    relationship reference becomes a LIN, UHF, RFID or visible tag.
    """
    values = [
        _animal_field(fields, "ID", "Animal ID", "Record ID"),
        _animal_field(
            fields,
            "Livestock Identification Number (LIN)",
            "Livestock Identification Number",
            "LIN"
        ),
        _animal_field(fields, "UHF#", "UHF #", "UHF", "EPC", "EPC#"),
        _animal_field(fields, "RFID#", "RFID #", "RFID"),
        _animal_field(
            fields,
            "New Tag #",
            "Tag #",
            "Calf Tag",
            "Tag Number",
            "Tag"
        ),
        _animal_field(fields, "DisplayID", "Display ID")
    ]

    return [
        _norm_tag(value)
        for value in values
        if str(value or "").strip()
    ]


def _resolve_related_animals(sheet_id: str, animal: dict) -> dict:
    """
    Resolve Dam # and Sire # against Ranch Tracker.

    Current HerdMate rows use the Ranch Tracker ID as a foreign key:
      Dam #  = 2715 -> Tag # 3905B/339
      Sire # = 3263 -> Tag # 6794J

    The resolver also accepts LIN/UHF/RFID/tag references so the data model
    can evolve without replacing this endpoint again.
    """
    fields = animal.get("fields", {}) if isinstance(animal, dict) else {}

    dam_reference = _animal_field(
        fields,
        "Dam #",
        "Dam ID",
        "Dam",
        "Cow ID",
        "Cow #",
        "Cow Tag"
    )

    sire_reference = _animal_field(
        fields,
        "Sire #",
        "Sire ID",
        "Sire",
        "Bull ID",
        "Bull #",
        "Bull Tag"
    )

    relationships = {
        "dam": {
            "reference": dam_reference,
            "id": dam_reference,
            "tag": "",
            "display_id": "",
            "uhf": "",
            "rfid": "",
            "lin": "",
            "status": "",
            "resolved": False
        },
        "sire": {
            "reference": sire_reference,
            "id": sire_reference,
            "tag": "",
            "display_id": "",
            "uhf": "",
            "rfid": "",
            "lin": "",
            "status": "",
            "resolved": False
        }
    }

    wanted = {
        "dam": _norm_tag(dam_reference),
        "sire": _norm_tag(sire_reference)
    }

    if not wanted["dam"] and not wanted["sire"]:
        return relationships

    token = get_service_token()
    if not token:
        return relationships

    try:
        ranch_data = sheets_get(token, sheet_id, "Ranch Tracker!A:CZ")
    except Exception as exception:
        print(f"[relationships] Ranch Tracker lookup failed: {exception}")
        return relationships

    if not ranch_data or len(ranch_data) < 2:
        return relationships

    headers = ranch_data[0]

    for row in ranch_data[1:]:
        if not row:
            continue

        row_dict = dict(
            zip(
                headers,
                row + [""] * max(0, len(headers) - len(row))
            )
        )
        row_fields = _clean_row(row_dict)
        match_values = _relationship_match_values(row_fields)

        for role in ("dam", "sire"):
            reference = wanted[role]

            if (
                reference
                and not relationships[role]["resolved"]
                and reference in match_values
            ):
                relationships[role] = _relationship_identity(
                    row_fields,
                    relationships[role]["reference"]
                )

        if relationships["dam"]["resolved"] and relationships["sire"]["resolved"]:
            break

    return relationships


def _relationship_display(identity: dict, fallback: str) -> str:
    """
    Human display order:
      visible ear tag -> LIN -> UHF -> RFID -> original reference

    Computers still receive every identifier in separate response fields.
    """
    return (
        str(identity.get("tag", "")).strip()
        or str(identity.get("lin", "")).strip()
        or str(identity.get("uhf", "")).strip()
        or str(identity.get("rfid", "")).strip()
        or str(fallback or "").strip()
    )


def _sentinel_animal_record(
    animal: dict,
    lookup_epc: str,
    relationships: Optional[dict] = None
) -> dict:
    """
    Convert DAVE's dynamic animal result into the common fields Sentinel
    displays, while preserving the complete dynamic Google Sheet row.
    """
    fields = animal.get("fields", {})
    relationships = relationships or {}

    dam_identity = relationships.get("dam", {})
    sire_identity = relationships.get("sire", {})

    raw_dam = _animal_field(
        fields,
        "Dam #",
        "Dam ID",
        "Dam",
        "Cow ID",
        "Cow #",
        "Cow Tag"
    )

    raw_sire = _animal_field(
        fields,
        "Sire #",
        "Sire ID",
        "Sire",
        "Bull ID",
        "Bull #",
        "Bull Tag"
    )

    tag = _animal_field(
        fields,
        "New Tag #",
        "Tag #",
        "Calf Tag",
        "Cow Tag",
        "Tag Number",
        "Tag"
    )

    display_id = _animal_field(
        fields,
        "DisplayID",
        "Display ID",
        "IDCalf",
        "Animal ID",
        "ID"
    )

    uhf = _animal_field(
        fields,
        "UHF#",
        "UHF #",
        "UHF",
        "EPC",
        "EPC#"
    )

    rfid = _animal_field(
        fields,
        "RFID#",
        "RFID #",
        "RFID"
    )

    lin = (
        str(animal.get("lin", "")).strip()
        or _animal_field(
            fields,
            "Livestock Identification Number (LIN)",
            "Livestock Identification Number",
            "LIN"
        )
    )

    photo = (
        str(animal.get("photo", "")).strip()
        or _animal_field(
            fields,
            "Photo",
            "Picture",
            "Image",
            "Photo Album"
        )
    )

    status = (
        str(animal.get("status", "")).strip()
        or _animal_field(fields, "Status")
    )

    return {
        "lookup_epc": lookup_epc,

        # The animal's own identities.
        "tag": tag or str(animal.get("tag", "")).strip(),
        "display_id": display_id,
        "uhf": uhf,
        "rfid": rfid,
        "lin": lin,

        "source": str(animal.get("source", "")).strip(),
        "status": status,

        "sex": _animal_field(fields, "Sex", "Calf Sex"),
        "type": _animal_field(fields, "Type", "Calf Type", "Category"),
        "breed": _animal_field(fields, "Breed"),
        "color": _animal_field(fields, "Color", "Calf Color"),

        "birth_date": _animal_field(
            fields,
            "Birth Date",
            "Date of Birth",
            "DOB",
            "Date"
        ),

        "date": str(animal.get("_date", "")).strip(),
        "age": _animal_field(fields, "Age"),
        "pasture": _animal_field(fields, "Pasture"),
        "herd": _animal_field(fields, "Herd"),

        "weight": _animal_field(
            fields,
            "Weight (lbs)",
            "Weight",
            "Current Weight",
            "Latest Weight"
        ),

        "birth_weight": _animal_field(
            fields,
            "Birth Weight (lbs)",
            "Birth Weight"
        ),

        "weaning_weight": _animal_field(
            fields,
            "Weaning Weight (lbs)",
            "Weaning Weight"
        ),

        # Human-facing parent values. The current Android client already reads
        # these two fields, so it will immediately show 3905B/339 and 6794J.
        "dam": _relationship_display(dam_identity, raw_dam),
        "sire": _relationship_display(sire_identity, raw_sire),

        # Preserve every parent identifier for later UI and lineage work.
        "dam_id": str(dam_identity.get("id", "") or raw_dam).strip(),
        "dam_tag": str(dam_identity.get("tag", "")).strip(),
        "dam_display_id": str(dam_identity.get("display_id", "")).strip(),
        "dam_uhf": str(dam_identity.get("uhf", "")).strip(),
        "dam_rfid": str(dam_identity.get("rfid", "")).strip(),
        "dam_lin": str(dam_identity.get("lin", "")).strip(),

        "sire_id": str(sire_identity.get("id", "") or raw_sire).strip(),
        "sire_tag": str(sire_identity.get("tag", "")).strip(),
        "sire_display_id": str(sire_identity.get("display_id", "")).strip(),
        "sire_uhf": str(sire_identity.get("uhf", "")).strip(),
        "sire_rfid": str(sire_identity.get("rfid", "")).strip(),
        "sire_lin": str(sire_identity.get("lin", "")).strip(),

        "due_date": _animal_field(fields, "Due Date"),

        "palp_result": _animal_field(
            fields,
            "Palp. Result",
            "Palp Result",
            "Palpation Result"
        ),

        "months_preg": _animal_field(
            fields,
            "Mth. Preg.",
            "Months Pregnant",
            "Months Preg"
        ),

        "bcs": _animal_field(
            fields,
            "Body Condition Score (BCS)",
            "Body Condition Score",
            "BCS",
            "Dam BCS"
        ),

        "disposition": _animal_field(fields, "Disposition"),

        "notes": _animal_field(
            fields,
            "Notes",
            "Calving Notes"
        ),

        "photo": photo,

        "_ambiguous": bool(animal.get("_ambiguous")),
        "_other_matches": animal.get("_other_matches", []),

        # Preserve the complete dynamic record.
        "fields": fields
    }


@app.get("/animal/health")
async def animal_health():
    return {
        "status": "ok",
        "service": "HerdMate Sentinel Animal Lookup",
        "dave_service": True,
        "version": "1.2.0"
    }

@app.post("/animal/lookup")
async def animal_lookup(request: AnimalLookupRequest):
    scanned_epc = request.epc.strip()
    sheet_id = request.herdmate_sheet_id.strip()

    if not scanned_epc:
        raise HTTPException(
            status_code=400,
            detail="EPC is required"
        )

    if not sheet_id:
        raise HTTPException(
            status_code=400,
            detail="herdmate_sheet_id is required"
        )

    # Always try the exact EPC reported by the reader first.
    lookup_candidates = [scanned_epc]

    # Some HerdMate tags are stored in the Sheet as the final 15 digits,
    # while the C316H reports a longer 24-digit EPC.
    #
    # Example:
    # scanned: 004252025740086010000015
    # stored:           740086010000015
    #
    # Only use the suffix as a fallback after the exact EPC has been tried.
    if scanned_epc.isdigit() and len(scanned_epc) > 15:
        uhf_suffix = scanned_epc[-15:]

        if uhf_suffix not in lookup_candidates:
            lookup_candidates.append(uhf_suffix)

    animal = None
    matched_identifier = ""

    try:
        for candidate in lookup_candidates:
            animal = find_animal(sheet_id, candidate)

            if animal:
                matched_identifier = candidate
                break

    except Exception as exception:
        raise HTTPException(
            status_code=500,
            detail=f"Animal lookup failed: {exception}"
        ) from exception

    timestamp = datetime.now().isoformat()

    if not animal:
        return {
            "found": False,
            "epc": scanned_epc,
            "matched_identifier": None,
            "normalized_lookup": False,
            "lookup_candidates": lookup_candidates,
            "operation": request.operation or "HerdMate",
            "animal": None,
            "ambiguous": False,
            "other_matches": [],
            "timestamp": timestamp
        }

    relationships = _resolve_related_animals(sheet_id, animal)

    sentinel_record = _sentinel_animal_record(
        animal,
        scanned_epc,
        relationships
    )

    sentinel_record["matched_identifier"] = matched_identifier
    sentinel_record["normalized_lookup"] = (
        matched_identifier != scanned_epc
    )

    return {
        "found": True,
        "epc": scanned_epc,
        "matched_identifier": matched_identifier,
        "normalized_lookup": matched_identifier != scanned_epc,
        "operation": request.operation or "HerdMate",
        "animal": sentinel_record,
        "ambiguous": bool(animal.get("_ambiguous")),
        "other_matches": animal.get("_other_matches", []),
        "timestamp": timestamp
    }
  
@app.get("/vet/status")
async def vet_status():
    return {
        "status": "online",
        "vet_knowledge_docs": vet_collection.count(),
        "field_memory_docs": memory_collection.count(),
        "ready": vet_collection.count() > 0,
        "service_account": os.path.exists(CREDENTIALS_FILE)
    }


@app.get("/health")
async def health_standard():
    """SageWire-standard health check."""
    return {
        "status": "ok",
        "service": "DAVE",
        "version": "4.0.0",
        "vet_docs": vet_collection.count(),
        "rag_service": RAG_SERVICE_URL or "not configured",
    }

@app.get("/vet/health")
async def health():
    return {"status": "ok", "service": "HerdMate DAVE Vet AI v4"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
