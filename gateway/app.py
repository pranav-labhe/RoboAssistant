import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from adapters import STT, LLM, TTS, build_adapters, ensure_even_pcm_bytes

# Load secrets from outside the repository first (~/.robo_secrets/gateway.env)
_EXTERNAL_ENV = Path.home() / ".robo_secrets" / "gateway.env"
if _EXTERNAL_ENV.exists():
    load_dotenv(_EXTERNAL_ENV)
load_dotenv()

# ============================================================================
# ESP32-S3 Tabletop Robot — Provider-Agnostic Voice Gateway
# Contract:
#   - GET  /health (or /voice/health) -> 200 JSON status
#   - POST /echo   (or /voice/echo)   -> 16 kHz s16le mono PCM in,
#                                        16 kHz s16le mono PCM out (X-Sample-Rate: 16000)
#   - POST /talk   (or /voice/talk)   -> 16 kHz s16le mono PCM in,
#                                        24 kHz s16le mono PCM out (X-Sample-Rate: 24000)
#                                        Optional header: X-Action: WAVE
# ============================================================================

MAX_HISTORY_MESSAGES = 6  # Last 3 user+assistant turns


class GatewayState:
    def __init__(self) -> None:
        self.http_client: Optional[httpx.AsyncClient] = None
        self.stt: Optional[STT] = None
        self.llm: Optional[LLM] = None
        self.tts: Optional[TTS] = None
        self.provider_info: dict[str, str] = {}
        self.history: list[dict[str, str]] = []


state = GatewayState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.http_client = httpx.AsyncClient()
    state.stt, state.llm, state.tts, state.provider_info = build_adapters(state.http_client)
    yield
    if state.http_client:
        await state.http_client.aclose()


app = FastAPI(title="Robot Voice Gateway", version="1.0.0", lifespan=lifespan)


def verify_bearer_token(authorization: Optional[str]) -> None:
    expected = os.getenv("GATEWAY_BEARER_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="GATEWAY_BEARER_TOKEN not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization[len("Bearer ") :].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid Bearer token")


@app.get("/health")
@app.get("/voice/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "providers": state.provider_info,
        "history_turns": len(state.history) // 2,
    }


@app.post("/echo")
@app.post("/voice/echo")
async def echo_pcm(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> StreamingResponse:
    """
    Gate 7: Echoes recorded 16 kHz, 16-bit signed LE mono PCM back at 16 kHz
    using chunked transfer encoding. Proves the full HTTP chunk decoder +
    odd-byte alignment guard on the ESP32-S3 with zero AI involved.
    """
    verify_bearer_token(authorization)
    raw_pcm = ensure_even_pcm_bytes(await request.body())
    if not raw_pcm:
        raise HTTPException(status_code=400, detail="Empty audio payload")

    async def stream_16k_chunks() -> AsyncIterator[bytes]:
        # Intentionally yield an initial odd-sized chunk (4095 bytes) followed by
        # standard chunks so Gate 7 rigorously tests the ESP32's 1-byte carryover guard!
        if len(raw_pcm) > 4095:
            yield raw_pcm[:4095]
            offset = 4095
        else:
            offset = 0
        chunk_size = 4096
        while offset < len(raw_pcm):
            yield raw_pcm[offset : offset + chunk_size]
            offset += chunk_size

    return StreamingResponse(
        stream_16k_chunks(),
        media_type="application/octet-stream",
        headers={"X-Sample-Rate": "16000"},
    )


def extract_action_tag(sentence: str) -> tuple[Optional[str], str]:
    """Extracts leading [WAVE] tag from the first sentence so TTS doesn't read it aloud."""
    trimmed = sentence.strip()
    if trimmed.upper().startswith("[WAVE]"):
        cleaned = trimmed[6:].strip()
        return "WAVE", cleaned
    return None, trimmed


@app.post("/talk")
@app.post("/voice/talk")
async def talk_pipeline(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Response:
    """
    Gate 8 & 9:
      1. Transcribes incoming 16 kHz s16le mono PCM via STT adapter.
      2. Pulls Sentence 1 from LLM adapter to determine optional X-Action header.
      3. Streams synthesized 24 kHz s16le mono PCM sentence-by-sentence.
    """
    verify_bearer_token(authorization)
    pcm16k = ensure_even_pcm_bytes(await request.body())
    if len(pcm16k) < 16000 * 2 * 4 // 10:  # < 400 ms at 16 kHz 16-bit
        return Response(status_code=204)

    assert state.stt is not None and state.llm is not None and state.tts is not None

    transcript = (await state.stt.transcribe(pcm16k)).strip()
    if not transcript:
        return Response(status_code=204)

    sentence_iter = state.llm.reply(state.history, transcript).__aiter__()
    try:
        first_raw_sentence = await sentence_iter.__anext__()
    except StopAsyncIteration:
        return Response(status_code=204)

    action_tag, first_sentence = extract_action_tag(first_raw_sentence)
    headers = {"X-Sample-Rate": "24000"}
    if action_tag:
        headers["X-Action"] = action_tag

    async def stream_sentence_pcm() -> AsyncIterator[bytes]:
        collected_sentences: list[str] = []

        if first_sentence:
            collected_sentences.append(first_sentence)
            async for pcm_chunk in state.tts.speak(first_sentence):
                yield pcm_chunk

        async for next_raw_sentence in sentence_iter:
            _, clean_sentence = extract_action_tag(next_raw_sentence)
            if not clean_sentence:
                continue
            collected_sentences.append(clean_sentence)
            async for pcm_chunk in state.tts.speak(clean_sentence):
                yield pcm_chunk

        full_reply = " ".join(collected_sentences).strip()
        if full_reply:
            state.history.append({"role": "user", "content": transcript})
            state.history.append({"role": "assistant", "content": full_reply})
            if len(state.history) > MAX_HISTORY_MESSAGES:
                del state.history[:-MAX_HISTORY_MESSAGES]

    return StreamingResponse(
        stream_sentence_pcm(),
        media_type="application/octet-stream",
        headers=headers,
    )
