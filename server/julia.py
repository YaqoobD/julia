"""Julia turn handler — the brain.

`handle_turn(session_id, user_text, channel)` runs:

  1. mode-3 pre-check — short-circuit with hard-refuse template, no Claude call
  2. retrieval — embed last 3 user turns, top-K corpus chunks
  3. Anthropic tool-use loop with prompt caching:
       call → may call `find_product` → re-call → must call `respond`
  4. validate `sources_used` (subset of retrieved page ids) and
     `product_suggestion.id` (must come from this turn's find_product results)
  5. append flattened user/assistant text to session memory, return TurnResult

Session memory is an in-memory dict keyed by session_id. Demo-only; lost on
restart. Sessions ended by mode-3 stay ended — subsequent turns return the
same template.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from server import retrieval, safety, tools

# Yes-call triggers per system prompt §"Product recommendations". When the user's
# message clearly expresses an unmet need a product would address, we force
# Claude to call find_product as the first tool action — empirically the model
# otherwise tends to narrate a product in `speech` without calling the tool.
_YES_CALL_TRIGGERS = [
    re.compile(r"\b(?:lubes?|lubricants?|moisturisers?|moisturizers?|toys?|vibrators?|products?|"
               r"creams?|gels?)\s+(?:i(?:'ve|\s+have)?\s+)?tried\b", re.I),
    re.compile(r"\b(?:i(?:'ve|\s+have)?\s+)?tried\s+\w+(?:\s+\w+){0,5}?\s+(?:and\s+)?"
               r"(?:hasn[’'`]?t|haven[’'`]?t|didn[’'`]?t|isn[’'`]?t|aren[’'`]?t|don[’'`]?t|doesn[’'`]?t)\s+"
               r"(?:work|help|do)", re.I),
    re.compile(r"\b(?:hasn[’'`]?t|haven[’'`]?t|didn[’'`]?t)\s+(?:really\s+)?(?:work|help)\w*", re.I),
    re.compile(r"\bwant\s+(?:something|a\s+\w+)\s+(?:gentler|different|better|that\b)", re.I),
    re.compile(r"\blooking\s+for\s+(?:a|some|something)\b", re.I),
    re.compile(r"\brecommend\s+(?:a|me|something)\b", re.I),
]


def _is_yes_call(user_text: str) -> bool:
    return any(p.search(user_text) for p in _YES_CALL_TRIGGERS)


# Practitioner-call triggers: explicit asks for a human professional. The
# system prompt also tells Claude to surface a practitioner on Mode-2 cues
# (persistent pain unresolved by GP, long-term desire mismatch, post-trauma
# reintroduction). Regex here is the explicit-ask backstop only.
_PRACTITIONER_TRIGGERS = [
    re.compile(r"\b(?:therapist|sexologist|counsell?ors?|counsell?ing)\b", re.I),
    re.compile(r"\bcouples?\s+(?:therapy|counsell?ing)\b", re.I),
    re.compile(r"\b(?:see|find|need)\s+(?:a|someone)\s+(?:professional|specialist|therapist|sexologist|expert)\b", re.I),
    re.compile(r"\bprofessional\s+help\b", re.I),
    re.compile(r"\b(?:nearest|in\s+my\s+area|near\s+me|local)\b.*\b(?:therapist|sexologist|counsell?or|specialist|professional)\b", re.I),
    re.compile(r"\b(?:therapist|sexologist|counsell?or|specialist|professional)\b.*\b(?:nearest|in\s+my\s+area|near\s+me|local)\b", re.I),
]


def _is_practitioner_call(user_text: str) -> bool:
    return any(p.search(user_text) for p in _PRACTITIONER_TRIGGERS)


# A location signal is one of: an online/video cue, a directory city by name,
# or a generic "I'm in X" / "based in X" / "live in X" phrasing. We use this
# to decide whether to FORCE find_practitioner. Without a location signal
# the regex backstop alone isn't enough to call the tool — the model would
# have to invent a location, which is exactly the failure mode we saw on
# stage testing (Helena Marsh surfaced before user said anywhere).
_LOCATION_SIGNAL = re.compile(
    r"\b(?:online|video|remote|virtual|over\s+(?:the\s+)?(?:phone|video)|"
    r"london|manchester|birmingham|bristol|glasgow|edinburgh|"
    r"(?:i[’'`]?m|i\s+am)\s+in\s+\w+|based\s+in\s+\w+|live\s+in\s+\w+)\b",
    re.I,
)


def _has_location_signal(user_text: str) -> bool:
    return bool(_LOCATION_SIGNAL.search(user_text))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "server" / "system_prompt.md"

# MODEL = "claude-sonnet-4-5"
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
RETRIEVAL_K = 5
HISTORY_USER_TURNS_FOR_QUERY = 3
PRODUCT_HARD_CAP_PER_SESSION = 2
PRACTITIONER_HARD_CAP_PER_SESSION = 2

SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


@dataclass
class ProductSuggestion:
    id: str
    why_this_one: str


@dataclass
class PractitionerSuggestion:
    id: str
    why_this_one: str


@dataclass
class TurnResult:
    speech: str
    sources_used: list[str]
    product_suggestion: ProductSuggestion | None
    ended: bool
    mode_3_category: str | None = None
    anthropic_called: bool = True
    retrieved_page_ids: list[str] = field(default_factory=list)
    suggested_replies: list[str] = field(default_factory=list)
    practitioner_suggestion: PractitionerSuggestion | None = None


SUGGESTED_REPLIES_MAX = 3
SUGGESTED_REPLY_MAX_LEN = 120


def _validate_suggested_replies(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not s:
            continue
        if len(s) > SUGGESTED_REPLY_MAX_LEN:
            s = s[:SUGGESTED_REPLY_MAX_LEN].rstrip()
        out.append(s)
        if len(out) >= SUGGESTED_REPLIES_MAX:
            break
    return out


@dataclass
class Session:
    history: list[dict[str, Any]] = field(default_factory=list)  # [{role, content: str}]
    product_count: int = 0
    practitioner_count: int = 0
    ended: bool = False


_SESSIONS: dict[str, Session] = {}
_CLIENT: Anthropic | None = None


def _client() -> Anthropic:
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip('"')
        if not api_key or api_key.upper().startswith("PLACEHOLDER"):
            raise RuntimeError("ANTHROPIC_API_KEY missing or placeholder.")
        _CLIENT = Anthropic(api_key=api_key)
    return _CLIENT


def _get_session(session_id: str) -> Session:
    s = _SESSIONS.get(session_id)
    if s is None:
        s = Session()
        _SESSIONS[session_id] = s
    return s


def reset_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def _retrieval_query(session: Session, user_text: str) -> str:
    """Concatenate the last N user turns (including this one) for retrieval."""
    prior_users = [m["content"] for m in session.history if m["role"] == "user"]
    recent = prior_users[-(HISTORY_USER_TURNS_FOR_QUERY - 1):] if HISTORY_USER_TURNS_FOR_QUERY > 1 else []
    parts = recent + [user_text]
    return " \n".join(parts)


def _format_context_block(chunks: list[retrieval.Chunk]) -> str:
    if not chunks:
        return "<context_set>(no retrieved context this turn)</context_set>"
    rendered = []
    for c in chunks:
        rendered.append(
            f'<context source_id="{c.source_page_id}" title="{c.source_title}">\n'
            f'{c.text}\n'
            f'</context>'
        )
    return "<context_set>\n" + "\n\n".join(rendered) + "\n</context_set>"


def _system_blocks(context_text: str, channel: str = "web") -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"# Channel\n\nchannel: {channel}\n\n"
                "# Retrieved context for this turn\n\n"
                "Use only material in the blocks below to ground factual claims. "
                "Cite the matching source_id values in `respond.sources_used`.\n\n"
                + context_text
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _history_to_messages(session: Session) -> list[dict[str, Any]]:
    return [{"role": m["role"], "content": m["content"]} for m in session.history]


def _validate_sources(raw: Any, retrieved_page_ids: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for v in raw:
        if isinstance(v, str) and v in retrieved_page_ids and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _validate_product(raw: Any, eligible_ids: set[str]) -> ProductSuggestion | None:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    why = raw.get("why_this_one")
    if not isinstance(pid, str) or not isinstance(why, str):
        return None
    if pid not in eligible_ids:
        return None
    return ProductSuggestion(id=pid, why_this_one=why.strip())


def _validate_practitioner(raw: Any, eligible_ids: set[str]) -> PractitionerSuggestion | None:
    if not isinstance(raw, dict):
        return None
    pid = raw.get("id")
    why = raw.get("why_this_one")
    if not isinstance(pid, str) or not isinstance(why, str):
        return None
    if pid not in eligible_ids:
        return None
    return PractitionerSuggestion(id=pid, why_this_one=why.strip())


def _mode_3_result(session: Session, hit: safety.Mode3Hit, user_text: str) -> TurnResult:
    template = safety.template_for(hit.category)
    session.history.append({"role": "user", "content": user_text})
    session.history.append({"role": "assistant", "content": template})
    session.ended = True
    return TurnResult(
        speech=template,
        sources_used=[],
        product_suggestion=None,
        ended=True,
        mode_3_category=hit.category,
        anthropic_called=False,
        retrieved_page_ids=[],
    )


def _ended_session_result(session: Session) -> TurnResult:
    last = next(
        (m["content"] for m in reversed(session.history) if m["role"] == "assistant"),
        "This conversation has ended. Please reach the service I pointed you to.",
    )
    return TurnResult(
        speech=last,
        sources_used=[],
        product_suggestion=None,
        ended=True,
        anthropic_called=False,
    )


async def handle_turn(session_id: str, user_text: str, channel: str = "web") -> TurnResult:
    session = _get_session(session_id)

    if session.ended:
        return _ended_session_result(session)

    hit = safety.check_mode_3(user_text)
    if hit is not None:
        return _mode_3_result(session, hit, user_text)

    query = _retrieval_query(session, user_text)
    chunks = retrieval.retrieve(query, k=RETRIEVAL_K)
    retrieved_page_ids = {c.source_page_id for c in chunks}
    context_text = _format_context_block(chunks)

    system_blocks = _system_blocks(context_text, channel=channel)
    messages: list[dict[str, Any]] = _history_to_messages(session)
    messages.append({"role": "user", "content": user_text})

    eligible_product_ids: set[str] = set()
    eligible_practitioner_ids: set[str] = set()
    find_product_calls = 0
    find_practitioner_calls = 0
    speech: str | None = None
    sources_used: list[str] = []
    product_suggestion: ProductSuggestion | None = None
    practitioner_suggestion: PractitionerSuggestion | None = None
    suggested_replies: list[str] = []
    debug = os.environ.get("IRIS_DEBUG") == "1"

    client = _client()
    can_use_product = session.product_count < PRODUCT_HARD_CAP_PER_SESSION
    can_use_practitioner = session.practitioner_count < PRACTITIONER_HARD_CAP_PER_SESSION
    # An explicit practitioner ask outranks a product yes-call — but only
    # when a location signal is also on the table. Without a location, the
    # system prompt rule says the model should ask first, so we leave
    # tool_choice open and let the model decide. Forcing the tool with no
    # location forces it to fabricate one, which surfaced the wrong
    # practitioner in stage testing.
    if (
        can_use_practitioner
        and _is_practitioner_call(user_text)
        and _has_location_signal(user_text)
    ):
        tool_choice: dict[str, Any] = {"type": "tool", "name": "find_practitioner"}
    elif can_use_product and _is_yes_call(user_text):
        tool_choice = {"type": "tool", "name": "find_product"}
    else:
        tool_choice = {"type": "any"}

    while True:
        if debug:
            print(f"[julia-debug] tool_choice={tool_choice}, message_count={len(messages)}")
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=tools.TOOL_SCHEMAS,
            tool_choice=tool_choice,
            messages=messages,
        )
        if debug:
            print(f"[julia-debug] response stop_reason={response.stop_reason}, "
                  f"content_block_types={[getattr(b, 'type', None) for b in response.content]}")

        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_use_blocks:
            speech = "Sorry — I lost my thread there. Could you say that again?"
            break

        find_block = next((b for b in tool_use_blocks if b.name == "find_product"), None)
        find_practitioner_block = next((b for b in tool_use_blocks if b.name == "find_practitioner"), None)
        respond_block = next((b for b in tool_use_blocks if b.name == "respond"), None)

        # Either find_* tool takes priority over respond. If the same response
        # also contains a respond block, that respond was generated before the
        # model saw the tool result — its suggestion is necessarily fabricated,
        # so we ignore it and re-call after the lookup runs.
        if find_practitioner_block is not None:
            find_practitioner_calls += 1
            # Hard gate: refuse the tool call unless a location signal
            # appears in this turn or any prior user turn. Prevents the
            # "fabricated location" failure mode where the model calls
            # find_practitioner with location="online" before the user has
            # said anything about being open to online.
            has_location = _has_location_signal(user_text) or any(
                _has_location_signal(m.get("content", ""))
                for m in session.history
                if m.get("role") == "user"
            )
            if not has_location:
                tool_result_text = (
                    "find_practitioner is GATED. The user has not yet named a UK city "
                    "OR said they're open to online/video sessions. You cannot call "
                    "this tool until one of those is on the table. Call `respond` now "
                    "and end your turn with a question that asks for their location "
                    "or whether they'd consider online — for example: 'Whereabouts "
                    "are you based, or would you be open to online sessions?'. Do "
                    "NOT name any specific practitioner in `speech` this turn — talk "
                    "in categories only ('a couples-trained sex therapist'). Try the "
                    "tool again next turn once the user has answered."
                )
                practitioner_results: list[dict[str, Any]] = []
            elif find_practitioner_calls > 1 or not can_use_practitioner:
                tool_result_text = (
                    "find_practitioner is not available right now (per-turn or per-session cap reached). "
                    "Call `respond` instead and finish the turn without a practitioner suggestion."
                )
                practitioner_results = []
            else:
                args = find_practitioner_block.input or {}
                specialty = args.get("specialty", "")
                location = args.get("location", "")
                practitioner_results = tools.find_practitioner(specialty=specialty, location=location)
                for p in practitioner_results:
                    eligible_practitioner_ids.add(p["id"])
                tool_result_text = _format_find_practitioner_result(practitioner_results)
                if debug:
                    print(f"[julia-debug] find_practitioner(specialty={specialty!r}, location={location!r}) -> "
                          f"{[p['id'] for p in practitioner_results]}")

            trimmed_content = [b for b in response.content
                               if not (getattr(b, "type", None) == "tool_use" and b.name == "respond")]
            messages.append({"role": "assistant", "content": trimmed_content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": find_practitioner_block.id,
                            "content": tool_result_text,
                        }
                    ],
                }
            )
            tool_choice = {"type": "tool", "name": "respond"}
            continue

        if find_block is not None:
            find_product_calls += 1
            if find_product_calls > 1 or not can_use_product:
                tool_result_text = (
                    "find_product is not available right now (per-turn or per-session cap reached). "
                    "Call `respond` instead and finish the turn without a product suggestion."
                )
                results: list[dict[str, Any]] = []
            else:
                args = find_block.input or {}
                category = args.get("category", "")
                attributes = args.get("attributes", []) or []
                if not isinstance(attributes, list):
                    attributes = []
                results = tools.find_product(category=category, attributes=attributes)
                for p in results:
                    eligible_product_ids.add(p["id"])
                tool_result_text = _format_find_product_result(results)
                if debug:
                    print(f"[julia-debug] find_product(category={category!r}, attributes={attributes}) -> "
                          f"{[p['id'] for p in results]}")

            # Reconstruct assistant content to contain ONLY the find_product tool_use
            # (drop any premature respond block in the same response so we don't
            # owe Anthropic a tool_result for it).
            trimmed_content = [b for b in response.content
                               if not (getattr(b, "type", None) == "tool_use" and b.name == "respond")]
            messages.append({"role": "assistant", "content": trimmed_content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": find_block.id,
                            "content": tool_result_text,
                        }
                    ],
                }
            )
            tool_choice = {"type": "tool", "name": "respond"}
            continue

        if respond_block is not None:
            data = respond_block.input or {}
            speech = (data.get("speech") or "").strip()
            sources_used = _validate_sources(data.get("sources_used"), retrieved_page_ids)
            raw_product = data.get("product_suggestion")
            product_suggestion = _validate_product(raw_product, eligible_product_ids)
            raw_practitioner = data.get("practitioner_suggestion")
            practitioner_suggestion = _validate_practitioner(raw_practitioner, eligible_practitioner_ids)
            suggested_replies = _validate_suggested_replies(data.get("suggested_replies"))
            if debug:
                print(f"[julia-debug] respond.product_suggestion (raw): {raw_product}")
                print(f"[julia-debug] eligible_product_ids: {sorted(eligible_product_ids)}")
                print(f"[julia-debug] validated product_suggestion: {product_suggestion}")
                print(f"[julia-debug] respond.practitioner_suggestion (raw): {raw_practitioner}")
                print(f"[julia-debug] eligible_practitioner_ids: {sorted(eligible_practitioner_ids)}")
                print(f"[julia-debug] validated practitioner_suggestion: {practitioner_suggestion}")
                print(f"[julia-debug] suggested_replies: {suggested_replies}")
            break

        speech = "Sorry — I lost my thread there. Could you say that again?"
        break

    if not speech:
        speech = "Sorry — I lost my thread there. Could you say that again?"

    if product_suggestion is not None:
        session.product_count += 1
    if practitioner_suggestion is not None:
        session.practitioner_count += 1

    session.history.append({"role": "user", "content": user_text})
    session.history.append({"role": "assistant", "content": speech})

    return TurnResult(
        speech=speech,
        sources_used=sources_used,
        product_suggestion=product_suggestion,
        ended=False,
        mode_3_category=None,
        anthropic_called=True,
        retrieved_page_ids=sorted(retrieved_page_ids),
        suggested_replies=suggested_replies,
        practitioner_suggestion=practitioner_suggestion,
    )


def _format_find_product_result(products: list[dict[str, Any]]) -> str:
    if not products:
        return "No matching products found. Call `respond` without a product_suggestion."
    lines = ["Matching products (pick at most one for `respond.product_suggestion`):"]
    for p in products:
        lines.append(
            f'- id: {p["id"]} | name: {p["name"]} | price: {p.get("price", "")} | '
            f'category: {p.get("category", "")} | attributes: {p.get("attributes", [])} | '
            f'use_case_tags: {p.get("use_case_tags", [])} | '
            f'why_this_one: {p.get("why_this_one", "")}'
        )
    return "\n".join(lines)


def _format_find_practitioner_result(practitioners: list[dict[str, Any]]) -> str:
    if not practitioners:
        return (
            "No matching practitioners found for that location and specialty. "
            "If the user might be open to online sessions, ask them and try again "
            "with location='online' next turn. Otherwise, call `respond` without a "
            "practitioner_suggestion and acknowledge that you don't have someone "
            "in their area in the directory."
        )
    lines = ["Matching practitioners (pick at most one for `respond.practitioner_suggestion`):"]
    for p in practitioners:
        modes = []
        if p.get("in_person_available"):
            modes.append("in-person")
        if p.get("online_available"):
            modes.append("online")
        lines.append(
            f'- id: {p["id"]} | name: {p["name"]} | title: {p.get("title", "")} | '
            f'city: {p.get("city", "")} | modes: {", ".join(modes) or "?"} | '
            f'specialties: {p.get("specialties", [])} | '
            f'website: {p.get("website", "")} | phone: {p.get("contact_phone", "")} | '
            f'why_this_one: {p.get("why_this_one", "")}'
        )
    return "\n".join(lines)
