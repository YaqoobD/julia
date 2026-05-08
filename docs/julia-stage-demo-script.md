# Julia — stage demo script

What you, the presenter, do and say in front of the room. Two web
conversations (product + practitioner), one short safeguarding beat, one
phone encore. Total runtime ~5–6 minutes plus Q&A.

> Companion docs: `docs/julia-spec.md` §8 (demo plan), `docs/julia-spec.md` §9
> (the pitch + Q&A lines), `docs/julia-phone-test-script.md` (the testing
> sheet — not for stage).

---

## Pre-flight (do this in the 10 minutes before you go on)

- [ ] FastAPI server running: `uvicorn server.main:app --port 8765 --host 127.0.0.1`
- [ ] Cloudflare Tunnel up so Vapi can reach the server:
      `cloudflared tunnel run julia-demo` — confirm the public URL still
      matches what's set as `model.url` on Vapi assistant `523e40d7-…`.
- [ ] Browser open at `http://127.0.0.1:8765/`. Hard-refresh (Ctrl+Shift+R)
      so cached CSS isn't stale.
- [ ] Mic permission already granted in Chrome for `127.0.0.1:8765`
      (do this BEFORE going on stage — the permission prompt mid-demo is
      the worst possible moment).
- [ ] **Prewarm**: send one throwaway turn ("hi") in the browser so the
      Anthropic prompt cache is hot. Then refresh the page so the chat
      column is clean for the audience.
- [ ] Phone in hand, on silent, Vapi test number already dialled-and-saved
      as a contact so you don't have to type digits on stage.
- [ ] Backup video of the full demo open in a second tab, ready to play if
      audio fails or the network dies.
- [ ] Speakers on, volume tested. The voice loop is the wow moment — if
      the room can't hear it, the demo doesn't land.

---

## Opening (≈30s)

Stand. Browser visible. No live Julia on screen yet — empty chat with the
hint phrasings ghosted in the composer.

> **Say**: "already has a recommendation engine, and a customer
> service AI. Neither of those can ethically or usefully discuss painful
> sex, menopausal dryness, or libido changes. Julia is the third pillar —
> a sexual wellness consultant grounded in NHS, BMS, and Women's Health
> Concern guidance, with sources cited openly, and access to our
> catalogue when products are genuinely part of the answer."

> **Say** (point at the screen): "She runs in two places. The website,
> which you're looking at — voice both ways. And the phone. Same Julia,
> different way in. We'll do both."

Click into the chat composer.

---

## Demo 1 — Menopause / dryness (web, voice) (≈90s)

**The point**: Julia recognises a real-life concern, gives grounded
information, surfaces a relevant product because the user has explicitly
asked for one, and nudges to a GP. References panel fills in real time.

### Turn 1

Click the mic button. Speak this line clearly into the mic:

> "I've been getting dry during sex since menopause started, and the
> lubes I've tried just haven't really worked."

Hit Send.

> **Bridge while Julia thinks** (≈2–3s): "Watch the right-hand panel.
> Every claim Julia makes is grounded in something a real clinician
> wrote, and the source slides in here as she answers."

**Watch for**:

- Julia's voice plays automatically.
- 1–2 NHS / Women's Health Concern source cards appear in the references
  panel on the right.
- A product card may already appear here — usually a glycerin-free lube
  (Sylk, Replens, YES VM, or similar). If it does, point at it now and
  jump to "Closing line" below. If it doesn't, go to Turn 2.

### Turn 2 (only if no product on Turn 1)

Click the mic again. Speak:

> "Yes please — what would you reach for?"

> **Bridge**: "She'll only suggest a product when the user has asked for
> one. That's the discipline — Julia isn't a salesperson."

**Watch for**:

- Product card slides in below the chat. Coral "IRIS SUGGESTS" label,
  product name, price, the *why this one* line, "View product" link.
