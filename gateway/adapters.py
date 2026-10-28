import asyncio
import base64
import io
import json
import math
import os
import re
import struct
import wave
from typing import AsyncIterator, Optional, Protocol

import httpx

# ============================================================================
# Provider-Agnostic Adapter Interfaces
#   class STT: async def transcribe(self, pcm16k: bytes) -> str
#   class LLM: async def reply(self, history, text: str) -> AsyncIterator[str]
#   class TTS: async def speak(self, text: str) -> AsyncIterator[bytes] (24kHz s16le mono)
# ============================================================================

ROBOT_SYSTEM_PROMPT = (
    "You are a cheerful small tabletop robot. Reply in plain spoken English "
    "under 25 words without markdown. If waving hello or goodbye, prefix your "
    "reply with [WAVE]."
)


class STT(Protocol):
    async def transcribe(self, pcm16k: bytes) -> str:
        ...


class LLM(Protocol):
    async def reply(self, history: list[dict[str, str]], text: str) -> AsyncIterator[str]:
        ...


class TTS(Protocol):
    async def speak(self, text: str) -> AsyncIterator[bytes]:
        ...


def pcm16k_to_wav_bytes(pcm16k: bytes) -> bytes:
    """Wraps raw 16 kHz, 16-bit signed LE mono PCM in a 44-byte RIFF WAV header."""
    if len(pcm16k) % 2 != 0:
        pcm16k = pcm16k[:-1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm16k)
    return buf.getvalue()


def split_into_sentences(text: str) -> list[str]:
    """Splits text into speakable sentences while preserving punctuation."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


def ensure_even_pcm_bytes(data: bytes) -> bytes:
    """Guarantees 16-bit s16le PCM byte buffers never have a dangling odd byte."""
    if len(data) % 2 != 0:
        return data[:-1]
    return data


# ============================================================================
# 1. CHIT ADAPTER (`C:\Python\Chit` / `https://api.chitt.online`)
# Contract from C:\Python\Chit\API.md & ChitAPIGuide.md:
#   - POST /chat with header `X-API-Key: <CHIT_API_KEY>`
#   - Strict request body: {"message": str, "session_id": str (optional), "think": bool}
#   - Unknown fields are rejected with HTTP 422.
#   - Bounded FIFO inference queue returns HTTP 429 when full -> backoff 1s, 2s, 4s.
# ============================================================================


class ChitLLM:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.base_url = os.getenv("CHIT_BASE_URL", "https://api.chitt.online").rstrip("/")
        self.api_key = os.getenv("CHIT_API_KEY", "")
        self.think = os.getenv("CHIT_THINK", "false").lower() == "true"
        self.session_id: Optional[str] = None

    async def reply(self, history: list[dict[str, str]], text: str) -> AsyncIterator[str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        payload: dict[str, object] = {
            "message": text,
            "think": self.think,
        }
        if self.session_id:
            payload["session_id"] = self.session_id

        backoff_seconds = [1.0, 2.0, 4.0]
        resp: Optional[httpx.Response] = None

        for attempt, delay in enumerate(backoff_seconds + [0.0]):
            resp = await self.client.post(
                f"{self.base_url}/chat",
                headers=headers,
                json=payload,
                timeout=30.0,
            )
            if resp.status_code == 429 and attempt < len(backoff_seconds):
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            break

        assert resp is not None
        data = resp.json()
        if isinstance(data.get("session_id"), str) and data["session_id"]:
            self.session_id = data["session_id"]

        reply_text = str(data.get("text", "")).strip()
        for sentence in split_into_sentences(reply_text):
            yield sentence


# ============================================================================
# 2. GOOGLE GEMINI ADAPTERS (Separate STT, LLM, TTS calls)
# ============================================================================


class GeminiSTT:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_STT_MODEL", "gemini-3.1-flash-lite")

    async def transcribe(self, pcm16k: bytes) -> str:
        wav_bytes = pcm16k_to_wav_bytes(pcm16k)
        b64_wav = base64.b64encode(wav_bytes).decode("ascii")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Transcribe the spoken words in this audio accurately into "
                                "plain text. Return ONLY the transcript, or an empty string "
                                "if there is only silence or noise."
                            )
                        },
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_wav,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "thinkingConfig": {"thinkingLevel": "MINIMAL"},
            },
        }
        resp = await self.client.post(
            url,
            headers={"x-goog-api-key": self.api_key},
            json=payload,
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(
            str(p.get("text", "")) for p in parts if not p.get("thought")
        ).strip()


class GeminiLLM:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_LLM_MODEL", "gemini-3.1-flash-lite")

    async def reply(self, history: list[dict[str, str]], text: str) -> AsyncIterator[str]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        contents = []
        for turn in history[-6:]:
            role = "user" if turn["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": turn["content"]}]})
        contents.append({"role": "user", "parts": [{"text": text}]})

        payload = {
            "systemInstruction": {"parts": [{"text": ROBOT_SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 256,
                "temperature": 0.6,
                "thinkingConfig": {"thinkingLevel": "MINIMAL"},
            },
        }
        resp = await self.client.post(
            url,
            headers={"x-goog-api-key": self.api_key},
            json=payload,
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return
        parts = candidates[0].get("content", {}).get("parts") or []
        full_text = "".join(
            str(p.get("text", "")) for p in parts if not p.get("thought")
        ).strip()
        sentences = split_into_sentences(full_text)
        # Yield Sentence 1 immediately for low latency, then batch any remaining
        # sentences into a single second TTS call to protect Gemini RPM quota.
        if len(sentences) <= 2:
            for sentence in sentences:
                yield sentence
        else:
            yield sentences[0]
            yield " ".join(sentences[1:])


class GeminiTTS:
    """Calls Gemini TTS and decodes base64 JSON audio into raw 24 kHz s16le mono PCM."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
        self.voice = os.getenv("GEMINI_TTS_VOICE", "Puck")

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": self.voice}
                    }
                },
            },
        }
        resp = await self.client.post(
            url,
            headers={"x-goog-api-key": self.api_key},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return
        parts = candidates[0].get("content", {}).get("parts") or []
        for part in parts:
            inline = part.get("inlineData") or {}
            b64_audio = inline.get("data")
            if b64_audio:
                raw_pcm = ensure_even_pcm_bytes(base64.b64decode(b64_audio))
                chunk_size = 4096
                for offset in range(0, len(raw_pcm), chunk_size):
                    yield raw_pcm[offset : offset + chunk_size]


# ============================================================================
# 3. OPENAI / OPENAI-COMPATIBLE ADAPTERS (/v1/...)
# ============================================================================


class OpenAICompatSTT:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_STT_MODEL", "whisper-1")

    async def transcribe(self, pcm16k: bytes) -> str:
        wav_bytes = pcm16k_to_wav_bytes(pcm16k)
        files = {"file": ("utterance.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}
        resp = await self.client.post(
            f"{self.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            data=data,
            files=files,
            timeout=25.0,
        )
        resp.raise_for_status()
        return str(resp.json().get("text", "")).strip()


class OpenAICompatLLM:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini")

    async def reply(self, history: list[dict[str, str]], text: str) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": ROBOT_SYSTEM_PROMPT}]
        messages.extend(history[-6:])
        messages.append({"role": "user", "content": text})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 80,
            "temperature": 0.6,
            "stream": True,
        }
        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=25.0,
        ) as resp:
            resp.raise_for_status()
            buf = ""
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if raw == "[DONE]":
                    break
                try:
                    chunk = json.loads(raw)
                    delta = chunk["choices"][0].get("delta", {}).get("content", "")
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if not delta:
                    continue
                buf += delta
                while True:
                    m = re.search(r"([.!?])\s+", buf)
                    if not m:
                        break
                    cut = m.end()
                    sentence = buf[:cut].strip()
                    buf = buf[cut:]
                    if sentence:
                        yield sentence
            if buf.strip():
                yield buf.strip()


class OpenAICompatTTS:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
        self.voice = os.getenv("OPENAI_TTS_VOICE", "alloy")

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": "pcm",  # Raw 24 kHz 16-bit signed LE mono PCM
        }
        async with self.client.stream(
            "POST",
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=30.0,
        ) as resp:
            resp.raise_for_status()
            carry = b""
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                data = carry + chunk
                if len(data) % 2 != 0:
                    carry = data[-1:]
                    data = data[:-1]
                else:
                    carry = b""
                if data:
                    yield data


