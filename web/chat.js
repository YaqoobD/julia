// Julia web chat client. POSTs each user turn to /api/turn, renders the
// response into the chat region, dedupes sources_used into the reference
// table, and renders product_suggestion as a card. Disables input on ended.

(() => {
  const sessionId = (crypto.randomUUID && crypto.randomUUID()) ||
    `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const chatEl = document.getElementById("chat");
  const formEl = document.getElementById("composer");
  const inputEl = document.getElementById("input");
  const sendBtn = document.getElementById("send-btn");
  const micBtn = document.getElementById("mic-btn");
  const hintsEl = document.getElementById("hints");
  const refListEl = document.getElementById("ref-list");
  const refEmptyEl = document.getElementById("ref-empty");
  const productRegionEl = document.getElementById("product-card-region");
  const practitionerRegionEl = document.getElementById("practitioner-card-region");
  const stopSpeakingBtn = document.getElementById("stop-speaking-btn");
  // The orb button itself is the indicator — no separate wrapper.
  const speakingIndicatorEl = stopSpeakingBtn;
  const orbStatusEl = document.getElementById("orb-status");
  const startBtn = document.getElementById("start-btn");

  const STATUS_PRESTART  = "Click Start to meet Julia.";
  // Empty idle so the status pill collapses (CSS :empty rule). The orb
  // itself is the idle indicator; no need to repeat in text.
  const STATUS_IDLE      = "";
  const STATUS_LISTENING = "Listening";
  const STATUS_THINKING  = "Julia is thinking";
  const STATUS_SPEAKING  = "Julia is speaking";
  const STATUS_ENDED     = "Conversation ended.";

  const INTRO_TEXT =
    "Hi, I'm Julia — a sexual wellness consultant. " +
    "I'm here to listen, no shame, no judgment, " +
    "grounded in trusted sources like the NHS and Women's Health Concern. " +
    "I'm not your doctor, but I can help you think things through. " +
    "What's on your mind?";

  function setStatus(text, opts = {}) {
    if (!orbStatusEl) return;
    orbStatusEl.textContent = text;
    orbStatusEl.classList.toggle("speaking", !!opts.speaking);
  }

  // Citation registry. page_id -> { idx, li, chips: Set<HTMLElement> }
  // Index is 1-based and assigned in the order each source is first cited.
  const sourceIndex = new Map();
  let sending = false;
  let currentAudio = null;
  let lastJuliaSpeech = null;  // most recent Julia utterance — clickable to replay

  // Web Audio analyser — drives the orb's amplitude reaction to speech.
  let audioCtx = null;
  let analyser = null;
  let analyserData = null;
  let vizRaf = null;

  function ensureAudioCtx() {
    if (audioCtx) return true;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return false;
    try {
      audioCtx = new Ctx();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 128;
      analyser.smoothingTimeConstant = 0.72;
      analyser.connect(audioCtx.destination);
      analyserData = new Uint8Array(analyser.frequencyBinCount);
      return true;
    } catch (_) {
      audioCtx = null;
      return false;
    }
  }

  // Set --orb-amp on the orb-stage parent so both .voice-orb and the sibling
  // .ambient-glow can read the same amplitude variable from one source.
  const orbStageEl = stopSpeakingBtn ? stopSpeakingBtn.closest(".orb-stage") : null;
  function setOrbAmp(v) {
    if (orbStageEl) orbStageEl.style.setProperty("--orb-amp", v);
    if (stopSpeakingBtn) stopSpeakingBtn.style.setProperty("--orb-amp", v);
  }
  function clearOrbAmp() {
    if (orbStageEl) orbStageEl.style.removeProperty("--orb-amp");
    if (stopSpeakingBtn) stopSpeakingBtn.style.removeProperty("--orb-amp");
  }

  function startViz() {
    if (!analyser || !stopSpeakingBtn) return;
    cancelAnimationFrame(vizRaf);
    const tick = () => {
      if (!currentAudio) {
        clearOrbAmp();
        vizRaf = null;
        return;
      }
      analyser.getByteFrequencyData(analyserData);
      let sum = 0;
      for (let i = 0; i < analyserData.length; i++) sum += analyserData[i];
      const avg = sum / analyserData.length / 255;
      // Voice typically sits in 0.04–0.22 range; expand to a clearer 0..1.
      const amp = Math.min(1, Math.max(0, (avg - 0.04) * 5.5));
      setOrbAmp(amp.toFixed(3));
      vizRaf = requestAnimationFrame(tick);
    };
    tick();
  }

  function stopViz() {
    cancelAnimationFrame(vizRaf);
    vizRaf = null;
    clearOrbAmp();
  }

  let mediaRecorder = null;
  let recordedChunks = [];
  let recording = false;
  let micStream = null;
  // When true, the next "stop" event from MediaRecorder should be
  // discarded (no transcription, no send). Set by exitVoiceMode and any
  // other path that abandons the in-flight recording.
  let discardNextRecording = false;

  // ─── Voice-mode state machine ───────────────────────────────────────
  // Voice mode is the hands-free conversational mode: click mic once →
  // mic stays open across many turns → speak → silence triggers send →
  // Julia responds → mic re-arms automatically → repeat. Exits on second
  // mic click, on textarea typing, on conversation-end, or on errors.
  //
  // States flow:    IDLE → ARMED → RECORDING → TRANSCRIBING → THINKING
  //                  ↑                                            ↓
  //                  └──── (textarea/error/ended) ──── SPEAKING ──┘
  const VM = {
    IDLE:         "idle",
    ARMED:        "armed",         // mic stream open, VAD waiting for speech
    RECORDING:    "recording",     // VAD captured speech, recording in progress
    TRANSCRIBING: "transcribing",  // sent to Whisper, waiting for text
    THINKING:     "thinking",      // turn submitted, mic muted, Claude wait
    SPEAKING:     "speaking",      // Julia TTS playing, mic muted
  };
  let voiceMode = VM.IDLE;
  // Tracks whether the user is currently in voice mode at all (any
  // non-IDLE state). Lets exit-triggers bail cleanly without case
  // analysis on the specific sub-state.
  function inVoiceMode() { return voiceMode !== VM.IDLE; }
  function setVoiceMode(next) {
    if (voiceMode === next) return;
    voiceMode = next;
    if (micBtn) {
      micBtn.classList.toggle("voice-armed",   next === VM.ARMED);
      micBtn.classList.toggle("voice-muted",   next === VM.THINKING || next === VM.SPEAKING);
      micBtn.classList.toggle("voice-active",  next !== VM.IDLE);
    }
  }

  // ─── Live mic waveform + voice-activity detection ───────────────────
  // Driven by a Web Audio analyser tap on the user's mic stream while
  // recording. Sets --mic-amp (0..1) on the mic button per frame; CSS
  // bars read it. ALSO does silence detection — once the user has spoken
  // and then goes quiet for SILENCE_HOLD_MS, we auto-stop recording so
  // the user doesn't have to click mic a second time.
  //
  // Tunables: stage venues can be noisy. SPEECH_THRESHOLD is "this is
  // someone talking, not just AC hum"; SILENCE_THRESHOLD is "they've
  // stopped". Hold times are conservative so a thinking-pause doesn't
  // trigger a false stop. NO_SPEECH_TIMEOUT_MS is a recovery for
  // accidental mic-clicks where nothing's said at all.
  const SPEECH_THRESHOLD              = 0.16;    // amp peak above this = "they spoke"
  const SILENCE_THRESHOLD             = 0.08;    // amp below this for the hold = silent
  const SILENCE_HOLD_MS               = 1500;    // 1.5s of silence after speech → stop
  // Require sustained speech across multiple frames before we trust it.
  // Ambient noise (people moving, clothes rustling) makes brief amp
  // spikes — without this, those spikes register as a "turn" and lead
  // to empty transcriptions and false errors.
  const MIN_SPEECH_FRAMES             = 8;       // ~130ms of continuous speech
  // Whisper trained on YouTube/subtitle data hallucinates a small set
  // of standard phrases when given near-silent audio. Reject these so
  // they never become "user turns" mid-conversation. Match is on the
  // lowercased + punctuation-stripped text.
  const WHISPER_HALLUCINATIONS = new Set([
    "thank you",
    "thanks for watching",
    "thanks",
    "bye",
    "goodbye",
    "see you",
    "see you next time",
    "subtitles by",
    "subtitles",
    "you",
    "yeah",
    "uh",
    "um",
    "hmm",
    "thank you for watching",
    "thank you so much",
    "i'll see you next time",
    "thank you for watching this video",
    "please subscribe",
    "subscribe",
  ]);
  // No-speech timeouts differ by context:
  //   - Voice mode: user is mid-conversation, may pause to think or to
  //     explain something to people in the room (stage demo). 60s gives
  //     real breathing room without the mic abruptly closing.
  //   - One-shot click: user clicked mic and didn't speak — likely
  //     accidental. Close fast so the recorder doesn't hang.
  const NO_SPEECH_TIMEOUT_VOICE_MS    = 60000;   // 60s in voice mode
  const NO_SPEECH_TIMEOUT_ONESHOT_MS  = 8000;    // 8s in one-shot mode

  let micAudioCtx = null;
  let micSource = null;
  let micAnalyser = null;
  let micVizRaf = 0;
  function startMicViz(stream) {
    if (!micBtn || !stream) return;
    let hasSpoken = false;
    let silenceStart = 0;
    let speechFrames = 0;   // count of consecutive frames above SPEECH_THRESHOLD
    const startedAt = performance.now();
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      micAudioCtx = new Ctx();
      micSource = micAudioCtx.createMediaStreamSource(stream);
      micAnalyser = micAudioCtx.createAnalyser();
      micAnalyser.fftSize = 256;
      micSource.connect(micAnalyser);
      const data = new Uint8Array(micAnalyser.frequencyBinCount);
      const tick = () => {
        if (!micAnalyser || !recording) return;
        micAnalyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const avg = sum / data.length / 255;
        // Speech sits around 0.04..0.20 — expand to a clearer 0..1.
        const amp = Math.min(1, Math.max(0, (avg - 0.03) * 6.0));
        micBtn.style.setProperty("--mic-amp", amp.toFixed(3));

        const now = performance.now();
        // Did they speak this frame? Require N consecutive frames above
        // the threshold so a single noise spike doesn't count as speech.
        if (amp > SPEECH_THRESHOLD) {
          speechFrames++;
          if (speechFrames >= MIN_SPEECH_FRAMES) {
            hasSpoken = true;
            silenceStart = 0;
          }
        } else if (amp < SILENCE_THRESHOLD) {
          speechFrames = 0;
          // Silent. Start counting silence only if they've spoken at least once.
          if (hasSpoken) {
            if (silenceStart === 0) silenceStart = now;
            else if (now - silenceStart >= SILENCE_HOLD_MS) {
              // 1.5s of silence after speech → auto-stop and submit.
              // 1.5s of silence after speech → end this turn. In voice
              // mode this triggers transcribe + send + auto-rearm; in
              // one-shot mode it exits cleanly.
              if (inVoiceMode()) stopTurn(); else exitVoiceMode();
              return;
            }
          } else {
            // Pick the right no-speech timeout for the current context.
            // Re-evaluated each frame because the user may have entered
            // voice mode after recording started.
            const noSpeechTimeout = inVoiceMode()
              ? NO_SPEECH_TIMEOUT_VOICE_MS
              : NO_SPEECH_TIMEOUT_ONESHOT_MS;
            if (now - startedAt >= noSpeechTimeout) {
              // No speech detected within the budget. Exit cleanly.
              exitVoiceMode();
              return;
            }
          }
        }
        // Mid-range amp (between thresholds) is "ambient noise" — don't
        // count toward silence, don't reset speech flag. Holds steady.

        micVizRaf = requestAnimationFrame(tick);
      };
      tick();
    } catch (err) {
      // If the analyser fails (e.g. browser audio context restrictions),
      // fall back to the CSS idle pulse + manual mic-click stop.
      console.warn("mic viz unavailable", err);
    }
  }
  function stopMicViz() {
    cancelAnimationFrame(micVizRaf);
    micVizRaf = 0;
    if (micBtn) micBtn.style.removeProperty("--mic-amp");
    try { if (micSource) micSource.disconnect(); } catch (_) {}
    try { if (micAnalyser) micAnalyser.disconnect(); } catch (_) {}
    try { if (micAudioCtx) micAudioCtx.close(); } catch (_) {}
    micSource = null;
    micAnalyser = null;
    micAudioCtx = null;
  }

  // SVG sparkle (same shape as the header AI badge) inserted before the
  // text in Julia bubbles. Subtle marker that this is an AI response,
  // visually echoing the AI badge in the topbar.
  const AI_GLYPH_SVG =
    `<svg class="msg-ai-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="M12 2v4"/><path d="M12 18v4"/>` +
    `<path d="m4.93 4.93 2.83 2.83"/><path d="m16.24 16.24 2.83 2.83"/>` +
    `<path d="M2 12h4"/><path d="M18 12h4"/>` +
    `<path d="m4.93 19.07 2.83-2.83"/><path d="m16.24 7.76 2.83-2.83"/>` +
    `</svg>`;

  function appendMessage(role, text, opts = {}) {
    const el = document.createElement("div");
    el.className = `msg ${role}` + (opts.ended ? " ended" : "");
    if (role === "julia") {
      el.insertAdjacentHTML("afterbegin", AI_GLYPH_SVG);
    }
    const textNode = document.createElement("span");
    textNode.className = "msg-text";
    textNode.textContent = text;
    el.appendChild(textNode);
    chatEl.appendChild(el);
    chatEl.scrollTop = chatEl.scrollHeight;
    return el;
  }

  function appendThinking() {
    const el = document.createElement("div");
    el.className = "msg julia thinking";
    el.setAttribute("aria-label", "Julia is thinking");
    for (let i = 0; i < 3; i++) {
      const dot = document.createElement("span");
      dot.className = "dot";
      el.appendChild(dot);
    }
    chatEl.appendChild(el);
    chatEl.scrollTop = chatEl.scrollHeight;
    return el;
  }

  function showError(msg) {
    const el = document.createElement("div");
    el.className = "error-banner";
    el.textContent = msg;
    chatEl.appendChild(el);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  function renderSources(sources) {
    if (!Array.isArray(sources) || sources.length === 0) return;
    let added = false;
    for (const s of sources) {
      if (!s || !s.page_id || sourceIndex.has(s.page_id)) continue;
      const idx = sourceIndex.size + 1;
      const li = document.createElement("li");
      li.dataset.pageId = s.page_id;
      li.dataset.citeIdx = String(idx);

      const num = document.createElement("span");
      num.className = "ref-num";
      num.textContent = String(idx);

      const a = document.createElement("a");
      a.href = s.url || "#";
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = s.title || s.page_id;

      li.appendChild(num);
      li.appendChild(a);
      refListEl.appendChild(li);
      sourceIndex.set(s.page_id, { idx, li, chips: new Set() });
      added = true;
    }
    if (added && refEmptyEl) refEmptyEl.style.display = "none";
  }

  function appendCiteChips(bubbleEl, sources) {
    if (!bubbleEl || !Array.isArray(sources) || sources.length === 0) return;
    const row = document.createElement("span");
    row.className = "cite-row";
    let appended = 0;
    for (const s of sources) {
      if (!s || !s.page_id) continue;
      const meta = sourceIndex.get(s.page_id);
      if (!meta) continue;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cite-chip";
      chip.dataset.pageId = s.page_id;
      chip.textContent = String(meta.idx);
      chip.title = s.title || s.page_id;
      chip.setAttribute("aria-label", `Source ${meta.idx}: ${s.title || s.page_id}`);
      row.appendChild(chip);
      meta.chips.add(chip);
      appended++;
    }
    if (appended > 0) bubbleEl.appendChild(row);
  }

  function pulseReference(pageId) {
    const meta = sourceIndex.get(pageId);
    if (!meta) return;
    meta.li.classList.remove("pulse");
    // Force reflow so the animation restarts on repeat clicks.
    void meta.li.offsetWidth;
    meta.li.classList.add("pulse");
    meta.li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setTimeout(() => meta.li.classList.remove("pulse"), 1500);
  }

  // One delegated listener for all chip clicks.
  if (chatEl) {
    chatEl.addEventListener("click", (e) => {
      const chip = e.target.closest(".cite-chip");
      if (!chip) return;
      pulseReference(chip.dataset.pageId);
    });
  }

  // Hover a reference item → highlight its chips in the transcript.
  if (refListEl) {
    refListEl.addEventListener("mouseover", (e) => {
      const li = e.target.closest("li[data-page-id]");
      if (!li) return;
      const meta = sourceIndex.get(li.dataset.pageId);
      if (!meta) return;
      for (const chip of meta.chips) chip.classList.add("highlighted");
    });
    refListEl.addEventListener("mouseout", (e) => {
      const li = e.target.closest("li[data-page-id]");
      if (!li) return;
      const meta = sourceIndex.get(li.dataset.pageId);
      if (!meta) return;
      for (const chip of meta.chips) chip.classList.remove("highlighted");
    });
  }

  function renderProduct(product) {
    if (!product) return;
    productRegionEl.innerHTML = "";
    const card = document.createElement("div");
    card.className = "product-card";
    if (product.image_url) card.classList.add("with-image");

    // Left column — product image (if available)
    let imageWrap = null;
    if (product.image_url) {
      imageWrap = document.createElement("div");
      imageWrap.className = "pc-image";
      const img = document.createElement("img");
      img.src = product.image_url;
      img.alt = product.name || "";
      img.loading = "lazy";
      img.onerror = () => { imageWrap.remove(); card.classList.remove("with-image"); };
      imageWrap.appendChild(img);
    }

    // Right column — text content
    const body = document.createElement("div");
    body.className = "pc-body";

    const label = document.createElement("div");
    label.className = "pc-label";
    label.textContent = "Julia suggests";

    const name = document.createElement("p");
    name.className = "pc-name";
    name.textContent = product.name || product.id;

    const price = document.createElement("div");
    price.className = "pc-price";
    price.textContent = product.price || "";

    const why = document.createElement("p");
    why.className = "pc-why";
    why.textContent = product.why_this_one || "";

    const link = document.createElement("a");
    link.className = "pc-link";
    link.href = product.pdp_url || "#";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "View product →";

    body.append(label, name);
    if (product.price) body.append(price);
    body.append(why, link);

    if (imageWrap) card.append(imageWrap);
    card.append(body);
    productRegionEl.appendChild(card);
  }

  function renderPractitioner(practitioner) {
    if (!practitioner || !practitionerRegionEl) return;
    practitionerRegionEl.innerHTML = "";
    const card = document.createElement("div");
    card.className = "practitioner-card";

    const body = document.createElement("div");
    body.className = "pc-body";

    const label = document.createElement("div");
    label.className = "pc-label";
    label.textContent = "Julia suggests speaking to";

    const name = document.createElement("p");
    name.className = "pc-name";
    name.textContent = practitioner.name || practitioner.id;

    if (practitioner.title) {
      const title = document.createElement("p");
      title.className = "pc-title";
      title.textContent = practitioner.title;
      body.append(label, name, title);
    } else {
      body.append(label, name);
    }

    const meta = document.createElement("div");
    meta.className = "pc-meta";
    if (practitioner.city) {
      const cityEl = document.createElement("span");
      cityEl.className = "pc-city";
      cityEl.textContent = practitioner.city;
      meta.appendChild(cityEl);
    }
    if (practitioner.online_available) {
      const badge = document.createElement("span");
      badge.className = "pc-badge";
      badge.textContent = "Online available";
      meta.appendChild(badge);
    }
    if (practitioner.in_person_available) {
      const badge = document.createElement("span");
      badge.className = "pc-badge pc-badge-soft";
      badge.textContent = "In-person";
      meta.appendChild(badge);
    }
    if (meta.children.length) body.append(meta);

    if (practitioner.why_this_one) {
      const why = document.createElement("p");
      why.className = "pc-why";
      why.textContent = practitioner.why_this_one;
      body.append(why);
    }

    if (practitioner.website) {
      const link = document.createElement("a");
      link.className = "pc-link";
      link.href = practitioner.website;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Visit website →";
      body.append(link);
    }

    const note = document.createElement("p");
    note.className = "pc-disclaimer";
    note.textContent = "Demo directory — not a real listing.";
    body.append(note);

    card.append(body);
    practitionerRegionEl.appendChild(card);
  }

  function showSpeakingIndicator() {
    if (speakingIndicatorEl) {
      speakingIndicatorEl.classList.remove("thinking");
      speakingIndicatorEl.classList.add("visible");
    }
    setStatus(STATUS_SPEAKING, { speaking: true });
  }

  function hideSpeakingIndicator() {
    if (speakingIndicatorEl) speakingIndicatorEl.classList.remove("visible");
    if (orbStatusEl && orbStatusEl.textContent === STATUS_SPEAKING) {
      setStatus(STATUS_IDLE);
    }
  }

  // Distinct "Julia is thinking" orb state during the Claude wait. Removed
  // automatically when speaking starts, or explicitly on error.
  function showThinkingIndicator() {
    if (speakingIndicatorEl) speakingIndicatorEl.classList.add("thinking");
  }
  function hideThinkingIndicator() {
    if (speakingIndicatorEl) speakingIndicatorEl.classList.remove("thinking");
  }

  // Set inside playTts; lets stopSpeaking()/replacement resolve the
  // current speech-done promise without depending on the spurious
  // `pause` event (which can fire for buffering / browser interruptions).
  let pendingSpeechResolver = null;

  function stopSpeaking() {
    if (currentAudio) {
      try { currentAudio.pause(); } catch (_) {}
      currentAudio = null;
    }
    stopViz();
    hideSpeakingIndicator();
    if (pendingSpeechResolver) {
      pendingSpeechResolver();
      pendingSpeechResolver = null;
    }
  }

  // Returns a Promise that resolves when this audio TRULY finishes playing
  // (ended, errored, or user-stopped via stopSpeaking). Does NOT resolve on
  // the `pause` event, which can fire during buffering / browser
  // interruptions and was prematurely revealing the product card.
  function playTts(text) {
    return new Promise((resolve) => {
      if (!text || !text.trim()) { resolve(); return; }

      // A new speech is replacing any in-flight one — release that promise
      // before we overwrite the resolver slot.
      if (pendingSpeechResolver) {
        pendingSpeechResolver();
        pendingSpeechResolver = null;
      }

      lastJuliaSpeech = text;
      if (stopSpeakingBtn) stopSpeakingBtn.classList.add("replayable");
      if (currentAudio) {
        try { currentAudio.pause(); } catch (_) {}
        currentAudio = null;
      }
      stopViz();

      let resolved = false;
      const finish = () => {
        if (resolved) return;
        resolved = true;
        if (pendingSpeechResolver === finish) pendingSpeechResolver = null;
        resolve();
      };
      pendingSpeechResolver = finish;

      const audio = new Audio(`/api/tts?text=${encodeURIComponent(text)}`);
      audio.crossOrigin = "anonymous";
      audio.addEventListener("error", () => {
        if (audio === currentAudio) { currentAudio = null; stopViz(); hideSpeakingIndicator(); }
        finish();
      });
      audio.addEventListener("ended", () => {
        if (audio === currentAudio) { currentAudio = null; stopViz(); hideSpeakingIndicator(); }
        finish();
      });
      // `pause` no longer resolves the speech-done promise. We still update
      // the orb UI so the indicator clears when audio actually halts.
      audio.addEventListener("pause", () => {
        if (audio === currentAudio && audio.ended) {
          currentAudio = null;
          stopViz();
          hideSpeakingIndicator();
        }
      });

      // Wire this audio element through Web Audio so the analyser can read
      // amplitude per frame for the orb visualiser.
      if (ensureAudioCtx()) {
        try {
          if (audioCtx.state === "suspended") audioCtx.resume();
          const src = audioCtx.createMediaElementSource(audio);
          src.connect(analyser);
        } catch (e) {
          console.warn("[julia] visualiser disabled this turn:", e);
        }
      }

      currentAudio = audio;
      audio.play()
        .then(() => { if (audio === currentAudio) { showSpeakingIndicator(); startViz(); } })
        .catch((err) => {
          // "The play() request was interrupted by a call to pause()" is a
          // known race when a new turn starts before the previous audio's
          // play() Promise has resolved. It's harmless — the audio just got
          // superseded — so we swallow it silently. Likewise AbortError.
          const name = err && err.name;
          const msg  = (err && err.message) || "";
          const isInterrupted =
            name === "AbortError" ||
            /interrupted by a call to pause/i.test(msg) ||
            /load request was aborted/i.test(msg);
          if (!isInterrupted) {
            console.warn("[julia] audio.play() rejected:", err);
            showError(`Audio failed to play: ${msg || err}. Click the orb again.`);
          } else {
            console.debug("[julia] audio.play() superseded:", msg);
          }
          if (audio === currentAudio) {
            currentAudio = null;
            stopViz();
            hideSpeakingIndicator();
          }
          finish();
        });
    });
  }

  function setSending(state) {
    sending = state;
    inputEl.disabled = state;
    sendBtn.disabled = state;
    micBtn.disabled = state;
    sendBtn.textContent = state ? "Sending…" : "Send";
  }

  function endSession() {
    if (inVoiceMode()) exitVoiceMode();
    inputEl.disabled = true;
    sendBtn.disabled = true;
    micBtn.disabled = true;
    sendBtn.textContent = "Conversation ended";
    inputEl.placeholder = "This conversation has ended.";
    setStatus(STATUS_ENDED);
  }

  if (stopSpeakingBtn) {
    stopSpeakingBtn.addEventListener("click", () => {
      if (currentAudio) {
        stopSpeaking();
        // Option A: in voice mode, interrupting Julia means the user wants
        // the floor right now. Skip the auto-rearm grace and start a new
        // turn immediately so they can speak without re-clicking the mic.
        if (inVoiceMode() && voiceMode === VM.SPEAKING) {
          startTurn();
        }
      } else if (lastJuliaSpeech) {
        playTts(lastJuliaSpeech);
      }
    });
  }

  // Pre-start gate: composer disabled until user clicks Start.
  function applyPrestartGate() {
    inputEl.disabled = true;
    sendBtn.disabled = true;
    micBtn.disabled = true;
    setStatus(STATUS_PRESTART);
  }

  function liftPrestartGate() {
    inputEl.disabled = false;
    sendBtn.disabled = false;
    micBtn.disabled = false;
    inputEl.focus();
  }

  if (startBtn) {
    applyPrestartGate();
    startBtn.addEventListener("click", () => {
      startBtn.hidden = true;
      appendMessage("julia", INTRO_TEXT);
      liftPrestartGate();
      // Hide hint chips while the intro plays so an accidental click
      // doesn't interrupt Julia mid-introduction. Restored when the
      // intro audio finishes.
      if (hintsEl) hintsEl.classList.add("hidden");
      const introDone = playTts(INTRO_TEXT);
      introDone.then(() => {
        if (hintsEl) hintsEl.classList.remove("hidden");
      });
    });
  }

  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      stopSpeaking();
      if (inVoiceMode()) exitVoiceMode();
      location.reload();
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && currentAudio) {
      e.preventDefault();
      stopSpeaking();
    }
  });

  let activeTurnId = 0;

  async function sendTurn(userText) {
    const turnId = ++activeTurnId;
    // Hide chips while a turn is in flight; revealAfterSpeech() repopulates
    // them once Julia finishes speaking.
    if (hintsEl) hintsEl.classList.add("hidden");
    // Cards persist across turns. A new product card replaces the old
    // product card (renderProduct clears its region before appending),
    // and same for practitioners — but turns that don't produce a card
    // leave the previous one on screen so the user can still see what
    // Julia recommended earlier.

    appendMessage("user", userText);
    const thinkingEl = appendThinking();
    setSending(true);
    setStatus(STATUS_THINKING);
    showThinkingIndicator();
    try {
      const resp = await fetch("/api/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, user_text: userText, channel: "web" }),
      });
      thinkingEl.remove();
      if (!resp.ok) {
        hideThinkingIndicator();
        showError(`Julia hit an error (HTTP ${resp.status}). Please try again.`);
        setSending(false);
        return;
      }
      const data = await resp.json();

      // Step 1 — bubble + citation chips + side-panel sources land immediately
      // so the user can read along while Julia speaks.
      const juliaBubble = appendMessage("julia", data.speech || "", { ended: !!data.ended });
      renderSources(data.sources_used);
      appendCiteChips(juliaBubble, data.sources_used);

      // Step 2 — composer becomes available right away. User isn't held
      // hostage by the playback animation.
      if (data.ended) {
        endSession();
        // ended:true is a hard-close (e.g. under-18 → Brook). Voice mode
        // exits cleanly so the user isn't left with an open mic.
        if (inVoiceMode()) exitVoiceMode();
      } else {
        setSending(false);
        if (!inVoiceMode()) inputEl.focus();
      }

      // Step 3 — kick off TTS. playTts returns a Promise that resolves
      // when audio is done (ended / paused / errored / replaced).
      const speechDone = playTts(data.speech || "");
      if (inVoiceMode() && !data.ended) setVoiceMode(VM.SPEAKING);

      // Step 4 — schedule the staggered reveal: product / practitioner card
      // first, then suggested-replies, paced to the speech. Pass the speech
      // length so revealAfterSpeech can size its safety timeout to match.
      const wordCount = (data.speech || "").trim().split(/\s+/).filter(Boolean).length;
      revealAfterSpeech(turnId, speechDone, data.product_suggestion,
                        data.practitioner_suggestion,
                        data.suggested_replies, wordCount);

      // Step 5 — voice mode auto-rearm. When Julia stops talking, drop
      // the speech-mute and start the next turn so the user can keep
      // the conversation going hands-free. ~250ms grace lets the TTS
      // tail finish without the analyser catching it.
      speechDone.then(() => {
        if (!inVoiceMode()) return;          // user exited mid-speech
        if (data.ended) return;              // conversation closed
        if (turnId !== activeTurnId) return; // a newer turn has started
        setTimeout(() => {
          if (!inVoiceMode()) return;
          if (turnId !== activeTurnId) return;
          startTurn();
        }, 250);
      });
    } catch (err) {
      thinkingEl.remove();
      hideThinkingIndicator();
      showError(`Network error reaching Julia: ${err.message || err}`);
      setSending(false);
    }
  }

  // Reveal sequence: wait for Julia to finish speaking (or a hard timeout),
  // then drop the product card, then the suggested-reply chips. Skipped if
  // a newer turn has started. The safety timeout is sized to the speech
  // length so chips don't appear mid-utterance for long responses (TTS
  // at ~150 wpm = 0.4s per word; we add 8s headroom + 5s floor).
  function revealAfterSpeech(turnId, speechPromise, productSuggestion, practitionerSuggestion, suggestedReplies, wordCount = 80) {
    const expectedSpeechMs = Math.max(0, wordCount) * 400;
    const HARD_MAX_MS = Math.max(15000, expectedSpeechMs + 8000);
    const timeout = new Promise((res) => setTimeout(res, HARD_MAX_MS));

    Promise.race([speechPromise, timeout]).then(() => {
      if (turnId !== activeTurnId) return; // a newer turn has superseded
      const hasCard = !!(productSuggestion || practitionerSuggestion);
      if (productSuggestion) renderProduct(productSuggestion);
      if (practitionerSuggestion) renderPractitioner(practitionerSuggestion);
      if (hasCard) {
        // Small breath between card landing and chips appearing.
        setTimeout(() => {
          if (turnId !== activeTurnId) return;
          renderSuggestedReplies(suggestedReplies);
        }, 600);
      } else {
        renderSuggestedReplies(suggestedReplies);
      }
    });
  }

  formEl.addEventListener("submit", (e) => {
    e.preventDefault();
    if (sending) return;
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = "";
    sendTurn(text);
  });

  inputEl.addEventListener("keydown", (e) => {
    // Typing in the composer means the user wants to switch from voice
    // back to typing. Release the mic immediately. Modifier-only keys
    // (Shift, Cmd, etc.) don't count — those don't insert text.
    const isContentKey =
      e.key.length === 1 ||                           // printable char
      e.key === "Backspace" || e.key === "Delete" ||  // editing
      e.key === "Enter";                              // submit
    if (isContentKey && inVoiceMode()) {
      exitVoiceMode();
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      formEl.requestSubmit();
    }
  });

  // Pasting / programmatic input also signals "user wants to type" — exit
  // voice mode so the mic doesn't keep running while they edit.
  inputEl.addEventListener("paste", () => {
    if (inVoiceMode()) exitVoiceMode();
  });

  // Click a suggested-prompt chip → fill the textarea and auto-send.
  if (hintsEl) {
    hintsEl.addEventListener("click", (e) => {
      const chip = e.target.closest(".hint-chip");
      if (!chip || sending) return;
      if (inputEl.disabled) return; // pre-start gate still active
      inputEl.value = chip.textContent.trim();
      formEl.requestSubmit();
    });
  }

  // Replace the hint chips with Julia's per-turn suggested follow-up replies.
  // Empty list (e.g., final turn / hard refusal) → hide the chip row entirely.
  function renderSuggestedReplies(replies) {
    if (!hintsEl) return;
    hintsEl.innerHTML = "";
    if (!Array.isArray(replies) || replies.length === 0) {
      hintsEl.classList.add("hidden");
      return;
    }
    for (const r of replies) {
      if (typeof r !== "string") continue;
      const text = r.trim();
      if (!text) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hint-chip suggested";
      btn.textContent = text;
      hintsEl.appendChild(btn);
    }
    hintsEl.classList.remove("hidden");
  }

  function pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    for (const t of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  // Enter voice mode — acquires mic stream, starts the first turn cycle.
  // The stream stays open until exitVoiceMode is called, so subsequent
  // turns reuse the same permission grant and analyser context.
  async function enterVoiceMode() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      showError("Voice input not supported in this browser.");
      return;
    }
    // If Julia is still talking on the way in, silence her — the user
    // wants the floor.
    if (currentAudio) stopSpeaking();
    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      showError(`Mic permission denied: ${err.message || err}`);
      return;
    }
    micBtn.setAttribute("aria-label", "Exit voice mode");
    micBtn.title = "Click to exit voice mode (or type to release)";
    setVoiceMode(VM.ARMED);
    startTurn();
  }

  // Exit voice mode — releases stream, closes audio context, returns
  // to IDLE. Called on second mic click, textarea focus/typing, or
  // ended:true response. Mark the in-flight recording as abandoned so
  // the upcoming MediaRecorder "stop" event doesn't transcribe whatever
  // (likely empty / Whisper-hallucinated) audio was captured.
  function exitVoiceMode() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      discardNextRecording = true;
      try { mediaRecorder.stop(); } catch (_) {}
    }
    stopMicViz();
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      micStream = null;
    }
    recording = false;
    setVoiceMode(VM.IDLE);
    micBtn.classList.remove("recording");
    micBtn.setAttribute("aria-label", "Click to start voice mode");
    micBtn.title = "Click to start voice mode";
    if (orbStatusEl && orbStatusEl.textContent === STATUS_LISTENING) {
      setStatus(STATUS_IDLE);
    }
  }

  // Begin a new RECORDING turn on the already-acquired stream.
  // Creates a fresh MediaRecorder per turn (chunks reset).
  function startTurn() {
    if (!micStream || !inVoiceMode()) return;
    // Re-enable the mic track in case it was muted during Julia's last turn.
    micStream.getTracks().forEach((t) => { t.enabled = true; });
    const mimeType = pickMimeType();
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(micStream, mimeType ? { mimeType } : undefined);
    mediaRecorder.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size > 0) recordedChunks.push(e.data);
    });
    mediaRecorder.addEventListener("stop", onRecordingStop);
    mediaRecorder.start();
    recording = true;
    micBtn.classList.add("recording");
    setVoiceMode(VM.ARMED);
    setStatus(STATUS_LISTENING);
    startMicViz(micStream);
  }

  // Stop the current MediaRecorder (triggers onRecordingStop). Does NOT
  // release the stream — voice mode stays active across turns.
  function stopTurn() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
    stopMicViz();
    recording = false;
    micBtn.classList.remove("recording");
  }

  async function onRecordingStop() {
    // If the recording was abandoned (user explicitly exited voice mode,
    // typed in the composer, refresh, etc.), discard without transcribing.
    // Prevents Whisper-hallucinated phrases from being auto-sent as turns.
    if (discardNextRecording) {
      discardNextRecording = false;
      recordedChunks = [];
      return;
    }
    const type = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
    const blob = new Blob(recordedChunks, { type });
    recordedChunks = [];
    if (blob.size === 0) {
      // Empty capture — re-arm if still in voice mode so the user can try
      // again without re-clicking the mic.
      if (inVoiceMode()) startTurn();
      return;
    }
    const ext = type.includes("mp4") ? "mp4" : type.includes("ogg") ? "ogg" : "webm";
    const fd = new FormData();
    fd.append("audio", blob, `speech.${ext}`);

    if (inVoiceMode()) setVoiceMode(VM.TRANSCRIBING);
    micBtn.disabled = true;
    const prev = inputEl.placeholder;
    inputEl.placeholder = "Transcribing…";
    try {
      const resp = await fetch("/api/stt", { method: "POST", body: fd });
      if (!resp.ok) {
        showError(`Transcription failed (HTTP ${resp.status}).`);
        if (inVoiceMode()) startTurn();
        return;
      }
      const data = await resp.json();
      const text = (data.text || "").trim();
      // Whisper hallucinates a small set of phrases on near-silence audio
      // (subtitle / YouTube training-data artefacts). Reject anything
      // too short OR matching the known hallucination set so we don't
      // auto-send "Thank you." / "Bye!" as a user turn.
      const tooShort = text.length < 4;
      const isHallucination = WHISPER_HALLUCINATIONS.has(
        text.toLowerCase().replace(/[.!?,]/g, "").trim()
      );
      if (!text || tooShort || isHallucination) {
        // In voice mode, the user is mid-conversation — don't pile up
        // visible "didn't catch that" errors when ambient noise triggers
        // false captures. Silently re-arm and let them speak again.
        if (!inVoiceMode()) {
          showError("Didn't catch that — try again?");
        }
        if (inVoiceMode()) startTurn();
        return;
      }
      const existing = inputEl.value.trim();
      const finalText = existing ? `${existing} ${text}` : text;
      inputEl.value = finalText;
      inputEl.placeholder = prev;
      micBtn.disabled = sending;

      // Voice-first auto-send. Two cases:
      //   (a) voice mode is active → we always auto-send and DON'T re-arm
      //       here — the post-turn cleanup will re-arm after Julia speaks.
      //   (b) voice mode inactive (one-shot mic click) → auto-send only
      //       if user hadn't pre-typed; otherwise leave for manual Send.
      if (inVoiceMode()) {
        inputEl.value = "";
        // Mute the mic for the duration of THINKING + SPEAKING so Julia's
        // TTS doesn't feed back into the analyser.
        muteMicForJulia();
        setVoiceMode(VM.THINKING);
        sendTurn(finalText);
      } else if (!existing) {
        inputEl.value = "";
        sendTurn(finalText);
      } else {
        inputEl.focus();
      }
      return;
    } catch (err) {
      showError(`Network error during transcription: ${err.message || err}`);
      if (inVoiceMode()) startTurn();
    } finally {
      inputEl.placeholder = prev;
      micBtn.disabled = sending;
    }
  }

  // Disable the mic input track while Julia is generating + speaking, so
  // her TTS can't feed back into the user's analyser. Re-enabled when
  // we re-arm for the next turn.
  function muteMicForJulia() {
    if (!micStream) return;
    micStream.getTracks().forEach((t) => { t.enabled = false; });
  }

  // Mic button is now a voice-mode TOGGLE. First click enters voice mode
  // and starts listening; second click exits voice mode entirely.
  // Mid-state (RECORDING etc.) clicks also exit — user wants out.
  micBtn.addEventListener("click", () => {
    if (sending && !inVoiceMode()) return;
    if (inVoiceMode()) {
      exitVoiceMode();
    } else {
      enterVoiceMode();
    }
  });

  inputEl.focus();
})();
