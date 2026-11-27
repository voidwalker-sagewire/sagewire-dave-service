"""Small, reviewed product-identity index, separate from clinical prescribing.

Names come only from user messages. A name match identifies a reference, never
verifies the bottle, animal class, treatment indication, or veterinary protocol.
No doses, withdrawal calculations, or treatment authorization are stored here.
Label summaries were checked on 2026-11-27; update them with the linked labels.
"""
from dataclasses import dataclass
import re

LABEL_REVIEW_DATE = "2026-11-27"


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    pattern: str
    source: str
    facts: str
    explanation: str


PRODUCTS = (
    Product(
        "vetameg", "Vetameg injectable", r"\bvetameg\b",
        "https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=c166af9b-eed6-440d-9317-c7f46c496c85",
        "Vetameg injectable contains flunixin meglumine, an NSAID, not a vitamin supplement. "
        "The reviewed injection label specifies IV-only use in cattle; the horse IM route must not be transferred to cattle. "
        "Its cattle indications concern specified fever conditions and inflammation in endotoxemia. "
        "Do not use in bulls intended for breeding. Other NSAIDs or corticosteroids require veterinary review. "
        "Banamine injectable has the same active drug: two brand names do not mean two different drugs.",
        "Vetameg injectable contains **flunixin meglumine**, an anti-inflammatory medicine (NSAID). "
        "It is not a vitamin supplement. Its cattle label covers specified fever conditions and inflammation in endotoxemia, "
        "and specifies **IV-only administration in cattle**. Whether it fits an individual animal needs a separate treatment decision.",
    ),
    Product(
        "banamine", "Banamine injectable", r"\bbanamine\b(?!\s+(?:transdermal|paste))",
        "https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=2e531abd-b3ec-4819-81a3-f51e6867658c",
        "The reviewed Banamine INJECTABLE label contains flunixin meglumine, an NSAID, not firocoxib. "
        "It specifies IV-only cattle administration, unlike the horse IM route. "
        "These facts do not describe Banamine Transdermal or other formulations. "
        "Vetameg injectable contains the same active drug. Do not count these brands as separate medicines. "
        "Do not use the reviewed injectable in bulls intended for breeding.",
        "For **Banamine injectable**, the active drug is **flunixin meglumine**, an NSAID. "
        "Its cattle label covers specified fever conditions and inflammation in endotoxemia and specifies **IV-only use in cattle**. "
        "Other forms, including Banamine Transdermal, have separate labels; the bottle's formulation matters.",
    ),
    Product(
        "draxxin_kp", "Draxxin KP", r"\bdraxxin\s*kp\b",
        "https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=044385d5-c9eb-4a44-9ffd-2b68092262fe",
        "Draxxin KP combines tulathromycin (antibiotic) with ketoprofen (NSAID). "
        "Its cattle indication is treatment of BRD and associated fever, not a general pinkeye or pain indication. "
        "The reviewed label specifies subcutaneous neck injection, not IV or IM. "
        "It excludes reproducing animals over one year of age; review the complete animal-class restrictions. "
        "Do not transfer plain Draxxin indications to this combination or authorize dart delivery from a label dose alone.",
        "**Draxxin KP contains two medicines:** tulathromycin, an antibiotic, and ketoprofen, "
        "an NSAID with pain-, inflammation- and fever-reducing effects. **KP is the ketoprofen component.** "
        "The combination's cattle label is for **bovine respiratory disease (BRD) and the fever associated with it**. "
        "That does not make it a general-purpose pain treatment or establish that it is appropriate for pinkeye. "
        "Its labeled cattle route is subcutaneous. Suitability for a particular animal is a separate question.",
    ),
    Product(
        "draxxin", "Draxxin injectable (plain product)", r"\bdraxxin\b(?!\s*(?:kp\b|25\b))",
        "https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=258f413b-95e0-4082-a150-85af60b2af80",
        "The reviewed plain Draxxin injectable contains tulathromycin, not ketoprofen. "
        "It is a different product from Draxxin KP and Draxxin 25. "
        "Its beef/non-lactating dairy cattle indications include BRD, specified bacterial pinkeye and foot rot. "
        "Review the exact animal-class restrictions and subcutaneous cattle route; never copy its label to the KP combination.",
        "**Plain Draxxin injectable contains tulathromycin**, an antibiotic. "
        "It does not contain the ketoprofen anti-inflammatory found in **Draxxin KP**. "
        "Its label includes BRD, specified bacterial pinkeye and foot rot in the stated cattle classes. "
        "Those are distinct products with separate labels; Draxxin 25 is another formulation. "
        "Which full name is on your bottle?",
    ),
)
BY_KEY = {product.key: product for product in PRODUCTS}


