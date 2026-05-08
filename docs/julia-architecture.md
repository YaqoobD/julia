# Julia — Architecture & Design Doc

This doc describes **what the system is shaped like** and **how a turn flows through it**. It names roles ("an STT step", "an embedding store", "a TTS provider") rather than specific vendors. Vendor and library picks live in the tech-stack doc that follows.

Read `julia-spec.md` first. This doc assumes the spec's decisions and elaborates the system design that delivers them.

---

## 1. Goals and non-goals of this doc

**Goals:**

- Define the end-to-end data flow for one user turn, on both channels.
- Specify how mode-3 hard-refuse is routed deterministically without involving the language model.
- Specify the retrieval pipeline and how it interacts with the language model call.
- Specify the structured-output mechanism that returns Julia's speech, source citations, and optional product suggestion in one round trip.
- Specify how the reference-table source attribution is computed (and validated) per turn.
- State the latency budget and the interruption policy.

**Non-goals (explicitly out of this doc):**

- Specific vendors, library names, model IDs, voice IDs, API endpoints, pricing, hosting topology — all in the tech-stack doc.
- File and module layout — in the implementation plan.
- Test strategy and rehearsal cadence — in the implementation plan.

---

## 2. System shape

```
                ┌────────────────────────────────────────────────┐
                │                Backend (one Julia)              │
                │                                                │
   Web channel  │   ┌────────┐    ┌──────────────────────────┐   │
   ──────────► │ ──▶│  Web   │──▶ │                          │   │
   (HTTP +     │    │adapter │    │      Conversation        │   │
   audio I/O)  │    └────────┘    │        engine            │   │
                │                  │                          │   │
   Phone        │   ┌────────┐    │  • mode-3 router         │   │
   channel      │   │ Phone  │    │  • retrieval             │   │
   ──────────► │ ──▶│adapter │──▶ │  • LLM tool-use loop     │   │
   (telephony   │   │(webhook│    │  • response assembler    │   │
    webhooks)   │   └────────┘    │                          │   │
                │                  └──────────┬───────────────┘   │
                │                             │                   │
                │              ┌──────────────┴────────┐          │
                │              │  Stores & tools       │          │
                │              │   • corpus index      │          │
                │              │   • product index     │          │
                │              │   • session memory    │          │
                │              └───────────────────────┘          │
                └────────────────────────────────────────────────┘
```

Two channel adapters convert their respective transport (HTTP+audio for web; telephony webhooks for phone) into a uniform input to the conversation engine: `{session_id, user_text, channel}`. The conversation engine is **identical** across channels — same prompt, same retrieval, same product tool, same response shape. Only the audio pipeline at the edges differs.

The web adapter performs STT (speech to text) and TTS (text to speech) at its edges. The phone adapter does not — its telephony platform handles STT/TTS internally and the adapter only ferries text between platform webhooks and the engine.

---

## 3. Turn flow (sequence)

One user utterance → one Julia response. This is the spine of the system.

