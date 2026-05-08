# Julia — Implementation Plan

This is the fourth and final artifact in the spec-driven sequence. It assumes the spec, architecture, and tech-stack docs are settled and breaks the build itself into phases.

Read in this order:

1. `julia-spec.md` — what the product does
2. `julia-architecture.md` — how the system is shaped
3. `julia-tech-stack.md` — which specific tools fill each role
4. **This doc** — the build plan

---

## 1. Goals and constraints

**Goal:** A polished end-to-end Julia demo, runnable on a laptop with a stable internet connection, ready for a 5-minute internal hackathon presentation.

**Constraints:**

- Solo developer, ~1 week of prep + hackathon weekend.
- Three demo conversations are LOCKED (spec §8): menopause/dryness, couple-introducing-toys, under-18 → Brook.
- Anything that does not appear in those three conversations is presumptively cuttable.
- Production polish is NOT a goal. "Runs end-to-end on stage" is the bar.

**Non-goals for this plan:**

- Tests for code paths that don't appear in the demo.
- Multi-user concurrency, observability platforms, deployment hardening.
- A migration path off the in-memory stores. Version 2 problem.

---

## 2. Phase overview

| Phase | What | Approx time | Blocking? |
|---|---|---|---|
| 0 | Pre-flight (procurement, accounts, corpus shortlist, voice audition) | ~3h spread across days, mostly non-code | Yes — phone procurement gates phone channel |
| 1 | Project foundation (skeleton, deps, env, FastAPI hello-world) | ~3h | Yes — everything depends on it |
| 2 | Corpus indexing (offline, one-shot) | ~4h | Brain phase needs the index |
| 3 | Conversation engine (system prompt, retrieval, tool use, mode-3 router) | ~8h — the hardest day | Web channel needs this working |
| 4 | Web channel (chat UI, text-first) | ~6h | Voice phase needs the chat UI |
| 5 | Voice on web (ElevenLabs TTS + Whisper STT) | ~6h | — |
| 6 | Phone channel (Vapi adapter) | ~4h | — |
| 7 | Polish & rehearsal (system prompt iteration, demo scripts, backup video) | ~8h | Stage day depends on it |
| 8 | Stage day (pre-warm + presentation) | ~2h on the day | — |

**Suggested day mapping (assuming hackathon is the upcoming Sat/Sun):**

- Mon: Phase 0 + Phase 1
- Tue: Phase 2
- Wed: Phase 3
- Thu: Phase 4
- Fri: Phase 5
- Sat (hackathon day 1): Phase 6 + Phase 7
- Sun (hackathon day 2): Phase 7 finish + Phase 8

If the hackathon is further out, slide everything later — Phases 1–6 should still finish before Saturday.

---

## 3. File layout

```
julia/
├── README.md
├── pyproject.toml          # or requirements.txt
├── .env.example
├── .gitignore
├── .python-version
│
├── server/
│   ├── __init__.py
│   ├── main.py             # FastAPI app, route definitions, static mount
│   ├── julia.py             # turn handler — the conversation engine entry
│   ├── retrieval.py        # corpus loading + cosine search
│   ├── tools.py            # find_product + respond tool schemas
│   ├── safety.py           # mode-3 regex + hard-refuse templates
│   ├── stt.py              # Whisper wrapper
│   ├── tts.py              # ElevenLabs wrapper (streaming + fallback)
│   ├── vapi.py             # phone-channel webhook adapter
│   └── system_prompt.md    # the prompt (read at startup)
│
├── data/
│   ├── corpus/             # source pages as .md or .html (manual curation)
│   ├── corpus_index.npz    # built by scripts/index_corpus.py
│   └── products.json       # curated product index (manual curation)
│
├── scripts/
│   ├── index_corpus.py     # offline: chunk + embed + save
│   ├── prewarm.py          # pre-stage warm-up calls to all APIs
│   └── test_conversation.py # offline: drive each demo through the engine
│
└── web/
    ├── index.html
    ├── chat.js
    └── style.css
```

---

## 4. Phase 0 — Pre-flight

**Goal:** Unblock the rest of the work. Most of this is procurement, accounts, and curation; can run in parallel with later phases.

**Tasks:**

