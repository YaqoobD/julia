# Julia

> _She's here. And she's listening._

A voice-first sexual wellness consultant. People talk to her about the things
that don't fit a search box — menopause, painful sex, low libido, partners who
won't engage — and she responds with the calm, grounded answer a good GP-friend
would give, *not* a product carousel.

Web and phone are the same conversation engine: Claude with tool use and
retrieval, Whisper for ears, ElevenLabs for voice, Vapi for the phone line.

![Julia — title slide](docs/screenshots/title-slide.png)

---

## Why this exists

Most "wellness chatbots" are either:

1. **Search-engine surrogates** — they answer like a webpage, blurt the disclaimer,
   bounce the user to a GP for anything real.
2. **Product funnels in a trench coat** — every turn ends with a buy button.

Julia is built around three habits a thoughtful sex therapist actually has:

- **Listens before she speaks.** No diagnosis on turn one. No leading questions.
  She holds the silence and lets people finish describing what's actually wrong.
- **Cites her sources.** Every factual claim is grounded in a curated corpus
  (NHS, British Menopause Society, Women's Health Concern, etc.) with visible
  references in the UI.
- **Earns the product mention.** A product only surfaces when the user has
  described a specific need a product addresses. Same for therapists. Same for
  GP referrals. The default is "talk it through", not "buy something".

The win line from the demo deck: *people close the tab feeling **heard**, not
sold to.*

---

## What it looks like

![Julia web UI — live conversation, product surfaced only after a real need was expressed](docs/screenshots/julia-web-ui.png)

A real turn from the demo arc (vaginal dryness during menopause):

> **User:** "I've been getting dry since menopause started, and the lubes I've
> tried just haven't worked."
>
> **Julia:** "If lubes haven't landed, it's worth exploring two things: whether
> a longer-term **vaginal moisturiser** (used regularly, not just during sex)
> might help more, and whether your GP should rule out anything else going on.
> Want me to talk about moisturisers, or would it help to think through what to
> ask a GP first?" *[1]*
>
> *[1] [NHS — Vaginal dryness](https://www.nhs.uk/conditions/vaginal-dryness/)*

The product card (a fictional Vela-branded moisturiser) only appears *after*
the user follows up with "tell me about moisturisers" — never on the first turn.

---

## Safety rails

Hard refusals, by design:

- **Under-18 anything** (first-party or third-party) → signposts to
  [Brook](https://www.brook.org.uk/), the UK youth sexual-health charity.
- **Dosing or naming specific medications** → defers to a pharmacist or GP.
- **Crisis content** (self-harm, abuse, suicidality) → signposts to the right
  helpline and stops the wellness flow.

These are enforced in `server/safety.py` before the model is even called.

---

## How it's built

| Layer | Tool |
| --- | --- |
| Conversation brain | Anthropic Claude (tool use + prompt caching) |
| Speech-to-text | OpenAI Whisper |
| Text-to-speech | ElevenLabs |
| Phone channel | Vapi (custom-LLM webhook → same engine) |
| Retrieval | OpenAI `text-embedding-3-large` + cosine similarity over an NPZ index |
| Backend | FastAPI (Python 3.10+) |
| Frontend | Plain HTML / vanilla JS / CSS — no framework |

Architecture, role boundaries, and the conversation-design rationale are all
written up in [`docs/`](docs/).

---

## Run it locally

### 0 · Prerequisites

- **Python 3.10 or newer** (`python3 --version`)
- **Git**
- API keys for:
  - **Anthropic** — https://console.anthropic.com/settings/keys
  - **OpenAI** — https://platform.openai.com/api-keys (used for both Whisper STT and embeddings)
  - **ElevenLabs** — https://elevenlabs.io/app/settings/api-keys (and pick a `VOICE_ID` from https://elevenlabs.io/app/voice-library)
  - **Vapi** *(optional — only if you want the phone channel)* — https://dashboard.vapi.ai

### 1 · Clone

```bash
git clone https://github.com/YaqoobD/julia.git
cd julia
```

### 2 · Create a virtual environment

Standard Python:

```bash
python3 -m venv venv
source venv/bin/activate         # macOS / Linux
# .\venv\Scripts\activate        # Windows PowerShell
```

Or with [uv](https://github.com/astral-sh/uv) if you have it:

```bash
uv venv
source .venv/bin/activate
```

### 3 · Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4 · Configure environment variables

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...

# Optional — phone channel only
VAPI_PUBLIC_KEY=...
VAPI_PRIVATE_KEY=...
VAPI_ASSISTANT_ID=...
```

`.env` is gitignored — your keys never go upstream.

### 5 · Build the retrieval corpus

Julia won't start without a corpus index — that's where her facts come from.

```bash
python scripts/fetch_corpus.py     # scrapes URLs in data/corpus/sources.txt → markdown
python scripts/index_corpus.py     # embeds the markdown → data/corpus_index.npz
```

A starter `sources.txt` is included (NHS / Women's Health Concern / BMS pages).
Add or remove URLs and re-run with `--force` to refresh.

### 6 · Start the server

```bash
uvicorn server.main:app --reload --port 8765
```

Open **http://localhost:8765** in Chrome (allow microphone access when prompted).

### 7 · *(Optional)* Run the demo deck

```bash
python -m http.server 8000 --directory presentation
```

Open **http://localhost:8000** — `f` for fullscreen, `s` for speaker notes,
arrows to navigate.

---

## Daily workflow (TL;DR)

```bash
cd julia
source venv/bin/activate
uvicorn server.main:app --reload --port 8765
```

`Ctrl+C` to stop the server, `deactivate` to leave the venv.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` on port 8765 | Use a different port: `--port 8766`, or kill the holder: `lsof -ti :8765 \| xargs -r kill` |
| `Corpus index not found` on startup | You skipped step 5 — run `scripts/fetch_corpus.py` then `scripts/index_corpus.py` |
| `ANTHROPIC_API_KEY not set` (or any provider key) | `.env` is missing or the key is blank. Restart `uvicorn` after editing `.env` so it re-reads the file |
| Microphone doesn't activate | Use Chrome on `http://localhost` (Firefox/Safari can be strict about mic on plain HTTP); also confirm OS-level mic permission for the browser |
| 401 / 403 from a provider | Key invalid or out of credit. Check the uvicorn logs for which provider failed |
| Phone channel doesn't connect | Vapi keys not set, or `VAPI_ASSISTANT_ID` doesn't point at an assistant configured to call back into your local tunnel |

---

## Bring your own corpus

`scripts/fetch_corpus.py` reads URLs from `data/corpus/sources.txt` and writes
clean markdown into `data/corpus/`. `scripts/index_corpus.py` then embeds those
markdown files into `data/corpus_index.npz` for cosine retrieval at turn time.

The scraped markdown is gitignored — only `sources.txt` ships, so the corpus
stays reproducible without republishing third-party content. See
[`docs/julia-implementation-plan.md`](docs/julia-implementation-plan.md) §6 for
source-selection guidance.

---

## Project layout

```
server/         FastAPI backend (conversation engine, channel adapters, safety, tools)
data/           products.json (fictional), practitioners.json (fictional), corpus/ (BYO)
scripts/        Offline scripts (corpus fetch + index, conversation test, prewarm)
web/            Frontend (HTML/JS/CSS — no framework)
presentation/   reveal.js demo deck
docs/           Planning artifacts (spec, architecture, tech stack, runbooks)
```

## Documents

Working artifacts from the build, in [`docs/`](docs/):

- [`julia-spec.md`](docs/julia-spec.md) — what Julia does (product spec)
- [`julia-architecture.md`](docs/julia-architecture.md) — how the system is shaped (roles, data flow)
- [`julia-tech-stack.md`](docs/julia-tech-stack.md) — which specific tools fill each role
- [`julia-implementation-plan.md`](docs/julia-implementation-plan.md) — the build broken into phases
- [`julia-stage-demo-script.md`](docs/julia-stage-demo-script.md) — stage-demo runbook (web channel)
- [`julia-phone-test-script.md`](docs/julia-phone-test-script.md) — phone-channel test runbook

---

## What this isn't

- **Not production-ready.** No auth, no rate limiting, no persistent storage —
  in-memory session state only.
- **Not multi-user.** Single-process FastAPI, in-memory session dict.
- **Not load-tested.**
- **Not a real service.** The product catalogue is fictional (Vela-branded for
  the demo). The practitioner directory uses Ofcom drama-reserved phone numbers
  and `example.com` URLs.

This is a portfolio / demo project — built to think through what voice-first
sexual-wellness guidance *should* feel like, not to ship one tomorrow.

---

## License

MIT — see [LICENSE](LICENSE).
