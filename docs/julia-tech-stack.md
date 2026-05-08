# Julia — Tech Stack Doc

This doc commits to specific vendors, libraries, and model versions for every role the architecture doc named generically. It is deliberately opinionated. Each pick states the reason and (where the choice is borderline) the alternatives considered.

Read `julia-spec.md` and `julia-architecture.md` first. This doc assumes the architecture is settled and only fills in the "which product" beneath each "which role."

The next artifact after this is the implementation plan (file layout, build order, rehearsal cadence).

---

## 1. Stack at a glance

| Role | Pick | Why |
|---|---|---|
| Backend language + framework | Python 3.11 + FastAPI | All AI SDKs (Anthropic, OpenAI, ElevenLabs, Vapi) are Python-native. FastAPI gives webhook handling, async, and static file serving in one library. |
| Conversation model | `claude-sonnet-4-5` via Anthropic SDK, with prompt caching enabled | Spec calls Sonnet 4.5 explicitly. Promote to `claude-opus-4-7` only if Sonnet misses nuance during prompt tuning. |
| STT (web inbound) | OpenAI Whisper API (`whisper-1`) | One HTTP POST, returns text. Spec already names Whisper as the visible-transcript option, which architecture confirmed as a feature not a fallback. |
| TTS (web outbound) | ElevenLabs streaming (WebSocket) | Spec locks ElevenLabs. Streaming WebSocket enables sentence-by-sentence playback in the latency budget. |
| Embeddings | OpenAI `text-embedding-3-large` | 3072-dim, strong on short query/long doc retrieval, simple API. |
| Vector store | NumPy in-memory flat cosine | ~250 chunks total. Flat search is ~30 lines. Chroma / FAISS would be over-engineered at this scale. |
| Phone channel | Vapi (pay-as-you-go, $10 starter credit) | Spec locks Vapi. $10 free credit on signup covers hackathon usage. Test number provisioned immediately; optional swap to your provisioned number if procurement lands. |
| Frontend | Plain HTML + vanilla JS + plain CSS, served as FastAPI static files | Single page (chat + reference table + product card). No build step. No framework tax. |
| Web ↔ backend transport | HTTP for sync requests + Server-Sent Events (SSE) for the streamed turn response | Simpler than WebSocket; native to browsers; FastAPI handles both directly. |
| Corpus scraper | `trafilatura` | Clean main-content extraction from HTML — strips nav/footer/sidebar/scripts automatically. Used in `scripts/fetch_corpus.py` to turn the curated URL list into clean markdown without manual copy-paste. |
| Hosting (during build & demo) | Localhost + Cloudflare Tunnel (`cloudflared`) | Tunnel gives Vapi a public webhook URL pointing at the laptop. No deployment platform needed for a hackathon. |
| Secrets | `.env` + `python-dotenv` | Hackathon-grade. Do not commit `.env`. |
| Observability | Standard `logging` to stdout, structured JSON if convenient | Console is enough; this is not production. |
| Dependency management | `uv` (or `pip` + `requirements.txt` if `uv` is not on hand) | `uv` is dramatically faster for fresh Python project setup, but pip is fine. |
| Python version | 3.11 | Modern type-hint support, broad SDK compatibility. |

---

## 2. Backend: Python 3.11 + FastAPI

**Pick:** FastAPI on Python 3.11.

**Reasons:**

- All four major SDKs Julia depends on (Anthropic, OpenAI, ElevenLabs, Vapi) have first-class Python clients. Same language for all integrations cuts cognitive load to one set of idioms.
- FastAPI handles three things Julia needs in one library: HTTP endpoints (web channel), webhook handlers (Vapi phone), and static file serving (frontend HTML/JS/CSS).
- Async is first-class — useful for streaming TTS and parallelising independent calls (e.g., embedding the query while preparing the system prompt).
- Pydantic models for request/response validation match the structured-output discipline of the architecture (tool schemas, response shapes).

**Alternatives considered:**

- *Node.js + Express*: Anthropic and OpenAI both have first-class TypeScript SDKs. Reasonable choice if the developer's strongest language is JS. For a solo + 1wk Python developer, Python wins on SDK breadth (Whisper API client maturity, Vapi Python helpers).
- *Flask*: simpler than FastAPI but no async support in the same idiomatic way; SSE is more verbose. FastAPI wins for the streaming use case.