1. Create accounts and grab API keys: Anthropic, OpenAI, ElevenLabs, Vapi. Save to `.env` as you go. (Vapi is pay-as-you-go with a $10 starter credit on signup — that more than covers hackathon usage; no separate "free tier" subscription.)
2. Audition 3–5 ElevenLabs voices using one paragraph from the menopause demo. Lock one voice. Save its ID as `ELEVENLABS_VOICE_ID`.
3. Shortlist ~25 corpus URLs (NHS menopause, BMS leaflets, Women's Health Concern, wellness publications on menopause and intimate wellness). Save URLs in `data/corpus/sources.txt`. Final cut to 20 happens in Phase 2.
4. File the phone-number procurement request. State explicitly: number to be routed to a Vapi assistant. If unsure, use the Vapi test number for the build — procurement can land later without code changes.
5. Shortlist ~20 candidate products (see spec §7 for required metadata fields). At least one glycerin-free lube/moisturiser appropriate for menopausal dryness. Final cut to 15 in Phase 2.
6. Install Cloudflare Tunnel CLI (`cloudflared`). Reserve a tunnel name (`julia-demo`). Test routing to `localhost:8000` with a placeholder.

**Verification:** `.env` has all keys filled. Voice locked. Corpus and product shortlists exist. `cloudflared tunnel list` shows the julia-demo tunnel.

---

## 5. Phase 1 — Project foundation

**Goal:** A FastAPI server that serves a static frontend on `localhost:8000` and exposes a `/health` endpoint. This is the skeleton everything else hangs off.

**Tasks:**

1. `mkdir julia && cd julia`. Initialise git, set `.gitignore` (include `.env`, `__pycache__/`, `.venv/`, `.python-version`, `data/corpus_index.npz`).
2. Create `.python-version` with `3.11`. Create venv: `uv venv` (or `python -m venv .venv`).
3. Install deps:
   ```
   uv pip install fastapi uvicorn[standard] python-dotenv anthropic openai elevenlabs httpx numpy tiktoken markdown
   ```
   (Vapi has no SDK; webhook is a plain JSON POST. `markdown` is for converting docs only — optional in the runtime.)
4. Create the file layout from §3, mostly empty.
5. `server/main.py`: a FastAPI app with `GET /health` returning `{"status": "ok"}`, plus a static-file mount for `web/` at `/`.
6. `web/index.html`: minimal "Julia" heading and a placeholder text box, just to confirm static mounting works.
7. Run `uvicorn server.main:app --reload --port 8000`. Open `http://localhost:8000` — see the placeholder. Hit `/health` — see ok.

**Deliverable:** A running FastAPI app you can curl. No Julia logic yet.

**Verification:** `curl http://localhost:8000/health` returns `{"status":"ok"}`. Browser shows the placeholder.

---

## 6. Phase 2 — Corpus indexing

**Goal:** A pre-built `data/corpus_index.npz` containing the embedded, chunked corpus, plus a `data/products.json` containing the curated product index. No manual copy-paste — the corpus is **scraped** from a curated URL list.

**Tasks:**

1. **Curate the URL list.** Save ~20 URLs into `data/corpus/sources.txt`, one per line; blank lines and `#` comments allowed. Sources from NHS, BMS / Women's Health Concern, wellness publications. The list MUST factually cover demo slots 1 (menopause/dryness) and 2 (couple introducing toys). See the "URL hunting list" appendix below for starting points.
2. **Write `scripts/fetch_corpus.py`** (the scraper):
   - Reads `data/corpus/sources.txt`, one URL per line.
   - For each URL: fetches with a polite User-Agent (e.g., `Julia-Hackathon/0.1 (internal demo)`), extracts main content via **`trafilatura`** (handles nav / footer / sidebar / script removal automatically; returns clean markdown), writes to `data/corpus/<slug>.md` where `<slug>` is derived from the URL path.
   - File header comments: `<!-- source_url: ... -->` and `<!-- source_title: ... -->` (title pulled from `<title>` or `og:title`).
   - ~1s delay between requests. Per-URL error handling — skip and log; do not fail the whole run.
   - Idempotent: re-running overwrites existing files (so URL list edits propagate cleanly).
3. **Run** `python scripts/fetch_corpus.py`. Spot-check 2–3 generated files. If any look noisy or stripped too aggressively, hand-trim or re-fetch with different trafilatura options (e.g., `include_tables=True`).
4. **Curate `data/products.json`**: a list of ~15 product objects, each with the metadata schema from spec §7. Hand-write the `why_this_one` one-liners — they are part of Julia's voice. Must include ≥1 fitting product for slot 1 (glycerin-free lube or moisturiser) and ideally one for slot 2.
5. **Write `scripts/index_corpus.py`** (the embedder):
   - Read every `.md` file in `data/corpus/` (skip `sources.txt`).
   - Extract `source_url` and `source_title` from the header comments.
   - Chunk each file's body using `tiktoken` with `cl100k_base` encoding into 500-token chunks with 50-token overlap.
   - For each chunk, generate an embedding via `client.embeddings.create(model="text-embedding-3-large", input=chunk_text)`.
   - Build a NumPy 2-D array `embeddings` (N, 3072) and a parallel list `metadata` of `{chunk_id, source_page_id, source_url, source_title, text}`.
   - L2-normalise embeddings.
   - Save with `np.savez_compressed(..., embeddings=..., metadata=...)`. (Pickle metadata into a parallel `.json` if `np.savez` complains.)
   - Print stats: total pages, total chunks, embedding-call count, total cost estimate.
6. **Run** `python scripts/index_corpus.py` once. Inspect output. Spot-check a few chunks for sane boundaries.

**Dependencies added in this phase:** `trafilatura>=1.12` (clean main-content extraction). Add to `requirements.txt`.

**Deliverable:** `data/corpus_index.npz` (+ optional `data/corpus_index.json` for metadata) and `data/products.json`.

**Verification:** Loading the npz returns a numpy array of shape `(~250, 3072)` with rows L2-normalised. `data/products.json` parses with at least 15 products, each having every required field.

### URL hunting list (starting points for `sources.txt`)

These are the websites to visit. Browse from the listed entry points and pick pages that directly support demo slots 1 + 2. Aim for ~20 URLs total, weighted ~12 toward menopause/dryness/HRT (slot 1) and ~5 toward couples/intimacy/communication (slot 2), with a few general intimate-wellness pages.

**NHS — start at https://www.nhs.uk/**

Look for these section URLs and the pages they link to:

- `nhs.uk/conditions/menopause/` (overview, symptoms, treatment, lifestyle)
- `nhs.uk/conditions/early-menopause/`
- `nhs.uk/conditions/hormone-replacement-therapy-hrt/` (and its sub-pages: types, benefits, risks, alternatives)
- `nhs.uk/conditions/vaginal-dryness/`
- `nhs.uk/conditions/painful-sex-dyspareunia/` (or the equivalent current title)
- `nhs.uk/live-well/sexual-health/` (general)

NHS pages are very clean and trafilatura handles them well.

**Women's Health Concern (the BMS public-facing site) — start at https://www.womens-health-concern.org/**

Look in `/help-and-advice/factsheets/` — they publish standalone factsheets on:

- Menopause overview
- HRT — benefits and risks
- Local oestrogen treatments (very relevant to slot 1)
- Vaginal dryness, itching and discomfort
- Sexual problems associated with the menopause
- Premature ovarian insufficiency
- Bioidentical and body-identical hormones

Pick 4–6 of these. They are standalone PDFs or web pages — prefer the web versions.

**British Menopause Society (clinician-focused, but has some public pages) — https://thebms.org.uk/**

Most of BMS is for HCPs and won't read well in Julia's voice. Skip unless you find a specifically-public page.

**Wellness-publication advice content (your choice of source)**

Pick a wellness publisher whose content you have a right to fetch. Any tone-appropriate
"intimate wellness 101" hub will do — look for pages on:

- Menopause and sex / lubricants for menopause
- Choosing a lube (the glycerin-free explanation matters for slot 1)
- Talking to your partner about toys / introducing toys to a relationship (slot 2)
- Couples' beginner guides
- Intimate-wellness 101 / vaginal moisturisers vs lubes

Plain, conversational content like this is the only credible source for Julia's
"we'd point you to a glycerin-free option" framing. **Pick generously here.**

**Skip / signpost-only (not in corpus):**

- **Brook (brook.org.uk)** — used as the under-18 redirect target only. Spec §6 explicitly says don't include in retrieval.
- **Samaritans / National Domestic Abuse Helpline** — phone numbers signposted from mode-3 templates. Not corpus.
- **NICE guidelines** — too clinical, not in Julia's voice.
- Anything behind a paywall, login, or robots.txt block.

**One-line format for `sources.txt`:**

```
# Slot 1: menopause / dryness / HRT
https://www.nhs.uk/conditions/menopause/
https://www.nhs.uk/conditions/menopause/symptoms/
https://www.womens-health-concern.org/help-and-advice/factsheets/vaginal-dryness/
# ... etc

# Slot 2: couples / introducing toys / communication
https://example.com/intimate-wellness/talking-about-sex-toys
# ... etc

# General intimate wellness
https://example.com/intimate-wellness/how-to-choose-a-lube
# ... etc
```

Run the scraper after the list is together. If any URL fails (404, blocked, paywall), the scraper logs it; remove from the list or replace.

---

## 7. Phase 3 — Conversation engine (the brain)

**Goal:** A `POST /api/turn` endpoint that takes `{session_id, user_text}`, runs the full architecture pipeline (mode-3 router → retrieval → tool-use loop → response assembler), and returns the structured `{speech, sources_used, product_suggestion}`. No frontend yet — drive it with curl.

This is the most demanding day. Treat it as one phase; do not split unless the budget allows.

**Tasks:**

1. **`server/system_prompt.md`** — write the full system prompt. Use Appendix A as the v1 starting point. This file is loaded at startup and never changes during a session.
2. **`server/safety.py`** — implement the mode-3 router:
   - The regex set lives here. Use Appendix B as the v1.
   - One function: `check_mode_3(user_text: str) -> Mode3Hit | None`. Returns a category (`"under_18"`, `"crisis"`, `"dosing"`) and the matched fragment if hit, or None.
   - Hard-refuse templates: one Python string per category. Each template includes the right helpline/redirect (Brook for under-18, Samaritans for crisis, "see a prescriber" for dosing) and ends the consultation thread.
3. **`server/retrieval.py`** — implement the corpus search:
   - Load `data/corpus_index.npz` at module import.
   - One function: `retrieve(query: str, k: int = 5) -> list[Chunk]`. Embeds the query (last 3 user turns concatenated, passed in by caller), L2-normalises, computes `embeddings @ query`, returns top-K metadata entries.
4. **`server/tools.py`** — define the two tool schemas:
   - `find_product_tool` — input `{category: str, attributes: list[str]}`, returns matching products from `data/products.json` (max 2 results).
   - `respond_tool` — input `{speech: str, sources_used: list[str], product_suggestion: {id, why_this_one} | None}`. This is how Julia ends a turn.
5. **`server/julia.py`** — the turn handler:
   - Function: `async def handle_turn(session_id: str, user_text: str, channel: str) -> TurnResult`.
   - Step 1: append user_text to session memory (in-memory dict).
   - Step 2: call `safety.check_mode_3`. If hit, return a TurnResult with the appropriate hard-refuse template as `speech` and empty sources/product. Mark session ended. **Return early — Claude is not invoked.**
   - Step 3: build retrieval query from last 3 user turns concatenated. Call `retrieval.retrieve(query, k=5)`.
   - Step 4: call Anthropic SDK with: system prompt (cached), session history, retrieved chunks formatted as `<context source_id="...">...</context>` blocks (cached), tool definitions. Use `tool_choice={"type": "any"}` so Claude must call a tool.
   - Step 5: tool-use loop — if Claude calls `find_product`, execute it against `data/products.json`, add result to messages, recall Claude. Hard cap: one `find_product` call per turn. If exceeded, return an error tool-result that prompts Claude to call `respond` instead.
   - Step 6: when Claude calls `respond`, validate `sources_used` (must be subset of retrieved set's `source_page_id`s) and `product_suggestion.id` (must match a result from this turn's `find_product` call). Drop invalids silently.
   - Step 7: append Julia's `respond` payload to session history. Return TurnResult.
6. **`server/main.py`** — add `POST /api/turn` that wraps `julia.handle_turn` and returns JSON.
7. **`scripts/test_conversation.py`** — a script that drives each of the three demo conversations through the engine and prints the turn-by-turn result. Use it to iterate on the system prompt.

**Deliverable:** A working `/api/turn` endpoint exercised end-to-end via the test script.

**Verification (this is the most important verification of the project):**

- **Slot 1 (menopause/dryness)** test conversation: user says "I've been getting dry during sex since menopause and lubes I've tried haven't worked." Expected: Julia acknowledges, gives general grounded info (cites at least one corpus source), suggests one product (a glycerin-free lube or moisturiser, surfaced as `product_suggestion`), and nudges to GP. `sources_used` should be a non-empty subset of the retrieved set.
- **Slot 2 (couple, nervous partner)** test: user says "How do I convince my partner to try toys?" Expected: Julia reframes ("convince" → consent + conversation), no product on first turn. On a follow-up where the user expresses a need, a product may appear. `sources_used` may be empty (mode-1 reframe is persona-driven).
- **Slot 3 (under-18)** test: user says "I'm 16 and..." Expected: regex fires, hard-refuse template returned, Brook signposted, NO call to Claude (verifiable by checking that no Anthropic API call was made — log it). `sources_used` empty, `product_suggestion` null.

If any of these fail, iterate the system prompt. Do NOT proceed to Phase 4 until all three demo conversations behave correctly via curl.

**Deferred to later phases:** voice (Phase 5), phone channel (Phase 6), reference table UI (Phase 4), product card UI (Phase 4).

---

## 8. Phase 4 — Web channel (chat UI, text-first)

**Goal:** A working web chat UI where you can type into a textbox, hit Send, and see Julia reply. The reference table accumulates source citations across the conversation. The product card renders when one is surfaced.

**Tasks:**

1. **`web/index.html`** — the layout:
   - Header: "Julia" name, AI disclosure line, source-credit line.
   - Main area: chat scroll region (80% width on desktop).
   - Right panel: reference table (sticky).
   - Below chat: product card area (initially empty).
   - Below that: textarea + mic button + Send button.
2. **`web/style.css`** — palette (coral/cream), reasonable typography, sensible widths. Reuse the palette from the HTML doc viewer style for consistency.
3. **`web/chat.js`** — client logic:
   - On Send: POST to `/api/turn` with `{session_id, user_text}`. Append user message to chat. Append Julia's response when received.
   - Render `sources_used`: append to reference table, deduplicated by source URL. Each row has the source title and an external-link icon.
   - Render `product_suggestion`: replace the product card content with name + price + why_this_one + "View product" link.
   - Generate a session_id on first visit (e.g., `crypto.randomUUID()`) and persist for the page load (no localStorage — fresh per session per spec).
4. **`server/main.py`** — already has `/api/turn` from Phase 3. No changes.
5. Hand-test the three demo conversations via the web UI. Verify:
   - Reference table grows naturally during slot 1.
   - Reference table may stay near-empty during slot 2's reframe turn — that's correct (two-tier grounding).
   - Slot 3 returns the hard-refuse template with no growth in reference table or product card.

**Deliverable:** Browser-based text chat UI, end-to-end functional for all three demo conversations.

**Verification:** From the web UI, the three demo conversations behave as in Phase 3 verification, plus the reference table and product card update correctly.

**Deferred:** voice (Phase 5), phone (Phase 6), polish/animation (Phase 7).

---

## 9. Phase 5 — Voice on web (TTS, then STT)

**Goal:** The user can speak into the mic to send a message (Whisper transcribes, transcript is visible in the chat) and hear Julia's reply spoken aloud (ElevenLabs TTS). Voice both ways on the web channel.

Build TTS first (lower-risk, audience hears it), then STT.

**Tasks (TTS):**

1. **`server/tts.py`** — ElevenLabs wrapper:
   - Function: `async def stream_tts(text: str) -> AsyncIterator[bytes]`. Connects to ElevenLabs WebSocket (`eleven_turbo_v2_5` model, locked voice ID), streams audio chunks back.
   - Fallback: `async def oneshot_tts(text: str) -> bytes` — single REST call returning full MP3, used if WebSocket fails.
2. **`server/main.py`** — add `GET /api/tts?text=...` (or POST with body) that streams audio bytes. Set `Content-Type: audio/mpeg`.
3. **`web/chat.js`** — when an Julia reply arrives, kick off audio playback:
   - Create an `<audio>` element with `src="/api/tts?text=<encoded speech>"` and call `.play()`.
   - On error (audio element fires `error` event), gracefully degrade — Julia's text is already visible in the chat, so user can read.
4. Test audio quality. Adjust ElevenLabs `voice_settings` (`stability`, `similarity_boost`) if needed. Lock the settings.

**Tasks (STT):**

1. **`server/stt.py`** — Whisper wrapper:
   - Function: `async def transcribe(audio: bytes, mime: str) -> str`. Sends the audio to OpenAI Whisper, returns the transcript.
2. **`server/main.py`** — add `POST /api/stt` accepting multipart audio upload, returns `{"text": "..."}`.
3. **`web/chat.js`** — mic button:
   - On click, `navigator.mediaDevices.getUserMedia({audio: true})`, start a `MediaRecorder` capturing webm/opus.
   - On click again (or release), stop the recorder, package the blob, POST to `/api/stt`.
   - Fill the textarea with the transcript so the user can see and edit before sending. (Visible-transcript-as-feature.)
4. Test the full voice loop: speak, see transcript appear, hit Send, hear Julia reply.

**Deliverable:** Hands-free demo possible on the web channel.

**Verification:** The three demo conversations runnable entirely via voice on the web. Mistranscriptions are catchable in the textarea before sending.

**Deferred:** mic-input streaming (we use record-then-upload, not streaming STT), interruption (deliberately not implemented per architecture §10), audio chunk streaming for sentence-by-sentence playback (cut list item 2 — fall back to full-response TTS first, add streaming only if time permits).

---

## 10. Phase 6 — Phone channel (Vapi adapter)

**Goal:** Dialling the Vapi number reaches Julia. The same backend, same conversation engine. Audience hears "same Julia, different way in."

**Tasks:**

1. **Cloudflare Tunnel up.** `cloudflared tunnel run julia-demo` so Vapi's webhooks can reach the laptop.
2. **`server/vapi.py`** — webhook adapter:
   - Endpoint: `POST /vapi/webhook`. Accepts Vapi's payload format, extracts the latest user message text.
   - Calls `julia.handle_turn(session_id=<call_id>, user_text=<text>, channel="phone")`.
   - Returns `{"response": <speech text>}`. Discards `sources_used` and `product_suggestion` (phone is voice-only per architecture §4.2).
3. **`server/main.py`** — register the Vapi route.
4. **Vapi assistant configuration** (in the Vapi dashboard):
   - Custom-LLM provider, URL = `<cloudflared-public-url>/vapi/webhook`.
   - First message: a short Julia greeting matching the web opening line.
   - Voice: pick a Vapi-supported voice that approximates the ElevenLabs choice (parity is not the goal).
   - Save and test the assistant from Vapi's playground.
5. Dial the Vapi test number. Ask one of the demo questions. Confirm Julia responds.
6. **Mode-3 verification on phone:** Vapi's STT still produces text, so the regex pre-check still fires. Test with "I am 16" — Julia should hard-refuse and signpost Brook over the call.

**Deliverable:** Working phone encore.

**Verification:** A live phone call to the Vapi number reaches Julia and gets a sensible reply, including the mode-3 hard-refuse path.

**If Vapi setup runs over time:** drop phone entirely (cut list item 4), web-only demo is complete.

---

## 11. Phase 7 — Polish & rehearsal

**Goal:** The demo runs end-to-end smoothly. The system prompt has been iterated based on rehearsal. A backup video exists. Pre-warm scripts are ready.

**Tasks:**

1. **Write the three demo conversation scripts** in detail (user lines verbatim; Julia's expected arc as bullet points). Save as `scripts/demo_scripts.md`. Use Appendix C as the skeleton.
2. **Rehearse each conversation with a stopwatch.** Target ~75–90s each. If a conversation runs long, tighten Julia's response length in the system prompt (`max_tokens`, plus an instruction to stay under N words for non-medical responses).
3. **Iterate the system prompt** based on what you hear:
   - Tone too clinical? Soften acknowledgments.
   - Sources cited inappropriately? Tighten the grounding rule.
   - Product surfaces too eagerly? Tighten the need-driven rule.
   - Mode-1 reframe sounds preachy? Soften the few-shot example.
4. **Capture a backup screen-recording** of the full ~5min demo on Friday afternoon. If anything goes wrong on stage, play this. Save as `backup-demo.mp4`. Test playback.
5. **Write `scripts/prewarm.py`** — calls every external API once (Anthropic, Whisper, ElevenLabs, Vapi if testable). Run this immediately before the demo to defeat cold-start latency.
6. **Stage logistics check:** ethernet vs wifi, laptop power, audio output, screen mirroring, mic levels.
7. **Failure rehearsal:** force ElevenLabs to fail (bad API key) — does the chat still show Julia's text? Force Whisper to mistranscribe — can the user-presenter see and correct in the textarea? If either path breaks, fix the fallback.

**Deliverable:** Confident, timed, rehearsed demo. Backup video. Pre-warm script.

**Verification:** A full dry-run on Friday with timer comes in under 5 minutes including transitions, opening, and roadmap close. Backup video plays cleanly.

---

## 12. Phase 8 — Stage day

**Goal:** The demo lands.

**The morning of:**

1. Ethernet at the venue. Test connectivity (does Cloudflare Tunnel resolve? Does Anthropic respond?).
2. `cloudflared tunnel run julia-demo` — leave running in a terminal.
3. `uvicorn server.main:app --port 8000` — leave running in another terminal.
4. Browser open to `http://localhost:8000`. Run through one full demo conversation in the dressing room to confirm everything works.

**T-5 minutes before going on:**

1. Run `python scripts/prewarm.py`. Confirms every API responds.
2. Confirm laptop is plugged in.
3. Have the backup video file open in another window, ready to play if needed.
4. Have the Vapi number on a sticky note or notes app.

**On stage:**

1. Run the script.
2. If anything fails — the network, Vapi, ElevenLabs — degrade gracefully:
   - ElevenLabs down → text-only chat is still live; narrate while reading.
   - Network fails entirely → play the backup video.
   - Vapi fails on the encore → skip and go straight to roadmap close.

---

## 13. Cut list (drop in this order if time pressure hits)

Reminder from spec §11. Use this when Phase 5 or 6 is running long.

1. Real your provisioned phone number → keep Vapi test number.
2. Streaming TTS sentence-by-sentence → one-shot TTS per turn.
3. Voice input on web → text input only; Julia still speaks back via TTS.
4. Vapi phone integration entirely → web-only demo, mention phone in roadmap slide.
5. Multi-source reference table → single most-relevant source per turn.
6. Animations / polish.

The first five can be cut without harming the core "third pillar" pitch.

---

## 14. Open items remaining at implementation time

Carrying forward from spec §12:

- **Final corpus URL list** — locks during Phase 0/2.
- **Final product index** — locks during Phase 0/2.
- **ElevenLabs voice** — locks during Phase 0.
- **Phone number** — Vapi test by default; your provisioned if procurement lands.
- **Opening-screen ghosted phrasings** — one of: spec proposes four; confirm during Phase 4.

---

## Appendix A — System prompt v1 (draft)

This is the v1 starting point for `server/system_prompt.md`. Iterate during Phase 7.

```
You are Julia, sexual wellness consultant. You speak with users on 's
website (and sometimes by phone) about sexual wellness concerns: menopause, low libido,
painful sex, intimacy and partner dynamics, and similar.

# Persona

You are an informed friend. Not a clinician, not a chatbot, not a salesperson.

- Acknowledge before advising. "That sounds frustrating" before "here's what might help."
- Ask at most one or two clarifying questions before saying anything substantial. No interrogation.
- Short turns. Two or three short paragraphs at most. Never bullet-pointed lists in your responses
  — they feel transactional.
- Never quote sources by name in conversation. ("According to NHS guidance..." kills tone.)
  Sources are credited via the reference table on screen, not in your speech.
- No statistics, no specific dosages, no medication name recommendations. General information only.
- Sometimes the right answer ends with a GP referral and no product. Knowing when to stop
  selling is part of being trustworthy.
- End most turns with a question that gives the user control: "Want me to share options, or
  shall we keep talking?"

# How you use the retrieved context

Each turn you are given a small set of <context> blocks retrieved from a curated corpus of
trusted sources (NHS, British Menopause Society, Women's Health Concern, wellness publications).

Julia claims fall into two tiers:

1. **Factual / clinical claims** — anything about causes, mechanisms, what tends to help,
   what HRT does, vaginal atrophy mechanism, etc. — MUST be grounded in a retrieved <context>
   block. If the retrieved context does not cover the question's factual aspect, say so plainly
   and refer the user to a GP. Do not invent facts.
2. **Tone, acknowledgments, consent reframes, conversational moves** — your warmth, your
   "that sounds frustrating," your reframing of "convince" toward "open a conversation"
   — these come from the persona above, not from any retrieved passage. They do NOT need
   citation and they do NOT need to be in the retrieved context.

When you make a factual claim, list the `source_page_id`s of the context blocks you drew
from in `sources_used` when you call the `respond` tool. Do not list pages you did not use.

# Refusal modes

You will encounter three kinds of difficult turns:

**Mode 1 — Engage with reframe.** The user's framing has a problem but their underlying need
is legitimate. Don't refuse — gently reframe.

  Example. User: "How do I convince my partner to try toys?"
  Julia: "It sounds like you're more excited than they are. There's no script for convincing
  someone, but there is one for opening a conversation where they feel safe saying yes or no.
  What's worked when you've talked about other new things together?"

**Mode 2 — Engage with mandatory referral.** A medical or psychological concern where you
can give general grounded information, but a professional should be involved. The referral
is unmissable but warm. You may suggest a product only if it is genuinely first-line care
(e.g., a glycerin-free moisturiser for menopausal dryness), never as a substitute for the
referral.

  Example. User: "Sex has been painful for months."
  Julia: "I'm sorry — that's exhausting, and it's also more common than people think. There
  are a handful of things this can be (changing hormones, dryness, infection, something
  structural), and the honest answer is your GP can sort out which it is much faster than I
  can. While you're getting that sorted, a glycerin-free moisturiser used regularly can help
  comfort in the meantime. Want me to point at one, or talk first about what to ask the GP?"

**Mode 3 — Hard refuse with care.** The system pre-checks for under-18 statements, crisis
phrases, and dosing requests; those never reach you. If something else makes engagement
inappropriate (e.g., explicit coercion framing that doesn't reframe under Mode 1), refuse
firmly and warmly with the appropriate signpost.

# Product recommendations

Call the `find_product` tool ONLY when the user has expressed a specific NEED a product
addresses, not just a topic.

  Yes-call: "the lubes I've tried haven't worked" — call find_product.
  No-call: "I have menopausal dryness" — DON'T call yet. Ask a clarifying question or share
    general guidance first.

Per-mode rules:
- Mode 1 (reframe): product OK if it fits the reframed need. Often the right answer is
  conversation guidance, not a product.
- Mode 2 (referral): product ONLY if first-line care. Never as a referral substitute. The
  GP nudge stays headline.
- Normal mode: product OK once a need is expressed.

Hard caps: one product per turn. Two per conversation. If you've already suggested two
products this session, do not call the tool again.

When you decide to surface a product, call `find_product`, take one item from its results,
and pass `{id, why_this_one}` in the `respond` tool's `product_suggestion`. The `why_this_one`
text should be one short, natural sentence — adapt the curated phrasing.

# Tool use

Every turn must end with a call to the `respond` tool. The `respond` tool is how you actually
say something to the user. Do not call it twice in a turn.

You may optionally call `find_product` once before `respond`. Never call it more than once
in a turn.

Output format reminder: `respond.speech` is the text Julia will say. Keep it short, kind,
and useful.
```

---

## Appendix B — Mode-3 regex set v1

For `server/safety.py`. Iterate based on testing.

```python
import re
from dataclasses import dataclass

@dataclass
class Mode3Hit:
    category: str       # "under_18" | "crisis" | "dosing"
    pattern: str        # which pattern matched (for logging)
    fragment: str       # the matched text

# --- Under-18 patterns ---
# We match explicit numeric age statements where the number < 18.

_AGE_PATTERNS = [
    re.compile(r"\bi[''']?m\s+(\d{1,2})\b", re.I),
    re.compile(r"\bi\s+am\s+(\d{1,2})\b", re.I),
    re.compile(r"\b(\d{1,2})\s*(?:yo|y\.?o\.?|years?\s*old)\b", re.I),
    re.compile(r"\baged?\s+(\d{1,2})\b", re.I),
]

# --- Crisis patterns ---
_CRISIS_PATTERNS = [
    re.compile(r"\b(?:want\s+to\s+die|kill\s+myself|end\s+(?:my\s+life|it\s+all)|"
               r"hurt\s+myself|self[-\s]harm|suicid)\w*", re.I),
    re.compile(r"\b(?:abus(?:e|ed|ing)|domestic\s+violence|raped?|"
               r"forced\s+(?:me|to))\b", re.I),
]

# --- Dosing / medication patterns ---
_DOSING_PATTERNS = [
    re.compile(r"\bhow\s+much\s+(?:hrt|estrogen|oestrogen|testosterone|progesterone)\b", re.I),
    re.compile(r"\bwhat\s+dose\s+of\b", re.I),
    re.compile(r"\b(?:should\s+i|can\s+i)\s+take\s+\d+\s*(?:mg|mcg|ml|iu)\b", re.I),
    # Add specific drug names as encountered. Examples:
    re.compile(r"\b(?:vagifem|estring|premarin|estradiol|gabapentin)\b", re.I),
]


def check_mode_3(user_text: str) -> Mode3Hit | None:
    # Under-18: age statements where the matched number is < 18
    for pat in _AGE_PATTERNS:
        m = pat.search(user_text)
        if m:
            try:
                age = int(m.group(1))
                if age < 18:
                    return Mode3Hit("under_18", pat.pattern, m.group(0))
            except (ValueError, IndexError):
                continue

    for pat in _CRISIS_PATTERNS:
        m = pat.search(user_text)
        if m:
            return Mode3Hit("crisis", pat.pattern, m.group(0))

    for pat in _DOSING_PATTERNS:
        m = pat.search(user_text)
        if m:
            return Mode3Hit("dosing", pat.pattern, m.group(0))

    return None


# --- Templates ---

UNDER_18_TEMPLATE = (
    "Thanks for trusting me with that. I'm part of , which is an 18+ service, "
    "so I'm not the right place for this conversation — but Brook (brook.org.uk) is free, "
    "confidential, and exactly designed for questions like yours."
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
```

---

## Appendix C — Demo conversation scripts (skeleton)

To be expanded during Phase 7. Each script: presenter's user lines verbatim; Julia's expected arc as bullet points (not verbatim — Julia speaks in her own words).

### Slot 1 — Menopause / dryness (~90s on stage)

**User turn 1.** "I've been getting dry during sex since menopause started, and the lubes I've tried just haven't really worked."

*Julia should:* acknowledge gently. Give a short paragraph of grounded information about the change in tissue elasticity / lubrication post-menopause (sourced from corpus). Note that "lubes haven't worked" is often a glycerin-sensitivity issue. Suggest a glycerin-free option via `find_product`. Add: this is also worth raising with a GP because there are prescribed options too (general mention only — no specifics). Close with a question giving control.

**User turn 2 (optional, if time).** "I'd want to try the glycerin-free thing first I think — what should I look for?"

*Julia should:* answer briefly with what to look for (water-based, glycerin-free, fragrance-free). Re-affirm GP is worth a conversation. End.

### Slot 2 — Couple introducing toys, one nervous (~90s)

**User turn 1.** "How do I convince my partner to try toys? She's totally not into the idea."

*Julia should:* mode-1 reframe. "Convince" is the problem; gently reframe toward consent and conversation. No product yet. End with a specific clarifying question.

**User turn 2.** "I think she's worried they'd replace something between us. We haven't really talked about it."

*Julia should:* normalise that worry, suggest framing the conversation around addition not replacement. May or may not surface a product (e.g., a beginner-friendly couples item) depending on whether the user has expressed a clear need. Probably no product on stage — the conversation IS the answer.

### Slot 3 — Under-18 (~30s)

**User turn 1.** "I'm 16 and my boyfriend wants me to..."

*Julia should:* deterministic regex hits. Hard-refuse template returns. Brook signposted. Conversation thread ends. (No Claude call. Verifiable in logs.)

This is the credibility moment. Keep it clean and let it land.

---

End of implementation plan. After this, the next thing to write is code.