```
USER UTTERS

  Web channel:                          Phone channel:
  ─ mic captures audio                  ─ telephony platform captures audio
  ─ web adapter STT → text              ─ telephony platform STT → text
  ─ adapter renders user text           ─ telephony platform delivers text
    in chat (visible transcript)         to phone adapter via webhook
                  │                                  │
                  └────────────┬─────────────────────┘
                               │
                               ▼
                   ┌──────────────────────┐
              (1)  │  MODE-3 ROUTER       │
                   │  deterministic regex │
                   │  pre-check on text   │
                   └──────────┬───────────┘
                          ┌───┴───┐
                       hit│       │miss
                          ▼       ▼
              ┌──────────────┐    ┌─────────────────────────┐
         (2a) │ Mode-3 hard  │ (2b) │  RETRIEVAL              │
              │ refuse       │    │  embed last-3-user-turns│
              │ template     │    │  → top-K corpus chunks  │
              │ assembled    │    └────────────┬────────────┘
              │ from         │                 │
              │ static text  │                 ▼
              └──────┬───────┘    ┌─────────────────────────┐
                     │       (3)  │  LLM TOOL-USE LOOP      │
                     │            │  inputs: system prompt, │
                     │            │  history, retrieved     │
                     │            │  chunks, tool defs      │
                     │            │  loop: model may call   │
                     │            │  find_product; must end │
                     │            │  by calling respond     │
                     │            └────────────┬────────────┘
                     │                         │
                     │            ┌────────────▼────────────┐
                     │       (4)  │  RESPONSE ASSEMBLER     │
                     │            │  validate sources_used  │
                     │            │  ⊆ retrieved set;       │
                     │            │  attach product card    │
                     │            │  if surfaced            │
                     │            └────────────┬────────────┘
                     │                         │
                     └─────────────┬───────────┘
                                   ▼
                   ┌──────────────────────────┐
              (5)  │  CHANNEL DELIVERY        │
                   │                          │
                   │  Web: speech → TTS →     │
                   │       audio stream;      │
                   │       sources_used →     │
                   │       reference table;   │
                   │       product → card     │
                   │                          │
                   │  Phone: speech → text    │
                   │         to telephony     │
                   │         platform (its    │
                   │         TTS plays it)    │
                   └──────────────────────────┘
```

### Step-by-step

**(1) Mode-3 router.** Runs on every turn before anything else. Deterministic regex set covering: explicit age statements with `n < 18`, crisis phrases, dosing / named-drug requests. On hit → step (2a). On miss → step (2b). The router is the single source of truth for mode-3 — Claude is the **backup** judgment layer, not the primary.

**(2a) Mode-3 hard-refuse template.** When the router hits, the engine assembles the response from a static, hand-written template family (one per category: under-18 → Brook; crisis → Samaritans / Domestic Abuse Helpline; dosing → prescriber). Never calls the language model for this turn. The template carries enough warmth to feel like Julia, but it is not generated. Skips retrieval, skips product. Marks the session as ended for that thread.

**(2b) Retrieval.** The engine embeds the **last three user turns concatenated** as a single query (cheap; covers conversational follow-ups like "what about for postnatal?"), runs a similarity search over the indexed corpus, returns the top-K chunks (K is a tech-stack tunable; ~3–5 expected). Each chunk carries `{chunk_id, source_page_id, source_url, text}`.

**(3) LLM tool-use loop.** The engine calls the language model with: (a) the system prompt encoding persona, two-tier grounding rule, mode-1/2/3 few-shot examples, and product-trigger rules; (b) full conversation history for this session; (c) the retrieved chunks as context, each tagged with its `source_page_id`; (d) two tool definitions: `find_product` and `respond`. The model may call `find_product` zero or one time, then must call `respond`. (See §6 for tool contracts.)

**(4) Response assembler.** Takes the `respond` tool call. Validates that every `source_page_id` in `sources_used` was in the retrieved set returned at step (2b) — drops any hallucinated IDs silently. Resolves `product_suggestion.id` against the product index to produce a render-ready product card (name, price, why-this-one, PDP link).

**(5) Channel delivery.** Two paths:

- **Web**: `speech` is streamed to the TTS provider. As TTS audio chunks return, they are streamed to the browser and played. The text of `speech` is also rendered into the chat for the visible-transcript pattern. `sources_used` (post-validation) is appended to the persistent reference table, deduplicated by `source_page_id`. `product_suggestion` (if present) renders as a card below the conversation.
- **Phone**: `speech` is returned as text to the telephony webhook. The telephony platform performs its own TTS and plays the audio. `sources_used` and `product_suggestion` are not surfaced on phone — phone is a voice-only channel.

---

## 4. Channel adapters

### 4.1 Web adapter

**Inputs to the engine:** `{session_id, user_text, channel: "web"}`.

**Outputs from the engine:** `{speech, sources_used, product_suggestion?}` where `speech` is text the adapter will TTS-render, and the other fields drive the UI side-panels (reference table, product card).

**Audio pipeline (web only):**

