"""OpenAI Whisper STT wrapper.

transcribe(audio, mime, filename) -> str. Single REST call to whisper-1.
Audio comes in from the browser as webm/opus; the OpenAI SDK accepts it
as a multipart upload tuple.
"""

from __future__ import annotations

import os

from openai import AsyncOpenAI

WHISPER_MODEL = "whisper-1"

_CLIENT: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip().strip('"')
        if not api_key or api_key.upper().startswith("PLACEHOLDER"):
            raise RuntimeError("OPENAI_API_KEY missing or placeholder.")
        _CLIENT = AsyncOpenAI(api_key=api_key)
    return _CLIENT


async def transcribe(audio: bytes, mime: str = "audio/webm", filename: str = "speech.webm") -> str:
    resp = await _client().audio.transcriptions.create(
        model=WHISPER_MODEL,
        file=(filename, audio, mime),
        language="en",
    )
    return (resp.text or "").strip()