def normalize_name_text(text):
    text = text.lower().replace("®", " ")
    # Accept explicitly spelled names, without fuzzy-matching another drug.
    text = re.sub(r"\b(?:[a-z][\s-]+){2,}[a-z]\b",
                  lambda m: re.sub(r"[\s-]", "", m[0]), text)
    return re.sub(r"[\s_-]+", " ", text).strip()


def named_products(text):
    normalized = normalize_name_text(text)
    return [product.key for product in PRODUCTS if re.search(product.pattern, normalized)]


def medication_context(question, history):
    """Retain user-specified names/corrections without importing AI claims.

    The returned list is a record of named references, not a statement that all
    products were administered or that the last product is the current bottle.
    """
    keys = []
    for message in [*history, {"role": "user", "content": question}]:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = message.get("content")
        if not isinstance(text, str):
            continue
        for key in named_products(text):
            if key in keys:
                keys.remove(key)
            keys.append(key)
    return keys


def medication_references(keys):
    return [(BY_KEY[key].facts, {"source": BY_KEY[key].source,
                               "medication_id": key, "label_review_date": LABEL_REVIEW_DATE})
            for key in keys]


def medication_instructions(keys):
    if not keys:
        return ""
    names = ", ".join(BY_KEY[key].name for key in keys)
    return (
        "\n\n--- USER-NAMED PRODUCT REFERENCES ---\n"
        f"Names found in user messages: {names}. These are references to products, not proof they were given together.\n"
        "Use the reviewed label identities below instead of earlier AI product claims. "
        "Preserve a clearly spelled/corrected name through short follow-ups. An uncertain later dictation "
        "may refer to an earlier named product; ask a brief confirmation rather than denying it exists. "
        "Do not silently identify a different bottle from a similar-sounding name. "
        "Answer general ingredient/use questions from these references without demanding a weight or dose label. "
        "Individual treatment, timing, combinations, route, dose, site limits and withdrawal decisions still "
        "need the exact formulation, animal class, indication and veterinary protocol. "
        "Do not treat a standard label summary as permission for off-label use or dart delivery. "
        "If Vetameg and Banamine mean their injectable forms, both contain flunixin; "
        "do not describe them as a vitamin plus an NSAID or plan two separate flunixin doses. "
        "Cite the matching label for medication facts, and do not invent unsupported treatment instructions."
    )


# Deliberately narrow routing: clinical questions stay on the clinical path.
# This is an information lookup, not an emergency classifier or a safety filter.
_CLINICAL = re.compile(
    r"\d|\b(?:dose|dosage|cc|ml|give|giving|gave|shot|inject|injection|dart|"
    r"bull|cow|calf|steer|heifer|animal|pink\s*eye|wound|bleed\w*|diarrhea|"
    r"sick|down|bloat\w*|collapse\w*|breath\w*|cough\w*|overdose|"
    r"pregnan\w*|withdraw\w*|slaughter|milk|breeding|not|don.t|instead|"
    r"together|combine|mix|already)\b", re.I)
_EDUCATION = re.compile(r"\b(?:what\b|used for|ingredients?|contain\w*|kp|pain|inflamm\w*)", re.I)
_FOLLOWUP_WORDS = set(
    "what does do the kp component ingredient it that one for is a pain medicine "
    "medication mediation help helps with at all or an inflammation just respiratory "
    "like fucking fuck f dave please tell me about and yes no".split()
)
_INFORMATION_WORDS = _FOLLOWUP_WORDS | set(
    "used uses use contains contain ingredients ingredient antibiotic anti inflammatory "
    "can you explain actually part of vetameg banamine draxxin draxxinkp".split()
)