- **Inbound**: browser captures mic audio → adapter sends to STT provider → adapter renders the transcribed text in the chat (visible-transcript UX) → adapter passes the text to the engine.
- **Outbound**: adapter receives `speech` text from the engine → adapter streams to TTS provider → audio chunks stream to browser → browser plays.

**Visible transcript is a feature, not a fallback.** Every user message appears as text in the chat after STT. This doubles as the failure-mode mitigation when STT mistranscribes — the user can see what Julia heard.

### 4.2 Phone adapter

**Inputs to the engine:** `{session_id, user_text, channel: "phone"}`.

**Outputs from the engine:** `{speech, sources_used, product_suggestion?}` — but only `speech` is used by the phone adapter. `sources_used` and `product_suggestion` are discarded (phone is voice-only).

**Audio pipeline:** the telephony platform (whoever the tech-stack doc picks) handles STT and TTS internally. The phone adapter speaks to the platform via webhooks: receives `{user_text}`, returns `{speech_text}`. The adapter never touches audio bytes itself.

**Session continuity:** phone sessions are independent of web sessions (same Julia, separate sessions — per spec §5). The phone adapter generates its own session IDs based on call-leg identifier.

---

## 5. Stores

### 5.1 Corpus index

- Pre-built once before the hackathon. ~20 source pages chunked into ~500-token segments with overlap.
- Each chunk has: `{chunk_id, source_page_id, source_url, source_title, text, embedding_vector}`.
- Lookup: vector similarity over `embedding_vector`. Top-K returned.
- The index is read-only at runtime. No live additions during a session.
- The corpus is small enough (~250 chunks expected) that the choice of vector store technology is not load-bearing for the architecture — covered in the tech-stack doc.

### 5.2 Product index

- Pre-built once. ~15 products with full metadata per spec §7.
- Each product: `{id, name, price, category, use_case_tags, why_this_one, attributes, pdp_url}`.
- Lookup is via `find_product(category, attributes)` tool call (see §6.1) — never via free-form search. This is a deliberate constraint: it prevents fabrication and keeps the "earned, not offered" promise honest at the API layer, not just the prompt layer.

### 5.3 Session memory

- In-memory map of `session_id → message_history`. No persistence across restarts.
- Each session holds the full message history (user turns + Julia turns + tool calls + tool results). Demo conversations are 4–6 turns, so context bloat is not a concern.
- Session is ephemeral and per-channel: a phone session and a web session are independent even if hypothetically the same person. Cross-channel continuity is out of scope.

---

## 6. Tool contracts

The language model uses tool calls as both its action mechanism (`find_product`, `find_practitioner`) and its response mechanism (`respond`). The model is instructed to always end a turn by calling `respond`. Each turn the model may call at most one of `find_product` or `find_practitioner` (never both) before `respond`.

### 6.1 `find_product`

**Purpose:** retrieve one or two products from the curated index that match the user's expressed need.

**When callable:** any time during the model's reasoning for a turn, except when mode-3 has already routed (mode-3 does not invoke the model at all).

**When the system prompt instructs the model to call it:** ONLY after the user has expressed a specific need a product addresses (per spec §4 product trigger). Yes-example: "lubes I've tried haven't worked." No-example: "I have menopausal dryness" → don't call yet; ask a clarifying question or share general guidance first.

**Input shape:**

```
{
  category: string,              // e.g., "lube", "moisturiser", "vibrator"
  attributes: array of strings   // e.g., ["glycerin-free", "water-based"]
}
```

**Output shape:**

```
{
  results: array of {
    id: string,
    name: string,
    price: string,
    why_this_one: string,        // pre-curated one-liner; model may quote or adapt
    attributes: array of strings,
    pdp_url: string
  }
}
```

**Result-set size:** the tool returns at most two products. Even if the index has many matches, only the top two are returned — the curated set IS the gate.

**Mode-2 special rule (enforced in the system prompt, not the tool):** when the conversation is in mode-2 (mandatory referral), the model may use the tool only if the result is genuinely first-line care. Never as a referral substitute.

### 6.2 `find_practitioner`

