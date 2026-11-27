"""Conversation and citation policy without network or model dependencies."""

import hashlib
import json
import re

REPLY_TOOL_NAME = "submit_field_reply"


def reply_tool(reference_ids):
    """Use Claude's tool protocol for shape, rather than prompt-only JSON."""
    items = {"type": "string"}
    if reference_ids:
        items["enum"] = list(reference_ids)
    source_ids = {"type": "array", "items": items,
                  "description": "Exactly the supplied passage IDs cited inline in answer, or [] if none apply."}
    if not reference_ids:
        source_ids["maxItems"] = 0
    return {
        "name": REPLY_TOOL_NAME,
        "description": "Submit DAVE's user-facing field reply and the passage IDs supporting its inline citations. This submits a reply, not a treatment action.",
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "answer": {"type": "string", "description": "The natural conversational reply. Cite relevant provided passages inline as [V1], etc. Ask a concise clarification when needed."},
                "source_ids": source_ids,
            },
            "required": ["answer", "source_ids"],
        },
    }


def extract_reply_input(response):
    """Never reinterpret free-text, truncated, or refused output as a reply."""
    if getattr(response, "stop_reason", None) in ("max_tokens", "refusal"):
        raise ValueError("Model reply was truncated or refused")
    calls = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
    if len(calls) != 1 or getattr(calls[0], "name", None) != REPLY_TOOL_NAME:
        raise ValueError("Expected one submit_field_reply tool response; received ordinary text or unexpected tool output")
    payload = getattr(calls[0], "input", None)
    if not isinstance(payload, dict):
        raise ValueError("The reply tool input must be an object")
    return json.dumps(payload, ensure_ascii=False)


def clean_history(history):
    """Accept conversational text only; clients cannot supply system messages."""
    messages = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})
    # Retain user/assistant pairs rather than starting on an orphaned answer.
    messages = messages[-16:]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def knowledge_query(question, history):
    """Carry the presenting complaint into retrieval for short follow-ups.

    Only user observations enter the search, never an earlier AI diagnosis.
    """
    observations = [m["content"][:1000] for m in history if m["role"] == "user"]
    selected = observations[:1] + observations[-3:] if len(observations) > 3 else observations
    selected = list(dict.fromkeys(selected + [question.strip()]))
    return "\n".join(selected)


def memory_scope(user_id, operation, sheet_id):
    """Avoid pooling anonymous/default users or different operations/sheets.

    These are caller-supplied namespaces, not authentication. Existing API
    callers still need authentication before cross-session memory is enabled.
    """
    if not user_id or user_id in ("default", "HerdMate", operation):
        return None
    return hashlib.sha256(json.dumps(
        [user_id, operation or "", sheet_id or ""], separators=(",", ":")
    ).encode()).hexdigest()


def filter_memories(candidates, scope_id, session_id, question, history):
    """No current-session echoes or legacy AI answers presented as outcomes."""
    if not scope_id or not session_id:
        return []
    current_questions = {question.strip()}
    current_questions.update(m["content"] for m in history if m["role"] == "user")
    accepted = []
    for doc, meta in candidates:
        meta = meta or {}
        if meta.get("scope_id") != scope_id or not meta.get("session_id"):
            continue
        if meta["session_id"] == session_id:
            continue
        if meta.get("type") != "field_conversation":
            continue
        # Legacy records have no provenance fields and are deliberately excluded.
        if not meta.get("question") or meta["question"].strip() in current_questions:
            continue
        accepted.append((doc, meta))
    return accepted


def parse_grounded_answer(raw_text, references):
    """Expose only supplied references explicitly selected AND cited in text.

    This validates citation provenance, not clinical correctness or semantic
    support. Those still require retrieval evaluation and veterinary review.
    """
    text = raw_text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("DAVE answer must be a JSON object")
    answer, source_ids = payload.get("answer"), payload.get("source_ids")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("DAVE answer is missing")
    if not isinstance(source_ids, list) or any(not isinstance(s, str) for s in source_ids):
        raise ValueError("DAVE source_ids must be a list of reference IDs")
    # A grouped citation names the same explicit passage IDs; canonicalize it
    # without accepting an unknown ID or inferring a missing source selection.
    answer = re.sub(r"\[(V\d+(?:\s*,\s*V\d+)+)\]",
                    lambda match: " ".join("[" + item.strip() + "]" for item in match[1].split(",")), answer)
    cited_ids = re.findall(r"\[(V\d+)\]", answer)
    if any(s not in references for s in source_ids + cited_ids):
        raise ValueError("DAVE cited a reference that was not supplied")
    if set(source_ids) != set(cited_ids):
        raise ValueError("DAVE reference list does not match the answer citations")
    sources, details = [], []
    # Use first-citation order; duplicate chunks from one source share a number.
    for source_id in dict.fromkeys(cited_ids):
        source = references[source_id]
        if source not in sources:
            sources.append(source)
            details.append({"id": str(len(sources)), "source": source})
    source_numbers = {source: str(i + 1) for i, source in enumerate(sources)}
    answer = re.sub(r"\[(V\d+)\]", lambda m: "[" + source_numbers[references[m[1]]] + "]", answer)
    return answer.strip(), sources, details
