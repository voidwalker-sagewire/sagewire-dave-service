"""Conversation and citation policy without network or model dependencies."""

import hashlib
import json
import re


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