# ============================================================================
# 4. MOCK ADAPTERS (Zero-Key Local Verification for `curl` & Gate 8/9 Bring-Up)
# ============================================================================


class MockSTT:
    async def transcribe(self, pcm16k: bytes) -> str:
        duration_sec = len(pcm16k) / (16000 * 2)
        return f"Hello robot, this is a {duration_sec:.1f} second audio test."


class MockLLM:
    async def reply(self, history: list[dict[str, str]], text: str) -> AsyncIterator[str]:
        yield "[WAVE] Hello Pranav! I received your audio cleanly."
        await asyncio.sleep(0.05)
        yield "My gateway streaming pipeline is ready for your ESP32."


class MockTTS:
    """Generates a pleasant 24 kHz 16-bit mono PCM two-tone chime per sentence."""

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        sample_rate = 24000
        duration_sec = 0.35
        total_samples = int(sample_rate * duration_sec)
        freq = 523.25 if "Hello" in text else 659.25  # C5 then E5
        samples = bytearray(total_samples * 2)
        for i in range(total_samples):
            val = int(8000 * math.sin(2.0 * math.pi * freq * i / sample_rate))
            struct.pack_into("<h", samples, i * 2, val)
        chunk_size = 4096
        for offset in range(0, len(samples), chunk_size):
            yield bytes(samples[offset : offset + chunk_size])


def build_adapters(client: httpx.AsyncClient) -> tuple[STT, LLM, TTS, dict[str, str]]:
    stt_name = os.getenv("STT_PROVIDER", "gemini").lower()
    llm_name = os.getenv("LLM_PROVIDER", "gemini").lower()
    tts_name = os.getenv("TTS_PROVIDER", "gemini").lower()

    if stt_name == "openai":
        stt: STT = OpenAICompatSTT(client)
    elif stt_name == "mock":
        stt = MockSTT()
    else:
        stt = GeminiSTT(client)

    if llm_name == "chit":
        llm: LLM = ChitLLM(client)
    elif llm_name == "openai":
        llm = OpenAICompatLLM(client)
    elif llm_name == "mock":
        llm = MockLLM()
    else:
        llm = GeminiLLM(client)

    if tts_name == "openai":
        tts: TTS = OpenAICompatTTS(client)
    elif tts_name == "mock":
        tts = MockTTS()
    else:
        tts = GeminiTTS(client)

    info = {"stt": stt_name, "llm": llm_name, "tts": tts_name}
    return stt, llm, tts, info
