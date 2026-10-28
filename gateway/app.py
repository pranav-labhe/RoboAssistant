import os
import re
from collections import OrderedDict
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

MAX_HISTORY_MESSAGES = 6  # Last 3 user+assistant turns per session
MAX_SESSIONS = 32         # Bounded LRU session store for 2 GiB EC2 safety


class GatewayState:
    def __init__(self) -> None:
        self.http_client: Optional[httpx.AsyncClient] = None
        self.stt: Optional[STT] = None
        self.llm: Optional[LLM] = None
        self.tts: Optional[TTS] = None
        self.provider_info: dict[str, str] = {}
        self.sessions: OrderedDict[str, list[dict[str, str]]] = OrderedDict()

    def get_session_history(self, session_id: str) -> list[dict[str, str]]:
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", session_id or "")[:64] or "default-robot"
        if clean_id in self.sessions:
            self.sessions.move_to_end(clean_id)
            return self.sessions[clean_id]
        if len(self.sessions) >= MAX_SESSIONS:
            self.sessions.popitem(last=False)
        self.sessions[clean_id] = []
        return self.sessions[clean_id]


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
async def health(
    x_session_id: Optional[str] = Header(default="default-robot"),
) -> dict[str, object]:
    session_hist = state.sessions.get(x_session_id or "default-robot", [])
    return {
        "status": "ok",
        "providers": state.provider_info,
        "active_sessions": len(state.sessions),
        "history_turns": len(session_hist) // 2,
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
    """Extracts [WAVE] tag anywhere in a sentence so TTS never reads it aloud."""
    trimmed = sentence.strip()
    if re.search(r"\[wave\]", trimmed, flags=re.IGNORECASE):
        cleaned = re.sub(r"\s*\[wave\]\s*", " ", trimmed, flags=re.IGNORECASE).strip()
        return "WAVE", cleaned
    return None, trimmed


@app.post("/talk")
@app.post("/voice/talk")
async def talk_pipeline(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_id: Optional[str] = Header(default="default-robot"),
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
    session_history = state.get_session_history(x_session_id or "default-robot")

    try:
        transcript = (await state.stt.transcribe(pcm16k)).strip()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream STT timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream STT error ({exc.response.status_code})",
        ) from exc

    if not transcript:
        return Response(status_code=204)

    sentence_iter = state.llm.reply(session_history, transcript).__aiter__()
    try:
        first_raw_sentence = await sentence_iter.__anext__()
    except StopAsyncIteration:
        return Response(status_code=204)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream LLM timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream LLM error ({exc.response.status_code})",
        ) from exc

    action_tag, first_sentence = extract_action_tag(first_raw_sentence)
    headers = {"X-Sample-Rate": "24000"}
    if action_tag:
        headers["X-Action"] = action_tag

    # Pre-fetch the first TTS chunk of Sentence 1 BEFORE sending HTTP 200 headers.
    # If Sentence 1 TTS times out or returns 5xx/429, we can still return clean 504/502!
    first_tts_iter: Optional[AsyncIterator[bytes]] = None
    first_pcm_chunk: bytes = b""
    if first_sentence:
        first_tts_iter = state.tts.speak(first_sentence).__aiter__()
        try:
            first_pcm_chunk = ensure_even_pcm_bytes(await first_tts_iter.__anext__())
        except StopAsyncIteration:
            first_tts_iter = None
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Upstream TTS timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream TTS error ({exc.response.status_code})",
            ) from exc

    async def stream_sentence_pcm() -> AsyncIterator[bytes]:
        collected_sentences: list[str] = []
        try:
            if first_sentence and first_tts_iter is not None:
                if first_pcm_chunk:
                    yield first_pcm_chunk
                async for pcm_chunk in first_tts_iter:
                    even_chunk = ensure_even_pcm_bytes(pcm_chunk)
                    if even_chunk:
                        yield even_chunk
                collected_sentences.append(first_sentence)

            async for next_raw_sentence in sentence_iter:
                _, clean_sentence = extract_action_tag(next_raw_sentence)
                if not clean_sentence:
                    continue
                async for pcm_chunk in state.tts.speak(clean_sentence):
                    even_chunk = ensure_even_pcm_bytes(pcm_chunk)
                    if even_chunk:
                        yield even_chunk
                collected_sentences.append(clean_sentence)
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError):
            # Mid-stream failure after HTTP 200 headers + Sentence 1 audio were already sent:
            # terminate stream cleanly on an even-byte boundary without crashing ASGI.
            pass
        finally:
            full_reply = " ".join(collected_sentences).strip()
            if full_reply:
                session_history.append({"role": "user", "content": transcript})
                session_history.append({"role": "assistant", "content": full_reply})
                if len(session_history) > MAX_HISTORY_MESSAGES:
                    del session_history[:-MAX_HISTORY_MESSAGES]

    return StreamingResponse(
        stream_sentence_pcm(),
        media_type="application/octet-stream",
        headers=headers,
    )
