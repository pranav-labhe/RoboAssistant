# 🤖 RoboAssistant — ESP32-S3 Tabletop Voice Robot & Provider-Agnostic Gateway

A compact tabletop voice robot (~20–30 cm, inspired by OttoRobot + XiaoZhi AI) built on the **`ESP32-S3-WROOM-1-N16R8`** (16 MB Flash, 8 MB Octal PSRAM) with a provider-agnostic FastAPI **Voice Gateway** (`STT → LLM → TTS`).

---

## ✨ Key Architectural Highlights

* **Zero Cloud API Keys on the Chip**: The ESP32-S3 firmware holds zero OpenAI or Gemini keys and never parses large JSON/base64 payloads in SRAM. It talks exclusively to your thin Python Gateway (`/health`, `/echo`, `/talk`) over verified TLS (`ISRG Root X2`, `ISRG Root YE`, `ISRG Root X1`, `GlobalSign ECC Root CA - R4`).
* **Outside-Repo Secrets Isolation**: All API keys (`GEMINI_API_KEY`, `CHIT_API_KEY`) and Wi-Fi credentials live outside the repository in `~/.robo_secrets/` (`gateway.env` and `robot_secrets.h`) so they can never be committed to Git.
* **Pluggable 3-Stage Voice Pipeline (`gateway/`)**:
  * **STT**: Google Gemini (`gemini-3.1-flash-lite`), OpenAI (`whisper-1`), or `mock`
  * **LLM**: Google Gemini (`gemini-3.1-flash-lite`), **Chit** (`https://api.chitt.online` `/chat` with strict schema + `session_id` reuse + `HTTP 429` exponential backoff), OpenAI-compatible (`/v1/chat/completions`), or `mock`
  * **TTS**: Google Gemini (`gemini-3.8-flash-tts`, 24 kHz 16-bit mono PCM), OpenAI (`tts-1`), or `mock`
* **12-Gate Hardware & Firmware Verification Protocol**: Isolated PlatformIO build environments (`gate1_board` through `gate7_echo` + `robot_main`) pinned to `espressif32 @ 6.9.0` (Arduino-ESP32 v2.0.17).
* **Dual-Core FreeRTOS Firmware (`src/robot_main/main.cpp`)**:
  * **Core 0 (`DisplayTask`, priority 1)**: 30 FPS non-blocking LovyanGFX PSRAM sprite eye expressions (`BOOT`, `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `ERROR`).
  * **Core 1 (`loop()`)**: Push-to-Talk (`GPIO 1`), 16 kHz `INMP441` capture with DC-offset removal into PSRAM, 400 ms PSRAM pre-buffer, 1-byte odd-chunk carryover guard, and stereo `[L, L]` `MAX98357A` playback.

---

## 📂 Repository Structure

* [`ROADMAP.md`](ROADMAP.md) — Master BOM, conflict-free GPIO pin map, and 12-Gate verification checklist
* [`platformio.ini`](platformio.ini) — Pinned `espressif32 @ 6.9.0` configuration for all 8 firmware targets
* [`include/pins.h`](include/pins.h) — Hardware GPIO definitions (`INMP441`, `MAX98357A`, `ST7789`, PTT `GPIO 1`, Servo `GPIO 18`)
* [`include/certs.h`](include/certs.h) — Multi-root TLS CA bundle (`ISRG Root X2`, `Root YE`, `ISRG Root X1`, `GlobalSign ECC Root CA - R4`)
* [`src/`](src/) — Gate 1–7 hardware validation sketches and full `robot_main` firmware
* [`gateway/`](gateway/) — FastAPI Voice Gateway (`app.py`, `adapters.py`, `Containerfile`, `DEPLOY.md`, unit & live tests)

---

## 🚀 Quickstart

### 1. Run the Voice Gateway Locally
```powershell
cd c:\Robo\gateway
python test_gateway.py
python test_live_gemini.py
uvicorn app:app --host 0.0.0.0 --port 8002
```

### 2. Compile All 8 Firmware Environments
```powershell
cd c:\Robo
python -m platformio run -e gate1_board -e gate2_display -e gate3_mic -e gate4_speaker -e gate5_loopback -e gate6_health -e gate7_echo -e robot_main
```

See [`gateway/DEPLOY.md`](gateway/DEPLOY.md) for AWS EC2 (`t4g.small`) Docker + Caddy deployment instructions alongside **Chit**.
