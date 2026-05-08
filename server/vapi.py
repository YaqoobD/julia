"""Vapi webhook adapter — phone channel.

Vapi's `custom-llm` provider POSTs OpenAI-compatible ChatCompletions requests
to /vapi/webhook. We pull the latest user utterance, run the same conversation
engine the web channel uses, and stream the assistant's `speech` back as a
single OpenAI chat.completion.chunk delta. Vapi's TTS pipeline reads
`choices[0].delta.content`.

`sources_used` and `product_suggestion` are discarded — phone is a voice-only
channel per architecture §4.2.

Session continuity: we key the julia session on Vapi's `call.id` (the call-leg
identifier) so a single phone call accumulates history through the in-process
engine state, while different calls stay isolated.

Discrepancy note: impl-plan §10 / tech-stack §8 describe the wire shape as
`POST {message: {role, content}}` → `{response: <speech>}`. Vapi's actual
`custom-llm` provider speaks OpenAI ChatCompletions. This adapter implements
the latter.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server import julia

router = APIRouter()


def _last_user_text(messages: list[Any]) -> str | None:
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            joined = " ".join(parts).strip()
            if joined:
                return joined
    return None


def _session_id_from(body: dict[str, Any]) -> str:
    call = body.get("call")
    if isinstance(call, dict):
        cid = call.get("id")
        if isinstance(cid, str) and cid:
            return f"vapi:{cid}"
    return f"vapi:anon:{uuid.uuid4()}"


async def _stream_chat_completion(speech: str) -> AsyncGenerator[bytes, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
        event = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "julia",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(event)}\n\n".encode("utf-8")

    yield chunk({"role": "assistant"})
    if speech:
        yield chunk({"content": speech})
    yield chunk({}, finish_reason="stop")
    yield b"data: [DONE]\n\n"


@router.post("/vapi/webhook")
@router.post("/vapi/webhook/chat/completions")
async def vapi_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "expected JSON object"}, status_code=400)

    messages = body.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": "expected `messages` array"}, status_code=400)

    user_text = _last_user_text(messages)
    if not user_text:
        return JSONResponse({"error": "no user message in payload"}, status_code=400)

    session_id = _session_id_from(body)
    result = await julia.handle_turn(
        session_id=session_id, user_text=user_text, channel="phone"
    )
    speech = result.speech or ""

    return StreamingResponse(
        _stream_chat_completion(speech),
        media_type="text/event-stream",
    )