**Versions to lock:**

- `fastapi` >= 0.110
- `uvicorn[standard]` >= 0.27 (ASGI server)
- `python-dotenv` >= 1.0
- `httpx` >= 0.27 (for any direct HTTP needs outside the SDKs)

---

## 3. Conversation model: Claude Sonnet 4.5 (with prompt caching)

**Pick:** `claude-sonnet-4-5` via the Anthropic Python SDK. Promote to `claude-opus-4-7` only if Sonnet falls short during prompt tuning.

**Reasons:**

- Spec specifies Sonnet 4.5 explicitly with Opus as fallback. No reason to override.
- Sonnet handles tool-use loops well (one or two tool calls in series) with low latency, which is what Julia's `find_product` → `respond` pattern needs.
- Cheaper than Opus; price matters less for a hackathon but per-turn latency matters a lot — Sonnet's first-token latency is meaningfully better.

**Tool use:**

- Two tools defined: `find_product` and `respond` (per architecture doc §6).
- `tool_choice` strategy: model-driven for `find_product` (model decides if it calls), then enforce that the turn ends with a `respond` call. Implementation can use `tool_choice={"type": "any"}` to force tool use, with the system prompt constraining which tool comes when.

**Prompt caching (load-bearing for latency):**

- The system prompt is large (persona + grounding rule + mode-1/2/3 few-shot examples + product trigger rules + tool definitions). It does not change per turn.
- Mark the system prompt as cached. The Anthropic SDK supports `cache_control: {"type": "ephemeral"}` blocks; place a cache breakpoint at the end of the system prompt block.
- The retrieved chunks for a given turn are also stable across the tool-use loop's two calls (find_product call, then respond call). Cache them for the second call too.
- Result: the second call within a turn (after `find_product` returns) reads almost everything from cache, dropping ~hundreds of ms.

**SDK:**

- `anthropic` >= 0.40 (Python SDK).
- Use `client.messages.create` with `tools=[find_product_tool, respond_tool]`.
- For streaming text out (so SSE can forward to TTS sentence-by-sentence): `client.messages.stream`.

---

## 4. STT (web channel): OpenAI Whisper

**Pick:** OpenAI Whisper API, model `whisper-1`.

**Reasons:**

- Spec lists Whisper as one of two STT options; architecture confirmed visible-transcript-as-feature, which is the case Whisper is explicitly named for.
- Single HTTP request: POST audio file, receive text. No streaming needed for the demo (user holds-to-record or finishes-then-submits).
- Solid handling of UK accents and clinical/wellness vocabulary out of the box.

**Alternatives considered:**

- *Claude native audio input* (Anthropic SDK supports audio in some configurations): cleaner integration, but architecture commits to a separate visible-transcript step which makes a dedicated STT step the right shape. Skipping it for this build.
- *Deepgram Nova*: arguably faster for streaming use cases; not needed for the non-streaming demo and adds another vendor account.

**SDK:**

- `openai` >= 1.40 (Python SDK).
- `client.audio.transcriptions.create(model="whisper-1", file=audio_bytes, response_format="text")`.

**Capture format:**

- Browser captures microphone audio as WebM/Opus (default for `MediaRecorder`).
- Whisper accepts WebM directly — no transcoding needed.

---

## 5. TTS: ElevenLabs streaming

**Pick:** ElevenLabs streaming API via WebSocket.

**Reasons:**

- Spec locks ElevenLabs. The voice character ("warm British female") is doing real work for the demo and ElevenLabs delivers on it.
- WebSocket streaming returns audio chunks as the model generates text, enabling sentence-by-sentence playback per architecture §3 step 5.

**Voice selection:**

- Sample 3–5 voices on the ElevenLabs Voice Library before Saturday. Lock one voice ID before the build begins. Document the chosen voice ID in `.env` as `ELEVENLABS_VOICE_ID`.
- Recommendation: filter Voice Library by language=English, gender=female, accent=British. Audition with a sentence from one of the demo conversations — the voice has to land Julia's tone.

**Model selection:**

- Use `eleven_turbo_v2_5` for low-latency streaming. The premium quality of `eleven_multilingual_v2` is overkill for the demo scenarios and adds latency.

