"""ElevenLabs TTS wrapper.

stream_tts: async iterator over MP3 chunks via the ElevenLabs streaming
HTTP endpoint (chunked transfer). The browser <audio> element can play
chunked MP3 as it arrives, so this gives sentence-level perceived latency
without needing a WebSocket.

oneshot_tts: single REST call returning the full MP3 bytes. Used as a
fallback if the streaming response fails.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import httpx

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_turbo_v2_5"
OUTPUT_FORMAT = "mp3_44100_128"

# Voice settings tuned for Julia — warm, caring, grounded.
# stability=0.55 calms the delivery (less peaky, more even tempo);
# similarity_boost=0.88 locks closer to the source voice character;
# style=0.18 gently amplifies the voice's natural inflection so it
#   reads as engaged rather than read-aloud-flat.
VOICE_SETTINGS = {
    "stability": 0.55,
    "similarity_boost": 0.88,
    "style": 0.18,
    "use_speaker_boost": True,
}


def _voice_id() -> str:
    vid = os.getenv("ELEVENLABS_VOICE_ID")
    if not vid:
        raise RuntimeError("ELEVENLABS_VOICE_ID not set")
    return vid


def _api_key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    return key


def _payload(text: str) -> dict:
    return {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }


def _headers() -> dict:
    return {
        "xi-api-key": _api_key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }


async def stream_tts(text: str) -> AsyncIterator[bytes]:
    """Yield MP3 chunks from the ElevenLabs streaming endpoint."""
    url = f"{ELEVEN_BASE}/text-to-speech/{_voice_id()}/stream"
    params = {"output_format": OUTPUT_FORMAT}
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST", url, params=params, headers=_headers(), json=_payload(text)
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk


async def oneshot_tts(text: str) -> bytes:
    """Single-shot fallback: full MP3 bytes via the non-streaming endpoint."""
    url = f"{ELEVEN_BASE}/text-to-speech/{_voice_id()}"
    params = {"output_format": OUTPUT_FORMAT}
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, params=params, headers=_headers(), json=_payload(text))
        resp.raise_for_status()
        return resp.content
