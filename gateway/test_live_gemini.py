import asyncio
import os
import sys
import tempfile
import wave
import winsound
from pathlib import Path
from dotenv import load_dotenv

import struct
import httpx
from adapters import GeminiLLM, GeminiSTT, GeminiTTS
from app import app

# ============================================================================
# Live Gemini Voice Test (Runs on your Dell Laptop Speakers!)
# Secrets are loaded from outside the repo: C:\Users\DELL\.robo_secrets\gateway.env
# ============================================================================


def save_24k_pcm_as_wav(pcm24k: bytes, wav_path: str) -> None:
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm24k)


async def main() -> None:
    ext_env = Path.home() / ".robo_secrets" / "gateway.env"
    if ext_env.exists():
        load_dotenv(ext_env)
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "PASTE_YOUR_GEMINI_API_KEY_HERE":
        print(f"[!] Please open {ext_env} and paste your GEMINI_API_KEY first!")
        sys.exit(1)

    async with httpx.AsyncClient() as client:
        llm = GeminiLLM(client)
        tts = GeminiTTS(client)
        stt = GeminiSTT(client)

        prompt = "Hello little robot! Introduce yourself in one short cheerful sentence."
        print(f"\n[1/3] Asking Gemini Brain ({llm.model}): '{prompt}'")
        sentences = [s async for s in llm.reply([], prompt)]
        full_reply = " ".join(sentences)
        print(f"      Gemini Reply: {full_reply}")

        print(f"\n[2/3] Generating 24 kHz Robot Voice via Gemini TTS ({tts.model}, voice={tts.voice})...")
        pcm_chunks = bytearray()
        for s in sentences:
            clean_s = s.replace("[WAVE]", "").strip()
            if clean_s:
                async for chunk in tts.speak(clean_s):
                    pcm_chunks.extend(chunk)

        out_wav = os.path.join(tempfile.gettempdir(), "robo_gemini_reply.wav")
        save_24k_pcm_as_wav(bytes(pcm_chunks), out_wav)
        duration_sec = len(pcm_chunks) / (24000 * 2)
        print(f"      Saved {len(pcm_chunks)} bytes ({duration_sec:.2f} sec) to {out_wav}")

        print("\n[3/4] Playing robot voice aloud on your Dell laptop speakers...")
        winsound.PlaySound(out_wav, winsound.SND_FILENAME)

        print("\n[4/4] Testing Full HTTP POST /talk Endpoint (GeminiSTT -> GeminiLLM -> GeminiTTS)...")
        # Downsample 24 kHz PCM (2:3 nearest) to 16 kHz mono s16le to simulate ESP32 mic upload
        samples_24k = struct.unpack(f"<{len(pcm_chunks)//2}h", pcm_chunks)
        samples_16k = [samples_24k[int(i * 1.5)] for i in range(int(len(samples_24k) / 1.5))]
        pcm_16k = struct.pack(f"<{len(samples_16k)}h", *samples_16k)

        token = os.getenv("GATEWAY_BEARER_TOKEN", "")
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_test:
                resp = await http_test.post(
                    "/talk",
                    content=pcm_16k,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "audio/pcm;rate=16000",
                    },
                )
                assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}"
                assert resp.headers.get("X-Sample-Rate") == "24000"
                assert len(resp.content) > 4800 and len(resp.content) % 2 == 0
                print(
                    f"      HTTP POST /talk -> {resp.status_code} OK | "
                    f"X-Sample-Rate: {resp.headers.get('X-Sample-Rate')} | "
                    f"X-Action: {resp.headers.get('X-Action', 'None')} | "
                    f"Streamed {len(resp.content)} bytes of 24 kHz PCM"
                )
        print("[PASS] Live Gemini Brain + Voice + Full /talk HTTP Pipeline succeeded!")


if __name__ == "__main__":
    asyncio.run(main())