**Streaming pattern:**

- ElevenLabs WebSocket: send text chunks as they arrive from Anthropic SDK's streaming response; receive PCM/MP3 audio chunks; forward to browser.
- Browser plays audio chunks via `MediaSource` API or simpler `<audio>` element with chunked playback.
- Failure mode: if the WebSocket falters, fall back to a single REST call to `POST /text-to-speech/{voice_id}` for the full response — accepts a 1.5–2s wait for the demo (spec cut list item 2).

**SDK:**

- `elevenlabs` >= 1.0 (Python SDK), or direct WebSocket via `websockets` library if the SDK abstraction gets in the way.

**Account tier:**

- Creator tier ($22/mo) is sufficient for hackathon usage. Audit usage after demo day.

---

## 6. Embeddings: OpenAI text-embedding-3-large

**Pick:** OpenAI `text-embedding-3-large`. 3072-dimensional vectors.

**Reasons:**

- Strong retrieval quality on the kind of short-query / longer-passage matching Julia does ("sex after menopause" → an NHS paragraph on vaginal atrophy).
- Cheap at this scale. ~250 chunks indexed once + ~5 query embeddings per turn = pennies for the hackathon.
- Already paying for an OpenAI account for Whisper, so no additional vendor.

**Alternatives considered:**

- *Voyage `voyage-3`*: strong on retrieval, often beats OpenAI on benchmarks. Adds an account. Save for a v2 if quality matters more than pragma.
- *Anthropic-native embeddings*: not currently first-party; skip.

**SDK:**

- Same `openai` package as Whisper.
- `client.embeddings.create(model="text-embedding-3-large", input=text)` returns `data[0].embedding` as a 3072-element list of floats.

**Indexing call:**

- Run once, on the curated corpus, before the demo. Cache the result on disk as a NumPy `.npz` (or pickle) file. The runtime backend loads it on startup.

---

## 7. Vector store: NumPy in-memory flat cosine

**Pick:** A NumPy 2-D array of embeddings + a parallel list of `{chunk_id, source_page_id, source_url, text}`. Cosine similarity via `numpy.dot` after L2-normalising on load.

**Reasons:**

- ~250 chunks × 3072 dims = 6 MB in memory. Flat scan is sub-millisecond on commodity hardware.
- ~30 lines of code. No service to run, no schema to migrate, no client SDK to learn.
- Architecture explicitly leaves the vector store choice non-load-bearing — any reasonable choice fits.

**Alternatives considered:**

- *Chroma*: would work, adds a dependency and a persistence story for no benefit at 250 chunks.
- *FAISS*: optimised for millions of vectors. Pure overkill here. Adds install complexity (system libraries).
- *sklearn `cosine_similarity`*: equivalent to the NumPy approach with one extra dependency. NumPy alone is simpler.

**Implementation sketch:**

- Load `.npz` file at startup → `embeddings: np.ndarray (N, 3072)`, `metadata: List[dict] (N entries)`.
- L2-normalise embeddings on load.
- Query: embed query → L2-normalise → `scores = embeddings @ query`, `top_k = np.argsort(scores)[-K:][::-1]`, return `[metadata[i] for i in top_k]`.

**Chunking strategy (executed at index time, not runtime):**