**Purpose:** retrieve one or two UK sex therapists from the curated directory that match a specialty and a location. Used when a *human professional* is the substantive answer (not a product, not a GP nudge alone).

**When callable:** as for `find_product`, but only after the user has either named their city or signalled they're open to online sessions. The system prompt requires the model to ask first if neither is on the table.

**Input shape:**

```
{
  specialty: string,    // comma-separated tags, e.g. "couples, desire, communication"
  location: string      // UK city name or the literal "online"
}
```

**Output shape:**

```
{
  results: array of {
    id: string,
    name: string,
    title: string,
    city: string,
    online_available: bool,
    in_person_available: bool,
    specialties: array of strings,
    website: string,
    contact_phone: string,
    why_this_one: string
  }
}
```

**Result-set size:** at most two practitioners, ranked by overlap of `specialty` against each entry's `specialties[]`.

**Mode-2 special rule:** practitioner suggestions sit *alongside* a GP nudge, not in place of one when the user described a physical symptom.

**Demo-data caveat:** the directory at `data/practitioners.json` is fake (invented names, `example.com` websites, Ofcom drama-reserved phone ranges) for the hackathon. Replacing with a verified COSRT export is a post-hackathon task — the contract here doesn't change, only the data source.

### 6.3 `respond`

**Purpose:** the structured response Julia emits. The model is instructed that **every turn must end with a `respond` call** (and never two).

