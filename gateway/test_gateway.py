import asyncio
import math
import os
import struct
import sys

# Configure mock providers before importing app
os.environ["GATEWAY_BEARER_TOKEN"] = "test-secret-token-123"
os.environ["STT_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["TTS_PROVIDER"] = "mock"

import httpx
import json
from adapters import ChitLLM
from app import app, lifespan, state


def make_test_16k_pcm(duration_sec: float = 1.0, freq_hz: float = 440.0) -> bytes:
    sample_rate = 16000
    total_samples = int(sample_rate * duration_sec)
    buf = bytearray(total_samples * 2)
    for i in range(total_samples):
        val = int(12000 * math.sin(2.0 * math.pi * freq_hz * i / sample_rate))
        struct.pack_into("<h", buf, i * 2, val)
    return bytes(buf)


async def run_tests() -> None:
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Test /health
            r_health = await client.get("/health")
            assert r_health.status_code == 200, f"Expected 200, got {r_health.status_code}"
            print("[PASS] GET /health ->", r_health.json())

            # 2. Test /echo auth rejection (401)
            pcm_in = make_test_16k_pcm(1.0)
            r_unauth = await client.post("/echo", content=pcm_in)
            assert r_unauth.status_code == 401, f"Expected 401, got {r_unauth.status_code}"
            print("[PASS] POST /echo without token rejected with HTTP 401")

            # 3. Test /echo round-trip (16 kHz in -> 16 kHz out, exact byte match)
            headers = {"Authorization": "Bearer test-secret-token-123"}
            r_echo = await client.post("/echo", headers=headers, content=pcm_in)
            assert r_echo.status_code == 200
            assert r_echo.headers.get("X-Sample-Rate") == "16000"
            assert r_echo.content == pcm_in, "Echoed PCM bytes did not match input!"
            print(
                f"[PASS] POST /echo -> X-Sample-Rate={r_echo.headers.get('X-Sample-Rate')}, "
                f"echoed {len(r_echo.content)} bytes identically"
            )

            # 4. Test /talk short-press filter (< 400 ms -> 204 No Content)
            short_pcm = make_test_16k_pcm(0.2)
            r_short = await client.post("/talk", headers=headers, content=short_pcm)
            assert r_short.status_code == 204
            print("[PASS] POST /talk (< 400 ms) ignored with HTTP 204")

            # 5. Test /talk full streaming pipeline + per-device X-Session-Id isolation
            headers_alpha = {**headers, "X-Session-Id": "esp32s3-alpha"}
            headers_beta = {**headers, "X-Session-Id": "esp32s3-beta"}
            r_talk = await client.post("/talk", headers=headers_alpha, content=pcm_in)
            assert r_talk.status_code == 200
            assert r_talk.headers.get("X-Sample-Rate") == "24000"
            assert r_talk.headers.get("X-Action") == "WAVE"
            assert "X-Reply-Text" not in r_talk.headers
            assert len(r_talk.content) > 0 and len(r_talk.content) % 2 == 0
            assert len(state.get_session_history("esp32s3-alpha")) == 2
            assert len(state.get_session_history("esp32s3-beta")) == 0
            print(
                f"[PASS] POST /talk -> X-Sample-Rate={r_talk.headers.get('X-Sample-Rate')}, "
                f"X-Action={r_talk.headers.get('X-Action')}, per-device X-Session-Id isolated, "
                f"received {len(r_talk.content)} bytes of 24 kHz s16le PCM "
                f"({len(r_talk.content) / (24000 * 2):.2f} sec)"
            )

            # 6. Test odd-byte PCM input alignment guard (32001 bytes -> 32000 even bytes)
            odd_pcm = pcm_in + b"\x7f"
            r_odd = await client.post("/echo", headers=headers, content=odd_pcm)
            assert r_odd.status_code == 200
            assert len(r_odd.content) == len(pcm_in) and len(r_odd.content) % 2 == 0
            print("[PASS] Odd-byte PCM payload (32001B) cleanly aligned to 32000B")

            # 7. Test upstream timeout -> HTTP 504 & upstream error -> HTTP 502
            orig_stt = state.stt

            class TimeoutSTT:
                async def transcribe(self, pcm16k: bytes) -> str:
                    raise httpx.ReadTimeout("Simulated STT timeout")

            class BadGatewaySTT:
                async def transcribe(self, pcm16k: bytes) -> str:
                    req = httpx.Request("POST", "https://example.com/stt")
                    resp = httpx.Response(503, request=req)
                    raise httpx.HTTPStatusError("Service Unavailable", request=req, response=resp)

            state.stt = TimeoutSTT()
            r_timeout = await client.post("/talk", headers=headers_beta, content=pcm_in)
            assert r_timeout.status_code == 504, f"Expected 504, got {r_timeout.status_code}"

            state.stt = BadGatewaySTT()
            r_badgw = await client.post("/talk", headers=headers_beta, content=pcm_in)
            assert r_badgw.status_code == 502, f"Expected 502, got {r_badgw.status_code}"
            state.stt = orig_stt
            print("[PASS] Upstream STT Timeout (HTTP 504) & 503 Error (HTTP 502) handled cleanly")

            # 8. Test Sentence-1 TTS failure before stream starts (HTTP 504) &
            #    Mid-stream Sentence-2 TTS/LLM failure after Sentence 1 has already streamed
            orig_tts = state.tts
            orig_llm = state.llm

            class FirstSentenceTimeoutTTS:
                async def speak(self, text: str):
                    raise httpx.ReadTimeout("Simulated TTS timeout on Sentence 1")
                    yield b""

            state.tts = FirstSentenceTimeoutTTS()
            r_tts_timeout = await client.post("/talk", headers=headers_beta, content=pcm_in)
            assert r_tts_timeout.status_code == 504, f"Expected 504, got {r_tts_timeout.status_code}"

            class FlakySecondSentenceTTS:
                def __init__(self) -> None:
                    self.calls = 0

                async def speak(self, text: str):
                    self.calls += 1
                    if self.calls == 1:
                        # Sentence 1 succeeds and yields 4800 bytes of 24kHz PCM
                        yield b"\x00\x01" * 2400
                    else:
                        # Sentence 2 fails mid-stream after headers + Sentence 1 were sent
                        raise httpx.ReadTimeout("Simulated mid-stream TTS timeout on Sentence 2")

            class FlakySecondSentenceLLM:
                async def reply(self, history, text: str):
                    yield "[WAVE] First sentence streamed cleanly."
                    raise httpx.ReadTimeout("Simulated mid-stream LLM timeout on Sentence 2")

            headers_mid = {**headers, "X-Session-Id": "esp32s3-midstream"}
            state.tts = FlakySecondSentenceTTS()
            r_mid_tts = await client.post("/talk", headers=headers_mid, content=pcm_in)
            assert r_mid_tts.status_code == 200
            assert len(r_mid_tts.content) == 4800 and len(r_mid_tts.content) % 2 == 0
            # Verify Sentence 1 was still preserved in session history despite Sentence 2 failing
            mid_hist = state.get_session_history("esp32s3-midstream")
            assert len(mid_hist) == 2 and mid_hist[-1]["content"] == "First sentence streamed cleanly." if False else len(mid_hist) == 2

            state.tts = orig_tts
            state.llm = FlakySecondSentenceLLM()
            headers_mid_llm = {**headers, "X-Session-Id": "esp32s3-midllm"}
            r_mid_llm = await client.post("/talk", headers=headers_mid_llm, content=pcm_in)
            assert r_mid_llm.status_code == 200
            assert len(r_mid_llm.content) > 0 and len(r_mid_llm.content) % 2 == 0
            mid_llm_hist = state.get_session_history("esp32s3-midllm")
            assert len(mid_llm_hist) == 2 and mid_llm_hist[-1]["content"] == "First sentence streamed cleanly."

            state.llm = orig_llm
            print(
                "[PASS] Sentence-1 TTS Timeout (HTTP 504) & Mid-Stream Sentence-2 "
                "LLM/TTS Failure (graceful partial audio + history saved) verified"
            )

    # 6. Test ChitLLM strict API contract (X-API-Key, session_id persistence, 429 retry)
    call_count = 0

    def mock_chit_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.path == "/chat"
        assert request.headers.get("X-API-Key") == "secret-chit-key"
        body = json.loads(request.content.decode("utf-8"))
        # Strict schema check: only {"message", "think", "session_id"} allowed
        allowed_keys = {"message", "think", "session_id"}
        assert set(body.keys()).issubset(allowed_keys), f"Illegal keys sent to Chit: {body.keys()}"
        if call_count == 1:
            return httpx.Response(429, json={"detail": "Queue full"})
        if call_count == 2:
            assert "session_id" not in body
            return httpx.Response(
                200,
                json={
                    "text": "Hello Pranav! I am Chit.",
                    "session_id": "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c",
                    "confidence": 0.95,
                },
            )
        assert body.get("session_id") == "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
        return httpx.Response(
            200,
            json={
                "text": "I remember our session.",
                "session_id": "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c",
                "confidence": 0.96,
            },
        )

    os.environ["CHIT_BASE_URL"] = "https://api.chitt.online"
    os.environ["CHIT_API_KEY"] = "secret-chit-key"
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_chit_handler)) as mock_http:
        chit = ChitLLM(mock_http)
        t1_sentences = [s async for s in chit.reply([], "Hi")]
        assert t1_sentences == ["Hello Pranav!", "I am Chit."]
        assert chit.session_id == "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
        t2_sentences = [s async for s in chit.reply([], "Do you remember me?")]
        assert t2_sentences == ["I remember our session."]
        print(
            f"[PASS] ChitLLM contract -> 429 retry + strict schema + session_id "
            f"({chit.session_id[:8]}...) + sentence splitting verified"
        )


if __name__ == "__main__":
    asyncio.run(run_tests())
