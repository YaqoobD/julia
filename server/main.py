"""Julia FastAPI server."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"

load_dotenv(PROJECT_ROOT / ".env")

from server import julia, retrieval, stt, tools, tts  # noqa: E402  (load_dotenv must run first)
from server.vapi import router as vapi_router  # noqa: E402

app = FastAPI(title="Julia", version="0.1.0")
app.include_router(vapi_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class TurnRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    user_text: str = Field(..., min_length=1)
    channel: str = "web"


class SourceOut(BaseModel):
    page_id: str
    title: str
    url: str


class ProductSuggestionOut(BaseModel):
    id: str
    name: str
    price: str
    why_this_one: str
    pdp_url: str
    image_url: str = ""


class PractitionerSuggestionOut(BaseModel):
    id: str
    name: str
    title: str
    city: str
    online_available: bool
    in_person_available: bool
    why_this_one: str
    website: str
    contact_phone: str = ""
    image_url: str = ""


class TurnResponse(BaseModel):
    speech: str
    sources_used: list[SourceOut]
    product_suggestion: ProductSuggestionOut | None
    practitioner_suggestion: PractitionerSuggestionOut | None = None
    ended: bool
    suggested_replies: list[str] = []


def _resolve_sources(page_ids: list[str]) -> list[SourceOut]:
    out: list[SourceOut] = []
    for pid in page_ids:
        meta = retrieval.get_page_meta(pid)
        if meta is None:
            continue
        out.append(SourceOut(page_id=pid, title=meta["title"], url=meta["url"]))
    return out


def _resolve_product(suggestion) -> ProductSuggestionOut | None:
    if suggestion is None:
        return None
    product = tools.get_product(suggestion.id)
    if product is None:
        return None
    return ProductSuggestionOut(
        id=suggestion.id,
        name=product.get("name", ""),
        price=product.get("price", ""),
        why_this_one=suggestion.why_this_one,
        pdp_url=product.get("pdp_url", ""),
        image_url=product.get("image_url", ""),
    )


def _resolve_practitioner(suggestion) -> PractitionerSuggestionOut | None:
    if suggestion is None:
        return None
    practitioner = tools.get_practitioner(suggestion.id)
    if practitioner is None:
        return None
    return PractitionerSuggestionOut(
        id=suggestion.id,
        name=practitioner.get("name", ""),
        title=practitioner.get("title", ""),
        city=practitioner.get("city", ""),
        online_available=bool(practitioner.get("online_available", False)),
        in_person_available=bool(practitioner.get("in_person_available", False)),
        why_this_one=suggestion.why_this_one,
        website=practitioner.get("website", ""),
        contact_phone=practitioner.get("contact_phone", ""),
        image_url=practitioner.get("image_url", ""),
    )


@app.post("/api/turn", response_model=TurnResponse)
async def api_turn(req: TurnRequest) -> TurnResponse:
    result = await julia.handle_turn(req.session_id, req.user_text, req.channel)
    return TurnResponse(
        speech=result.speech,
        sources_used=_resolve_sources(result.sources_used),
        product_suggestion=_resolve_product(result.product_suggestion),
        practitioner_suggestion=_resolve_practitioner(result.practitioner_suggestion),
        ended=result.ended,
        suggested_replies=result.suggested_replies,
    )


@app.get("/api/tts")
async def api_tts(text: str):
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        return StreamingResponse(tts.stream_tts(text), media_type="audio/mpeg")
    except Exception:
        try:
            audio = await tts.oneshot_tts(text)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TTS upstream error: {e}") from e
        return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/stt")
async def api_stt(audio: UploadFile = File(...)) -> dict[str, str]:
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="audio is empty")
    mime = audio.content_type or "audio/webm"
    filename = audio.filename or "speech.webm"
    try:
        text = await stt.transcribe(data, mime=mime, filename=filename)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"STT upstream error: {e}") from e
    return {"text": text}


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