**Input shape (this is the model's output to the engine):**

```
{
  speech: string,                              // the text Julia will say (or the chat will render)
  sources_used: array of source_page_id,       // page-level IDs from the retrieved set
  product_suggestion: {                        // optional; included only when find_product
    id: string,                                // was called and the model decided to surface
    why_this_one: string                       // a result
  } | null,
  practitioner_suggestion: {                   // optional; included only when find_practitioner
    id: string,                                // was called and the model decided to surface
    why_this_one: string                       // a result
  } | null
}
```

**Validation by the response assembler:**

- Every `source_page_id` in `sources_used` must have been in the retrieved set returned at step 2b. Hallucinated IDs are dropped silently before the reference table updates. (The model still sees the original message, so it learns nothing from the silent drop — this is an engine-side guard, not a teaching signal.)
- `product_suggestion.id` must match an item the model received from a `find_product` result this turn. If the model emits a product not in any returned result, the suggestion is dropped before assembly.
- `practitioner_suggestion.id` is validated the same way against this-turn `find_practitioner` results.
- `speech` is passed through unchanged.

---

## 7. The two-tier grounding rule, in architecture terms

The spec (§4) divides Julia's claims into two tiers. The architecture supports this division mechanically:

- **Factual claims** must be grounded — the system prompt instructs the model that any clinical / medical / mechanism claim must be sourced from a passage in the retrieved chunk set, and the corresponding `source_page_id` listed in `sources_used`. The reference-table validation (§6.2) enforces "at least claimed" — the model cannot list pages not retrieved. It does NOT enforce "actually used in this response" — that would require automated text-grounding which is out of scope for the hackathon.
- **Tone, acknowledgments, consent reframes** are persona-driven — the system prompt explicitly tells the model these are not subject to the grounding rule and do not require entries in `sources_used`.

The reference table reflects this honestly: a Mode-1 reframe turn that gives consent guidance and no clinical claims may legitimately have an empty `sources_used`. The table simply doesn't grow that turn.

---

## 8. Reference table source attribution

**Per turn:** the model self-reports which `source_page_id`s it used in `sources_used` (per the grounding rule above). The response assembler validates the report (subset check against retrieved set), drops invalid entries silently, and passes the validated list to the web adapter.

**Aggregation across the session:** the web adapter maintains a per-session set of source pages used so far, deduplicated by `source_page_id`. As `sources_used` arrives each turn, new entries are appended to the table; repeated entries are not duplicated.

**Display:** each row in the table shows the source title and a clickable URL that opens the original page in a new tab. The table persists across the session and is visible alongside the chat throughout.

**Honest empties:** turns that legitimately use no factual claims (e.g., a pure mode-1 reframe) produce no new rows. The table will not grow uniformly turn-by-turn, and that is fine — it reflects the actual grounding work.

---

## 9. Latency budget

**Target:** under 3 seconds from "user finishes speaking" to "Julia's first audible word." This is a feels-natural bar, not a hard contract.

Approximate per-step budget:

- STT (web, inbound) — small.
- Mode-3 router — negligible (regex).
- Embedding the query — small.
- Vector similarity search — small (corpus is ~250 vectors).
- LLM call (with retrieval context, possibly two model passes for tool-use loop) — the largest single cost.
- Response assembler — negligible.
- TTS first chunk — small if streaming is supported by the TTS provider.

**Optimisation strategies enabled by this architecture:**

- **Prompt caching on the system prompt + retrieved chunks** — the system prompt does not change between turns within a session, and retrieved chunks for a turn can be cached for that turn's tool-use loop (where the model may run twice — once to call `find_product`, once to call `respond`).
- **Streaming TTS** — `speech` is sentence-streamed to the TTS provider so audio playback can begin before the full response is generated.

If the latency target is missed, the cut list (spec §11) prescribes the order: drop streaming TTS first, accept a 2–3s wait per full turn.

---

## 10. Interruption policy

**Not implemented.** Demo conversations are scripted; the user does not need to cut Julia off mid-utterance.

This is a deliberate choice. Streaming-STT-with-mid-TTS-cancellation adds substantial complexity (audio playback control, mid-flight LLM cancellation, reconciliation of what Julia had said vs what user heard). For a solo + 1-week build with three scripted demo conversations, the cost-benefit is clear.

If the architecture survives past the hackathon, interruption support would be added at the channel adapter layer (web adapter handles its own audio cancellation; phone adapter delegates to the telephony platform's interruption support if available).

---

## 11. Failure modes (architectural)

These are the failure modes the architecture must accommodate. Mitigations live in the implementation plan; what the architecture commits to is *that they are observable and recoverable per turn*, not session-fatal.

- **STT mistranscription** (web) → visible transcript renders the model's input; user can see and re-state. Per-turn recoverable.
- **Retrieval returns nothing relevant** → the model receives an empty (or weak) chunk set, falls back to "I don't have specific guidance on this — a GP / appropriate helpline is the right next step." Two-tier grounding rule plus weak retrieval combine to produce a clean fallback rather than hallucination. Per-turn recoverable.
- **Hallucinated `sources_used` IDs** → response assembler drops them silently; reference table does not display the false attribution. The user sees fewer sources, not wrong sources. Per-turn recoverable.
- **Hallucinated `product_suggestion.id`** → response assembler drops the suggestion. The user sees no product card that turn even if `speech` mentioned a product by name. (Mitigation in the system prompt: instruct the model not to mention a product in `speech` unless it was returned from `find_product` this turn.)
- **TTS provider rate-limit / outage** → web adapter falls back to text-only render for that turn (the chat still shows Julia's reply). Phone adapter has no fallback — if the telephony platform cannot TTS, the call degrades.
- **Tool-use loop runaway** (model keeps calling `find_product` without ever calling `respond`) → engine enforces a hard cap of one `find_product` call per turn. After that, only `respond` is accepted; further tool calls return an error to the model.

---

## 12. What this doc deliberately does not specify

The following are tech-stack decisions, not architecture decisions, and live in the next doc:

- Which STT provider, which TTS provider, which language model and version.
- Which embedding model, which vector store technology, which similarity metric.
- Which telephony platform for the phone channel.
- Which web framework or transport (HTTP REST vs WebSocket vs SSE for streaming).
- Hosting, secrets management, observability.
- The exact regex patterns for the mode-3 router (drafted in spec §4 but final form belongs with the safety code in implementation).

What this doc commits to is that the system is shaped such that **any** reasonable choice of these pieces fits behind the channel-adapter / conversation-engine boundary without altering the turn flow above.

---

End of architecture doc. Tech-stack doc is the next artifact.