- Julia speaks the brand name aloud as part of her reply.
- Julia drops a GP nudge somewhere ("worth raising with your GP — there
  are prescribed options too").

### Closing line for Demo 1

> **Say** (point at the references panel, then the product card):
> "Two NHS sources, one product, GP nudge. That's the shape — grounded
> information, products only when products help, and a clinician
> handoff baked in."

### If Demo 1 goes off-script

- **Julia recommends a product on Turn 1 with no GP nudge** → fine, move
  on. Mention the GP nudge yourself: "and notice in production she'd
  also signpost a GP for prescribed options."
- **No product appears even after "yes please"** → say "she's being
  cautious here — that's the failure mode I'd rather have than the
  opposite. Let's move on." Don't push for a third turn.
- **Voice doesn't autoplay** → Chrome blocked autoplay. Click anywhere
  on the page, then click the small replay button next to Julia's
  message. Say nothing — recover silently.
- **STT mistranscribes** → the transcript is visible. Edit it in the
  textarea before hitting Send.

---

## Demo 2 — Long-term low desire → couples therapist (web, voice) (≈90s)

**The point**: not every answer is a product. Sometimes the right answer
is a person. Julia asks for the user's location, then surfaces a
practitioner — different card, different colour, different shape.

> **Say** (between demos): "OK, second one. This time it's not a product
> question."

### Turn 1

Click the mic. Speak:

> "We've been together eight years and sex has just stopped. We've
> talked about it, nothing changes. I think we need a couples
> therapist."

Hit Send.

> **Bridge**: "Watch — she's not going to recommend anyone yet. She'll
> ask where I'm based first, because the directory is location-aware."

**Watch for**:

- Julia validates the situation warmly ("that eight-year drift…").
- She asks for **location** OR whether you're open to **online**.
- **No** practitioner card appears yet.

### Turn 2

Click the mic. Speak:

> "I'm in Manchester."

> **Bridge while Julia thinks**: "Now watch the card. Different colour
> from the product card on purpose — this is a person, not a thing."

**Watch for**:

- Practitioner card slides in with **periwinkle / blue** accent (not
  coral). A specific Manchester therapist — likely *Dr. Iain Calder* or
  *Ms. Priya Bhatt*. Card shows specialty badges, In-person/Online
  badges, "Visit website →" link.
- The italic line at the bottom of the card: *"Demo directory — not a
  real listing."*

### Mandatory line (do not skip)

Point at the disclaimer line on the card. Say:

> **Say**: "The directory shown here is illustrative — names and
> contacts are placeholders. In production this is the COSRT-accredited
> list. The capability is real; the data is fake on purpose so we don't
> ship a hackathon directory live."

### Closing line for Demo 2

> **Say**: "So — same Julia, completely different shape of answer. No
> product. A human, a city, a contact path. That's Mode 2 — when the
> right next step is a person."

### If Demo 2 goes off-script

- **Julia names a therapist on Turn 1 without asking for location** →
  awkward but not fatal. Say: "she's jumped ahead — in the version
  shipped she asks for location first, that's a system-prompt
  regression I'll log." Move on.
- **Julia recommends a product** → say "wrong tool for this question —
  this is exactly the failure mode the system prompt is supposed to
  prevent. Logging it." Move on.
- **No practitioner card appears at all** → check the chat: did Julia
  speak the practitioner's name? If yes, point at the speech and say
  "card render lagged — name's there in the speech." If no, skip to
  Demo 3.

---

## Demo 3 — The safeguarding beat (web, ~20s)

**The point**: Julia will refuse to discuss anything sexual with anyone
who identifies as under-18. Not "she should" — the refusal is
deterministic, not a model judgment call. This is the credibility
moment that pre-empts every safeguarding question.

> **Say**: "One more thing before phone — the question every safeguarding
> exec will ask. 'What if a 16-year-old finds this?'"

Type into the textarea (don't use the mic — typing makes the moment
faster and audience can read along):

> `I'm 16 and my boyfriend wants me to try something`

Hit Send.

**Watch for**:

- Reply appears almost instantly (regex pre-check, no Anthropic call).
- Julia signposts to **Brook** (brook.org.uk) and ends the conversation.
- The textarea, mic, and Send button **disable** (greyed out). The
  conversation thread is over.

> **Say** (let it land for a beat): "Detection is a regex, not a
> judgment call by the model. Hard refuse, signpost to Brook, end the
> thread. That's a defensible safeguard story you can put in front of
> legal."

### If Demo 3 goes off-script

- **Julia engages with the question** → critical bug. Say "that's broken
  and I'll fix it before launch — the regex didn't fire." Move on
  fast — don't dwell.
- **Composer doesn't disable** → cosmetic. Say nothing, move on.

---

## Encore — Phone (≈60s)

**The point**: Same Julia, different way in.

Refresh the browser (so the safeguarding-locked state clears for the
audience to see Julia is alive again). Pick up your phone.

> **Say**: "Last thing. Same Julia, completely different channel. I'm
> dialling now."

Dial the saved Vapi contact. Put the phone on speaker. Hold it near the
mic of your laptop OR up to the room's mic if there's a podium one.

Wait for the greeting. Then say into the phone:

> "I've been getting dry during sex since menopause and the lubes I've
> tried just haven't really worked. What would you reach for?"

> **Bridge while waiting**: "No screen, no card. On phone Julia has to
> say the brand name out loud — she knows that, because the system
> prompt knows what channel she's on."

**Watch (listen) for**:

- Julia speaks a brand name aloud (Sylk / Replens / YES VM / Pjur / etc).
- Julia drops a GP nudge.
- Reply lands within a few seconds. Cold-start lag here is the biggest
  risk — that's why we prewarmed.

After Julia finishes, say into the phone:

> "Thanks Julia."

Hang up.

> **Say** (to the room): "Same engine, same persona, no UI. The phone
> doesn't have a card to fall back on, so the system prompt knows it's
> on phone and names the brand in speech."

### If the phone encore goes off-script

- **Long silence after dialling** → it's connecting. Don't fill it
  nervously — say "connecting" once and wait. If 10s passes with no
  audio, hang up and say "connection didn't land — let me show you the
  recording" and play the backup video clip of phone audio.
- **Julia doesn't name a brand** → say "she's hedged here — on web she'd
  show the card, on phone she should name it." Don't pretend it
  worked.
- **Echo / feedback loop between phone speaker and laptop mic** → mute
  the laptop mic before holding the phone up. Practise this beforehand.

---

## Close (≈30s)

> **Say**: "What you've just seen is end-to-end working software —
> grounded conversation, two channels, two card shapes, deterministic
> safeguarding. The roadmap from here is real cross-channel session
> continuity, real COSRT-accredited practitioner data, additional
> wellness journeys beyond menopause, multi-language, and embedded
> versions on PDPs — *not sure if this is right? ask Julia*. Happy to
> take questions."

Stand, hands off the keyboard. Wait for Q&A.

---

## Likely Q&A — short answers (full versions in spec §9)

| Question                                          | One-line answer                                                                                                                                              |
|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| "Couldn't ChatGPT do this?"                       | "Not grounded in NHS guidance, doesn't know our catalogue, no relationship to our brand. Julia is the layer that does all three."                             |
| "How do you stop hallucination?"                  | "Three layers: prompt restricts what she claims, medical claims are grounded in retrieved corpus passages, sources are visibly cited."                       |
| "What's the business case?"                       | "Brand-led trust drives long-term loyalty. Conversion uplift is a second-order effect, not the optimisation target."                                         |
| "What about safeguarding?"                        | "You just saw it. Detection is deterministic regex, not a model judgment call."                                                                              |
| "Voice both ways for sensitive content — bad UX?" | "For the demo, voice is the most powerful way to feel the product. In production we'd test voice carefully against text — there are real reasons for both." |
| "Is the practitioner directory real?"             | "No — illustrative. Replacing with the COSRT-accredited list is a post-hackathon task."                                                                      |
| "Are the product recommendations real ?" | "The catalogue and metadata are curated for the demo — IDs and PDP URLs need verifying against the live site before we ship."                                |

---

## Pacing markers (to time yourself)

| Section                  | Target  | Hard cap |
|--------------------------|---------|----------|
| Opening                  | 30s     | 45s      |
| Demo 1 — menopause       | 90s     | 2m       |
| Demo 2 — practitioner    | 90s     | 2m       |
| Demo 3 — safeguarding    | 20s     | 30s      |
| Phone encore             | 60s     | 90s      |
| Close                    | 30s     | 45s      |
| **Total demo runtime**   | **5m**  | **7m**   |

If you hit the hard cap on Demo 1 or 2, stop the conversation at the next
natural beat. Don't try to land Turn 3 — the audience is already with
you. The cut-list is in `docs/julia-implementation-plan.md` §13 if you
need to drop further.

---

## What to do if everything breaks

Open the second tab. Play the backup video. Say:

> "The live demo is misbehaving and I'd rather show you what working
> looks like than fight it on stage. This is a recording from earlier
> today — same code, same Julia."

Let the video play. Take the Q&A on the back of it. The judges will
respect the call.
