# DAVE — Data Model & Integration Spec
**HerdMate / SageWire — living design doc**
_Last updated: 2026-07-04 · Owner: Mike Miller (VoydWalker / Sagewire Syndicate)_

> This is the map. Dave gets built against THIS, not against guesses.
> When the Google Sheet changes, update this doc — but note: the whole
> point of the new design is that Dave reads columns DYNAMICALLY, so most
> sheet changes need NO code change at all.

---

## THE CORE DECISION (why the rewrite)

**Old Dave (v3.1, current):** hardwired. The code contains a hand-typed list
of ~30 exact column names ("Calf Tag", "Cow Tag", etc.). If a column is
renamed, added, or moved, Dave goes blind to it. This is why Dave kept
breaking every time the sheet changed. **Bad design.**

**New Dave (target):** dynamic. Dave finds the animal's row(s) in the
searchable tabs, grabs the ENTIRE row — every column, whatever it's named
that day, header included — and hands the whole thing to the AI. The AI
reads column headers like a human does. New column? Dave sees it. Renamed
column? Doesn't matter. **No code change when the sheet changes.**

Key insight: **the AI can read.** It does not need column names translated
in code. `Udder Condition: Good` is understood by the AI the same way a
human understands it. The hardwiring was pointless and actively harmful.

This makes the build SMALLER — we remove rigid code, we don't add more.

---

## HOW DAVE LOOKS UP AN ANIMAL (target behavior)

