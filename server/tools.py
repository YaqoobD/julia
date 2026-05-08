"""Tool definitions and runner for the Anthropic tool-use loop.

Three tools are exposed to Claude:

* `find_product` — search the curated index in `data/products.json` for products
  matching a category and a set of attribute/use-case tags. Returns up to two
  results, ranked by tag overlap.
* `find_practitioner` — search the (demo-only fake) index in
  `data/practitioners.json` for sex therapists matching a specialty and a
  location (city or "online"). Returns up to two results.
* `respond` — the only way Julia ends a turn. Carries the spoken text, the list
  of `source_page_id`s that grounded any factual claims, and optional
  product / practitioner suggestions ({id, why_this_one}).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = PROJECT_ROOT / "data" / "products.json"
PRACTITIONERS_PATH = PROJECT_ROOT / "data" / "practitioners.json"

MAX_PRODUCT_RESULTS = 2
MAX_PRACTITIONER_RESULTS = 2

with PRODUCTS_PATH.open(encoding="utf-8") as f:
    _PRODUCTS: list[dict[str, Any]] = json.load(f)

_PRODUCT_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in _PRODUCTS}

with PRACTITIONERS_PATH.open(encoding="utf-8") as f:
    _PRACTITIONERS: list[dict[str, Any]] = json.load(f)["practitioners"]

_PRACTITIONER_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in _PRACTITIONERS}


def get_product(product_id: str) -> dict[str, Any] | None:
    return _PRODUCT_BY_ID.get(product_id)


def get_practitioner(practitioner_id: str) -> dict[str, Any] | None:
    return _PRACTITIONER_BY_ID.get(practitioner_id)


def find_product(category: str, attributes: list[str]) -> list[dict[str, Any]]:
    """Return up to MAX_PRODUCT_RESULTS products matching `category`,
    ranked by number of overlapping attribute / use-case-tag hits."""
    cat = (category or "").strip().lower()
    wanted = {a.strip().lower() for a in (attributes or []) if a and a.strip()}

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, p in enumerate(_PRODUCTS):
        if p.get("category", "").lower() != cat:
            continue
        haystack = {t.lower() for t in p.get("attributes", [])}
        haystack.update(t.lower() for t in p.get("use_case_tags", []))
        score = len(wanted & haystack)
        scored.append((-score, idx, p))

    scored.sort()
    return [p for _, _, p in scored[:MAX_PRODUCT_RESULTS]]


def find_practitioner(specialty: str, location: str) -> list[dict[str, Any]]:
    """Return up to MAX_PRACTITIONER_RESULTS therapists matching `location`
    (city name, or "online"), ranked by overlap of `specialty` against each
    practitioner's specialties[]."""
    loc = (location or "").strip().lower()
    wanted = {s.strip().lower() for s in (specialty or "").split(",") if s.strip()}
    if not wanted:
        wanted = {(specialty or "").strip().lower()} if specialty else set()
    wanted.discard("")

    online_only = loc in {"online", "uk", "anywhere", "remote", "video"}

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, p in enumerate(_PRACTITIONERS):
        city = p.get("city", "").lower()
        if online_only:
            if not p.get("online_available"):
                continue
        else:
            city_match = loc and (loc in city or city.startswith(loc))
            if not city_match:
                continue
        haystack = {t.lower() for t in p.get("specialties", [])}
        score = len(wanted & haystack)
        scored.append((-score, idx, p))

    scored.sort()
    return [p for _, _, p in scored[:MAX_PRACTITIONER_RESULTS]]


# --- Anthropic tool schemas --------------------------------------------------

FIND_PRODUCT_SCHEMA: dict[str, Any] = {
    "name": "find_product",
    "description": (
        "Search the curated product index. Call this ONLY when the user "
        "has expressed a specific need a product would address (not just a topic). "
        "Returns up to two products. Pick at most one to surface to the user via "
        "`respond.product_suggestion`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": (
                    "Product category. One of: lube, moisturiser, vibrator, kit, "
                    "pelvic-floor, massage-oil."
                ),
            },
            "attributes": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Attributes or use-case tags that should bias the search, e.g. "
                    "['glycerin-free', 'menopause', 'sensitive'] or ['couples', "
                    "'introducing-toys', 'beginner']."
                ),
            },
        },
        "required": ["category", "attributes"],
    },
}