- Spec calls for ~500-token chunks with overlap. Use `tiktoken` (OpenAI's tokenizer) to split source pages into 500-token chunks with 50-token overlap.
- One source page produces multiple chunks but they all carry the same `source_page_id`. The reference table aggregates by `source_page_id`, not chunk.

---

## 8. Phone channel: Vapi

**Pick:** Vapi.

**Reasons:**

- Spec locks Vapi. Vapi is purpose-built for AI voice agents over phone — handles STT, TTS, and turn-taking internally so the backend only sees text via webhook.
- Vapi provisions a test number immediately on signup, removing dependency on procurement for the build phase.

**Pricing model (pay-as-you-go):**

- $10 starter credit on signup — covers a hackathon project comfortably.
- Calls: $0.05/min Vapi orchestration fee + at-cost passthrough for the STT/LLM/TTS providers Vapi calls under the hood.
- One concurrent call line is included; extra lines are $10/line/month (not needed for the demo).
- For Julia: the demo encore is ~30 seconds of call time, plus development testing maybe 30–60 minutes total. Well under the $10 starter credit.

**Setup:**

- Create Vapi account, generate a test number.
- Configure the assistant to forward each user utterance (post their internal STT) to Julia's webhook endpoint, then read the returned text via Vapi's TTS.
- Vapi assistant config:
  - `model: { provider: "custom-llm", url: "<cloudflared-public-url>/vapi/webhook" }` — point to the FastAPI Vapi adapter exposed via Cloudflare Tunnel.
  - `voice`: choose a Vapi-supported voice that approximates the ElevenLabs choice (perfect parity is not the goal; "same Julia, different way in" is the framing).
  - `firstMessage`: a brief Julia greeting that matches the web opening.

**Webhook contract:**

- Vapi POSTs `{message: {role, content}, ...}` to `/vapi/webhook`. Adapter extracts the latest user content, passes to the conversation engine with `channel="phone"`, returns `{response: <speech text>}`.
- The conversation engine's `sources_used` and `product_suggestion` outputs are discarded for the phone channel (per architecture §4.2).

**Procurement plan:**

- Day 1: file procurement request for a real number routed to Vapi.
- If it lands by Friday: update Vapi's number config, no other code changes needed.
- If not: demo from the Vapi test number. Audience does not care.

---

## 9. Frontend: plain HTML + vanilla JS + plain CSS

**Pick:** A single `index.html` + `chat.js` + `style.css`, served as static files by FastAPI. palette (coral / cream).

**Reasons:**

- One page (chat + reference table + product card). No client-side routing. No state library. Frameworks add a build step that costs more time than it saves at this scale.
- Solo + 1wk: keep total moving parts low.
- Browser APIs needed (MediaRecorder for mic, MediaSource or `<audio>` for TTS playback, EventSource for SSE) are all standard.

**Alternatives considered:**

- *React*: appropriate if the developer already has a comfortable Vite + React skeleton. Otherwise the toolchain tax (Vite, npm, build, hot reload) outweighs the ergonomic benefit on a single page.
- *Next.js*: same point, larger.
- *htmx*: tempting for the chat-style append pattern; not needed because vanilla JS handles SSE and DOM append in ~50 lines.

**UI structure:**

- Top: Julia persona block (name, AI disclosure, source line).
- Middle: chat scroll area. Each turn renders user message bubble + Julia reply bubble. Julia bubble has a small audio control to replay TTS.
- Right side panel: reference table (sticky). Each row: source title, clickable URL.
- Below chat input: product card area (empty until first product surfaces).
- Input area: textarea + mic button + send button. Mic button records via MediaRecorder, on stop POSTs audio to `/api/stt`, fills textarea with transcript, user can edit before sending.

**Styling:**

- coral (`#F26A6A` approximate) for accents and the persona header.
- Cream (`#FAF6F0` approximate) for background.
- System font stack — no web fonts (latency, polish doesn't move the needle).

---

## 10. Web ↔ backend transport: HTTP + Server-Sent Events

**Pick:**

- HTTP POST `/api/turn` (request: `{session_id, user_text}`) returning a Server-Sent Event stream.
- Stream events: `text_chunk`, `source`, `product`, `done`. Browser EventSource handler updates UI as events arrive.
- Sibling endpoints: HTTP POST `/api/stt` (audio in → transcript out), HTTP POST `/api/tts` (text in → audio out, fallback only — primary TTS path is server-side WebSocket to ElevenLabs).

**Reasons:**

- SSE is one-direction (server → browser) which is exactly the streaming need. WebSocket would add complexity (heartbeats, reconnect logic) for no extra capability.
- FastAPI has first-class SSE support via `fastapi.responses.StreamingResponse` with `text/event-stream`.
- Browser EventSource is one line to set up.

**Audio path:**

- TTS audio bytes are NOT sent over SSE (binary in SSE is awkward). Instead: the server-side TTS WebSocket to ElevenLabs returns audio chunks; the FastAPI route streams those chunks as a separate `audio/mpeg` HTTP response, consumed by an `<audio>` element with a streaming source.
- For the simpler initial build, accept "TTS plays once full text is generated" — a 1.5–2s wait per turn — and skip audio chunk streaming (cut list item 2 made this presumptively cuttable).

**Alternatives considered:**

- *WebSocket*: more capability than needed; SSE is the right tool.
- *Long polling*: works, but worse UX and not simpler than SSE.

---

## 11. Hosting / deployment: localhost + Cloudflare Tunnel

**Pick:**

- Run everything on the developer's laptop during build and demo.
- Expose the FastAPI server publicly via Cloudflare Tunnel (`cloudflared tunnel run`) so Vapi's webhooks can reach localhost.

**Reasons:**

- Demo is on stage from a laptop. There is no production traffic. A cloud deployment costs time without buying anything for this audience.
- Cloudflare Tunnel is free, faster than ngrok's free tier, and gives a stable hostname (after one-time `cloudflared tunnel create`).

**Setup:**

- `cloudflared tunnel create julia-demo`
- `cloudflared tunnel route dns julia-demo julia-demo.<your-domain>`  *(if a domain is on hand; otherwise use the auto-generated `*.trycloudflare.com` URL)*
- `cloudflared tunnel run julia-demo` — points to `localhost:8000`.

**Alternatives considered:**

- *ngrok*: works, free tier has a churning hostname which means re-pasting into Vapi config every restart.
- *Cloud platform (Fly.io, Railway, Vercel)*: appropriate if there were a real user base; for a 5-min stage demo the deploy/redeploy loop slows iteration.

**Pre-stage checklist:**

- Tunnel up before Vapi any pre-warm calls.
- Hard-wire laptop ethernet if available — wifi at venues is unpredictable.

---

## 12. Secrets

**Pick:** `.env` file at the project root, loaded via `python-dotenv` at startup.

**Required keys:**

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
VAPI_API_KEY=...
VAPI_ASSISTANT_ID=...
```

**Hygiene:**

- `.gitignore` includes `.env` from the first commit.
- A `.env.example` checked in with the variable names but no values.
- No keys in logs, no keys in screenshots during the demo.

---

## 13. Observability

**Pick:** Python `logging` to stdout. Structured JSON formatter optional.

**What to log per turn:**

- `session_id`, `channel`, `user_text` (or hash if user privacy matters in tests).
- Mode-3 router result (hit / miss, which pattern if hit).
- Retrieval result: top-K chunk IDs and similarity scores.
- Tool calls made by the model (`find_product` args + result count).
- Final `respond` payload: `sources_used` (post-validation), whether `product_suggestion` was surfaced.
- Total turn latency.

**What NOT to add:**

- Sentry, Datadog, OpenTelemetry — production gear, no value for a one-stage-demo project.

---

## 14. Dev environment

**Python version:** 3.11. Modern type hints, broad SDK compatibility.

**Package manager:** `uv` if installable in 30 seconds; otherwise plain `pip` + `requirements.txt`.

**Virtual environment:** `uv venv` or `python -m venv .venv`.

**Editor:** any. The codebase is small enough that VS Code, Cursor, or terminal-only all work.

**OS:** Linux or macOS for the build (the developer is on Linux per the environment context). Cloudflare Tunnel works the same way.

---

## 15. Approximate cost estimate (hackathon week + demo)

Rough order of magnitude only. is presumably comfortable with all of these.

| Vendor | Estimated spend |
|---|---|
| Anthropic | $5–15 (development + demo turns; prompt caching reduces this further) |
| OpenAI (Whisper + embeddings) | $1–3 |
| ElevenLabs Creator tier | $22/month (one month minimum) |
| Vapi pay-as-you-go | $0 (covered by $10 starter credit; demo + testing well under that) |
| Cloudflare Tunnel | $0 |
| **Total** | ~$30–40 |

Outside cost: time. The big one.

---

## 16. What this doc still leaves to the implementation plan

The next-stage artifact (implementation plan) covers:

- Concrete file and module layout.
- Day-by-day build order across the prep week.
- Exact regex patterns for the mode-3 router (drafted in spec §4 but final form lives with the safety code).
- Exact system-prompt text including the few-shot examples for each refusal mode.
- Test/rehearsal cadence: when to hand-test the backend, when to rehearse with timer, when to record the backup video.
- Pre-warm checklist for stage day.

---

End of tech-stack doc. Implementation plan is the next artifact.