1. User asks about an animal (by tag #, UHF EPC, or RFID#).
2. Dave searches the **searchable per-animal tabs** (list below) for any
   row whose tag/UHF/RFID matches.
3. For every match, Dave grabs the **full row, all columns, with headers**.
4. Dave hands those complete rows to the AI as the animal's context.
5. The AI answers using whatever fields are relevant to the question —
   dynamically, no hardcoded field list.
6. **Duplicate tags across years** (e.g. a sold 2025 bull + a live 2026
   heifer both tagged 765): Dave ranks matches — active status first, then
   most recent date — and flags the others so it can ask "which one?"
   (This logic already exists in v3.1 and is kept.)

**Tab scope cap:** the sheet has ~26 tabs. Dave must NOT dump all of them
into the AI per question (too slow, too expensive). Dave only searches the
per-animal tabs below, and only pulls rows that match the animal.

---

## SEARCHABLE PER-ANIMAL TABS (Dave reads these, dynamically)

These are the tabs Mike confirmed are live and keyed by an animal.
Exact tab-name spelling confirmed against the sheet export where possible.

| Tab | Status | Tag column(s) to match on | Notes |
|---|---|---|---|
| **Ranch Tracker** | ✅ live, MAIN | `Tag #`, `New Tag #`, `UHF#`, `RFID#` | The master animal record. ~100 columns. Contains the LIN. |
| **Calf Tracker** | ✅ live | `Calf Tag`, `UHF#`, `RFID#`, `Cow Tag` (as dam) | Calving records. Photo col = **`Picture`**. |
| **Palpation Log** | ✅ live (the one WITH a space) | `Tag #` | Pregnancy checks. NOTE: there are two similarly-named tabs; the one Mike uses is **"Palpation Log"** (with space), NOT "PalpationLog". |
| **ScrotalExamLog** | ✅ live | `Tag #` | Bull breeding-soundness exams. |
| **Cattle Tracker** | ✅ live | herd-level, not per-animal — see note | Tracks herd/pasture head groups, not individual animals. May be group-context only. CONFIRM how Dave should use. |
| **Pastures** | ✅ live | by `PastureName` | Pasture reference. Context, not per-animal. |
| **Herds** | ✅ live | by `HerdName` / `Tag #` | Herd reference. Context. |
| **WatchList** | ✅ live | `Tag #`, `EarTag`, `Calf Tag` | Animals flagged for monitoring/darting. Photo col = `Photo`. HIGH VALUE for Dave — this is the "keep an eye on" list. |
| **HeadCounter** | ✅ live | by `Pasture` / `UHF` | Head-count sessions. Context. |
| **Bovine Beacon** | ✅ live (live sheet only, not in export) | TBD | CONFIRM columns — not in the Excel snapshot. |
| **Animals** | ✅ live (live sheet only, not in export) | TBD | CONFIRM columns — not in the Excel snapshot. |
| **Nutrition Tracker** | ✅ live | by `Herd` / `Pasture` | Feed records. Herd/pasture-level context, not per-animal. |

### Open questions on tab scope
- **Cattle Tracker, Pastures, Herds, HeadCounter, Nutrition Tracker** are
  group/reference tabs (keyed by herd or pasture, not individual tag). Should
  Dave pull these as *context* when relevant (e.g. "this animal is in the
  North pasture, here's that pasture's info")? Or ignore for now and keep
  Dave strictly per-animal? **DECISION NEEDED.**
- **Bovine Beacon** and **Animals** — need column dumps from the live sheet.
- **Palpation Log vs PalpationLog** — confirmed Mike uses the spaced one.
  Dave must search "Palpation Log", not "PalpationLog".

---

## PHOTO HANDLING

Photos live in different columns on different tabs:

| Tab | Photo column |
|---|---|
| Ranch Tracker | `Photo` |
| Calf Tracker | `Picture` |
| CowLedger (not in scope) | `Photo`, `Photo Album` |
| WatchList | `Photo` |

**Behavior (per Mike):** photo choice is **relevant to the question.**
- "How does she look now?" → latest **Ranch Tracker `Photo`** + latest BCS
- "How was her udder?" → last udder-condition photo
- "Last calving?" → **Calf Tracker `Picture`** for that calving
- Dave picks the photo that fits the question, not just "a photo."

**Fetch mechanism:** AppSheet stores images as a relative path like
`Calf Tracker_Images/11a978eb.Picture.004951.jpg`. The value in the cell is
that path. To display/analyze it, Dave fetches the file from Google Drive
using the **same service account** that already reads the Sheet.

**⚠️ AUTH GAP TO RESOLVE:** the service account currently has only
`spreadsheets.readonly` scope. To read image bytes from Drive it also needs
`drive.readonly`, AND the Drive folder holding the AppSheet images must be
shared with the service account email (same as the Sheet was). CONFIRM the
image folder is shared with the service account.

Dave already accepts a user-uploaded photo in chat (the 📷 button) and can
analyze it via Claude vision — that path works today. The NEW work is
auto-fetching the AppSheet photo from Drive.

---

## THE LIN — DON'T FORGET IT

`Livestock Identification Number (LIN)` in Ranch Tracker is the GS1-prefixed
unique animal ID — the farm-to-fork provenance anchor, the whole point of
the system. Dave currently never mentions it. **New Dave should surface the
LIN** when discussing an animal. It's the number that ties everything
together from birth to fork.

Related: `Livestock Identification Number (LIN) Sequence` and the GS1 company
prefix `7400860100000001`.

---

## LEGACY / DO NOT WIRE YET

**Vaccine & treatment columns in Ranch Tracker** (Bovishield, Dectomax,
Ultrabac 7, Endovac, LA 200, CattleMaster, Cydectin, dewormers, castration,
etc.): **LEGACY. Mid-migration.** These hold only "last dose," no history.
Mike is moving them to **TreatmentLog** (one dated row per treatment = real
history). Migration NOT done.

**Decision:** Dave SKIPS these columns for now. When TreatmentLog migration
is complete, point Dave at TreatmentLog for full dated treatment history.
That will be strictly better than "last dose" ever was.

Mark the code with a clear `# FUTURE: wire to TreatmentLog once migration done`.

---

## FUTURE FEATURES (roadmap, not now)

- **TreatmentLog integration** — full dated vaccine/treatment history per
  animal, once migration from Ranch Tracker columns is done.
- **Weaning data** — no weaning cycle has happened yet on this operation;
  WeaningLedger will populate over time. Wire Dave when data exists.
- **Photo BCS scoring** — Dave scores body condition 1–9 from a photo.
  Vision already exists; this is prompt + wiring.
- **Udder / teat assessment from photo** at calving.
- **Calf birth-weight estimate from photo.**

---

## SEPARATE TRACK — HARDWARE (NOT part of Dave)

Keep these off the Dave build. Captured so they're not lost:

- **Gallagher Bluetooth load cells** — if installed at work, snag one and
  intercept the BLE signal (same method that cracked the crane scale). Adds
  live-weight capture. "Someday, one afternoon" project.
- **Fixed RFID reader** — just received. Auto head-count / gate passage.
- **C72 gun** — app works.
- **R6 sleds** — BLE protocol still unsolved (sends binary `A5 5A` packets,
  needs correct inventory command to output EPC). Waiting on Chainway
  protocol doc or second-sled test. SEPARATE open issue.

---

## BUILD ORDER (proposed)

1. **Rewrite `find_animal` to be dynamic** — rip out hardcoded column list,
   pull full rows with headers from the searchable tabs, hand whole rows to
   the AI. Keep the v3.1 duplicate-tag ranking. This alone fixes "Dave is
   half-blind" and makes the sheet free to change.
2. **Add LIN surfacing** — make sure Dave sees and mentions the LIN.
3. **Confirm + wire the searchable tab list** — lock which tabs, resolve the
   group/reference-tab question, get Bovine Beacon + Animals columns.
4. **Photo auto-fetch from Drive** — add `drive.readonly` scope, confirm
   folder sharing, fetch image by path, pass to vision, question-relevant
   selection.
5. **Later:** TreatmentLog, weaning, photo BCS scoring.

---

## STANDING NOTES

- Production sheet ID: `1ziqvEJRYmqf4IvYLa4Ij3z4I-swln4nAMJI6gLlzlGI`
  (NOTE: a different ID `1Ih4DRJD...` appeared in a Dave settings screenshot —
  confirm which is the one true production sheet.)
- Dave backend: manual Python process `/root/herdmate_vet_api.py` on
  `dave-brain-server`, port 8001, NOT a Coolify container. Does not survive
  reboot on its own (convert to systemd service eventually).
- Service account reads the Sheet; needs Drive scope added for photos.
- This app is unpaid, self-built, running on the ranch where Mike works.
  It is a legit cattle app. Treat it like one.