FIND_PRACTITIONER_SCHEMA: dict[str, Any] = {
    "name": "find_practitioner",
    "description": (
        "Search the curated UK sex-therapist directory. Call this ONLY when the "
        "user has expressed they want professional human support (a therapist, "
        "counsellor, or sexologist) — not just because they shared a topic. "
        "Before calling, you MUST know either the user's city OR that they "
        "are open to online sessions; if neither is on the table, ask first "
        "and call on the next turn. Returns up to two practitioners. Pick at "
        "most one to surface to the user via `respond.practitioner_suggestion`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "specialty": {
                "type": "string",
                "description": (
                    "What the practitioner needs to be good at, expressed in tags or "
                    "short phrases the directory uses, e.g. 'couples, desire, "
                    "communication' or 'trauma, vaginismus' or 'men's-sexual-health, "
                    "performance-anxiety'. Comma-separated is fine."
                ),
            },
            "location": {
                "type": "string",
                "description": (
                    "Either a UK city name (e.g. 'London', 'Manchester', 'Bristol') "
                    "OR the literal string 'online' if the user is open to / "
                    "asking for video sessions. Match what the user actually told "
                    "you — don't guess."
                ),
            },
        },
        "required": ["specialty", "location"],
    },
}

RESPOND_SCHEMA: dict[str, Any] = {
    "name": "respond",
    "description": (
        "End the turn by speaking to the user. This is the ONLY way to send text "
        "back. Always call exactly once per turn, after any optional `find_product` "
        "or `find_practitioner` call. `sources_used` MUST list the source_page_ids "
        "of any retrieved <context> blocks you drew factual claims from (empty "
        "list if your reply was purely persona/reframe). `product_suggestion` is "
        "null unless you decided to surface a product. `practitioner_suggestion` "
        "is null unless you decided to surface a therapist. `suggested_replies` "
        "is an optional list of 2–3 short candidate user replies that branch "
        "the conversation forward."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "speech": {
                "type": "string",
                "description": "What Julia says to the user. Short, kind, no bulleted lists.",
            },
            "sources_used": {
                "type": "array",
                "items": {"type": "string"},
                "description": "source_page_ids of retrieved context blocks used for factual claims. Empty if none.",
            },
            "product_suggestion": {
                "type": ["object", "null"],
                "properties": {
                    "id": {"type": "string"},
                    "why_this_one": {"type": "string"},
                },
                "required": ["id", "why_this_one"],
                "description": "Optional product to surface to the user, or null.",
            },
            "practitioner_suggestion": {
                "type": ["object", "null"],
                "properties": {
                    "id": {"type": "string"},
                    "why_this_one": {"type": "string"},
                },
                "required": ["id", "why_this_one"],
                "description": (
                    "Optional therapist to surface to the user, or null. The id "
                    "MUST come from a `find_practitioner` result on this turn."
                ),
            },
            "suggested_replies": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional. 2–3 short candidate next replies the user might give, "
                    "written in the user's first-person voice (5–12 words each). They "
                    "should branch the conversation forward — open and exploratory, "
                    "not yes/no. Examples: \"Tell me more about HRT\", \"I'd want to "
                    "try the glycerin-free option first\", \"What should I look for?\". "
                    "Empty list when not appropriate (e.g., final turn, sensitive refusal)."
                ),
            },
        },
        "required": ["speech", "sources_used", "product_suggestion"],
    },
}

TOOL_SCHEMAS: list[dict[str, Any]] = [FIND_PRODUCT_SCHEMA, FIND_PRACTITIONER_SCHEMA, RESPOND_SCHEMA]