def _information_words_only(text):
    normalized = normalize_name_text(text)
    words = set(re.findall(r"[a-z]+", normalized))
    allowed = _INFORMATION_WORDS | ({"jackson"} if "draxxin_kp" in named_products(text) else set())
    return bool(words) and words <= allowed


def product_information_reply(question, keys, citation_ids, *, image_present=False, history=()):
    """Answer narrow identity lookups from checked text without model guessing.

    citation_ids maps catalog keys to the exact V IDs supplied to the existing
    provenance validator. Return None for treatment, ambiguity or images.
    """
    if image_present or len(question.split()) > 60:
        return None
    current = named_products(question)
    normalized = normalize_name_text(question)
    if re.search(r"\b(?:not|don.t|instead|transdermal|paste|between|compare|versus|difference)\b", normalized):
        return None
    if (set(current) == {"vetameg", "banamine"}
            and set(re.findall(r"[a-z]+", normalized)) <= {"vetameg", "banamine", "or", "dave"}):
        ids = [citation_ids[key] for key in ("vetameg", "banamine")]
        return {"answer": (
            "If you mean **Vetameg injectable / Banamine injectable**, those are two brand names "
            "for **flunixin meglumine**, an NSAID—not a vitamin and a separate pain medicine. "
            f"[{ids[0]}] [{ids[1]}]\n\n"
            "Are you naming one product, or have you given both? Don't count them as two different drugs "
            "when discussing the treatment plan with your vet."
        ), "source_ids": ids}
    if _CLINICAL.search(question):
        return None
    # Only wholly recognized information questions can bypass clinical triage.
    # Unknown symptom wording, instructions or another product stay with the
    # model, even when an exact product name occurs elsewhere in the sentence.
    if not _information_words_only(question):
        return None
    # An unknown named drug must not inherit the last known drug's answer.
    # Only a small set of ordinary pronoun/KP follow-up words can use history.
    if not current and not set(re.findall(r"[a-z]+", normalized)) <= _FOLLOWUP_WORDS:
        return None
    focus = []
    for message in history:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = message.get("content")
        if not isinstance(text, str):
            continue
        explicit = named_products(text)
        if explicit:
            focus = explicit
        elif not _information_words_only(text):
            focus = []
    candidates = current or focus
    if len(candidates) != 1:
        return None
    key = candidates[0]
    # A product name alone is a valid correction/lookup. Other follow-ups must
    # actually ask for information, not merely mention a past product.
    name_only = re.sub(r"\bdave\b|[^a-z]", "", normalized)
    recognized_names = {"vetameg", "banamine", "draxxin", "draxxinkp"}
    if name_only not in recognized_names and not _EDUCATION.search(question):
        return None
    # KP by itself must never latch onto a different, most-recent product.
    if re.search(r"\bkp\b", normalized) and key != "draxxin_kp":
        return None
    explanation = BY_KEY[key].explanation
    if key == "draxxin_kp":
        if name_only == "draxxinkp":
            explanation = (
                "**Draxxin KP**, understood. It combines **tulathromycin** (antibiotic) "
                "with **ketoprofen** (NSAID). Its cattle label is for BRD and associated fever; "
                "it is a separate product from plain Draxxin."
            )
        elif "kp" in normalized and re.search(r"\b(?:component|what does)\b", normalized):
            explanation = (
                "**KP is ketoprofen**, the NSAID component. It has pain-, inflammation- and "
                "fever-reducing effects; tulathromycin is the antibiotic component. "
                "Draxxin KP's cattle label is specifically for BRD and its associated fever."
            )
        elif re.search(r"\b(?:pain|inflammation)\b", normalized):
            explanation = (
                "**Yes—the ketoprofen component is an NSAID**, with pain-, inflammation- and "
                "fever-reducing effects. Draxxin KP is not just an antibiotic. "
                "Its cattle label is specifically for **BRD and associated fever**; that does not "
                "establish that the combination is appropriate for another painful condition."
            )
    reference_id = citation_ids[key]
    return {"answer": explanation + f" [{reference_id}]", "source_ids": [reference_id]}
