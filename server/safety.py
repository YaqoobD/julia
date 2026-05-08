"""Mode-3 safety router.

Pre-Claude regex check for the three categories that must never reach the model:
under-18 age statements, crisis (self-harm / suicidal ideation / abuse disclosure),
and dosing / medication-naming requests. A hit short-circuits the turn with a
hard-refuse template that signposts the right service.

Iterate the patterns based on stage testing. Be conservative — false positives
here only mean the user gets a (warm) signpost instead of an answer; false
negatives let the model engage with content it shouldn't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Mode3Hit:
    category: str       # "under_18" | "crisis_distress" | "crisis_abuse" | "dosing"
    pattern: str        # which pattern matched (for logging)
    fragment: str       # the matched text


# --- Under-18 patterns -------------------------------------------------------
# Match explicit numeric age statements; the integer is checked at runtime so
# "I'm 45" doesn't fire.
_AGE_PATTERNS = [
    re.compile(r"\bi[’'`]?m\s+(\d{1,2})\b", re.I),
    re.compile(r"\bi\s+am\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:yo|y\.?o\.?|years?\s*old)\b", re.I),
    re.compile(r"\baged?\s+(\d{1,2})\b", re.I),
]

# --- Crisis patterns ---------------------------------------------------------
# Split into two buckets so we can route to the right helpline.
_CRISIS_DISTRESS_PATTERNS = [
    re.compile(
        r"\b(?:want\s+to\s+die|kill\s+myself|end\s+(?:my\s+life|it\s+all)|"
        r"hurt\s+myself|self[-\s]harm|suicid)\w*",
        re.I,
    ),
]

_CRISIS_ABUSE_PATTERNS = [
    re.compile(
        r"\b(?:abus(?:e|ed|ing)|domestic\s+violence|raped?|"
        r"forced\s+(?:me|to))\b",
        re.I,
    ),
]

# --- Dosing / medication patterns -------------------------------------------
_DOSING_PATTERNS = [
    re.compile(r"\bhow\s+much\s+(?:hrt|estrogen|oestrogen|testosterone|progesterone)\b", re.I),
    re.compile(r"\bwhat\s+dose\s+of\b", re.I),
    re.compile(r"\b(?:should\s+i|can\s+i)\s+take\s+\d+\s*(?:mg|mcg|ml|iu)\b", re.I),
    # Specific drug names — extend as encountered during stage testing.
    re.compile(r"\b(?:vagifem|estring|premarin|estradiol|gabapentin)\b", re.I),
]


def check_mode_3(user_text: str) -> Mode3Hit | None:
    for pat in _AGE_PATTERNS:
        m = pat.search(user_text)
        if m:
            try:
                age = int(m.group(1))
            except (ValueError, IndexError):
                continue
            if age < 18:
                return Mode3Hit("under_18", pat.pattern, m.group(0))

    for pat in _CRISIS_DISTRESS_PATTERNS:
        m = pat.search(user_text)
        if m:
            return Mode3Hit("crisis_distress", pat.pattern, m.group(0))

    for pat in _CRISIS_ABUSE_PATTERNS:
        m = pat.search(user_text)
        if m:
            return Mode3Hit("crisis_abuse", pat.pattern, m.group(0))

    for pat in _DOSING_PATTERNS:
        m = pat.search(user_text)
        if m:
            return Mode3Hit("dosing", pat.pattern, m.group(0))

    return None


# --- Hard-refuse templates ---------------------------------------------------

UNDER_18_TEMPLATE = (
    "Thanks for trusting me with that. This is an 18+ service, so I'm not the right place "
    "for this conversation — but Brook (brook.org.uk) is free, confidential, and exactly "
    "designed for questions like yours."
)

CRISIS_TEMPLATE_DISTRESS = (
    "I'm really glad you said that out loud. What you're feeling is serious and it deserves "
    "someone who can really sit with you — that's not me. The Samaritans are free, confidential, "
    "and answer 24/7 on 116 123. Please call them now if you can."
)

CRISIS_TEMPLATE_ABUSE = (
    "What you're describing isn't something to navigate alone. The National Domestic Abuse "
    "Helpline is free, confidential, and answer 24/7 on 0808 2000 247. They can help with "
    "next steps, whatever those look like for you."
)

DOSING_TEMPLATE = (
    "I can't help with specific doses or medications — that's a conversation for whoever "
    "is prescribing for you, because the right answer depends on you specifically. Your GP "
    "or the prescriber who started you on it is the right person to ask."
)

_TEMPLATES = {
    "under_18": UNDER_18_TEMPLATE,
    "crisis_distress": CRISIS_TEMPLATE_DISTRESS,
    "crisis_abuse": CRISIS_TEMPLATE_ABUSE,
    "dosing": DOSING_TEMPLATE,
}


def template_for(category: str) -> str:
    return _TEMPLATES[category]
