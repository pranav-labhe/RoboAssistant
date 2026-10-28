# 🤖 RoboAssistant — ESP32-S3 Tabletop Voice Robot & Provider-Agnostic Voice Gateway

A compact tabletop voice robot (~20–30 cm, inspired by OttoRobot + XiaoZhi AI) powered by an **`ESP32-S3-WROOM-1-N16R8`** (16 MB Flash, 8 MB Octal PSRAM) and a thin, provider-agnostic Python FastAPI **Voice Gateway** (`STT → LLM → TTS`).

---

## 🧭 1. How the System Works (Two Built-In Modes: Direct On-Chip vs. Gateway)

Your `ESP32-S3-WROOM-1-N16R8` has **8 MB of Octal PSRAM**, which allows `robot_main` to support **both** operating modes out of the box (switchable live via `POST http://<robot-ip>/config` with zero re-flashing):

### Mode A — Direct On-Chip Cloud Mode (`mode = "direct"`, Default — Zero Extra Hop!)
```
┌───────────────────────────────────────────────────┐       Verified TLS (GTS Root R4 / ISRG)       ┌──────────────────────────────────────────┐
│        ESP32-S3 Tabletop Robot (8 MB PSRAM)       │ ────────────────────────────────────────────► │  Google Gemini API & Chit (chitt.online) │
│                                                   │  1. STT: 16kHz WAV base64 -> Gemini Flash     │                                          │
│ • Core 0: 30 FPS ST7789 Eye Animation             │  2. LLM: Gemini Flash OR Chit POST /chat      │ • Zero middleman server needed           │
│ • Core 1: Hands-Free VAD + PTT + Direct TLS       │  3. TTS: Gemini 24kHz base64 decoded in PSRAM │ • Live config via POST /config (NVS)     │
│ • Built-in HTTP Config API: GET/POST /config      │ ◄──────────────────────────────────────────── │                                          │
└───────────────────────────────────────────────────┘                                               └──────────────────────────────────────────┘
```

### Mode B — Voice Gateway Mode (`mode = "gateway"`, Sentence-by-Sentence Streaming)
```
┌───────────────────────────────────────────┐         HTTP / Verified HTTPS         ┌──────────────────────────────────────────────┐
│          ESP32-S3 Tabletop Robot          │ ────────────────────────────────────► │         Python FastAPI Voice Gateway         │
│                                           │   POST /talk (16 kHz s16le mono PCM)  │               (gateway/app.py)               │
│ • Core 0: 30 FPS ST7789 Eye Animation     │   Headers:                            │                                              │
│ • Core 1: Audio I2S + VAD/PTT + Network   │     Authorization: Bearer <TOKEN>     │  1. STT Adapter (Gemini / OpenAI / Mock)     │
│ • INMP441 Mic (16 kHz RX + DC blocker)    │     X-Session-Id: esp32s3-<MAC>       │  2. LLM Adapter (Gemini / Chit / OpenAI)     │
│ • MAX98357A Amp (24 kHz Stereo [L, L] TX) │ ◄──────────────────────────────────── │  3. TTS Adapter (Gemini 24kHz PCM / OpenAI)  │
│ • Zero Cloud API Keys on the Chip         │   200 OK Chunked Stream (24 kHz PCM)  │  • Per-device session memory (X-Session-Id)  │
└───────────────────────────────────────────┘   X-Sample-Rate: 24000, X-Action: WAVE└──────────────────────────────────────────────┘
```

* **Outside-Repo Secrets Isolation**: All sensitive credentials (Wi-Fi password, Bearer token, `GEMINI_API_KEY`, `CHIT_API_KEY`) live **outside** the Git repository in `~/.robo_secrets/` (`C:\Users\<Username>\.robo_secrets\`) or in ESP32 NVS Flash via `POST /config`.

---

## 🛒 2. Hardware Purchase & Compatibility Guide (Exact India Buying Links & Anti-Trap Guide)

> [!NOTE]
> ### 🧭 Hardware Prototyping & Component Reference Guide
> The components listed below correspond to the pin assignments and interface configurations implemented across `include/pins.h`, `src/gate1` through `gate7`, and `src/robot_main/`.
>
> ⚠️ **Marketplace & Specification Notice**:
> Third-party marketplace listings, prices, seller return policies, and component revisions vary over time. Before purchasing, check the seller's return eligibility and terms on the product page, and verify that physical module markings match the required pinout and electrical ratings.

### Master Hardware Reference & Component Shopping Table

| Item | Required Specification in Code | Reference Amazon.in Product *(Check page details before buying)* | Est. Cost (₹) | Code Mapping & Electrical Notes |
| :--- | :--- | :--- | :---: | :--- |
| **1. ESP32-S3 Dev Board** | **`ESP32-S3-WROOM-1-N16R8`** (16MB Flash, **8MB Octal PSRAM**, Dual Type-C) | • [VIREXON ESP32-S3 N16R8 Dev Board (Dual Type-C, 16MB Flash, 8MB PSRAM)](https://www.amazon.in/dp/B0H94PHFSW)<br>• *Alternate:* [AYWHP ESP32-S3-DevKitC WROOM-1-N16R8](https://www.amazon.in/dp/B0DG8L1YW9) | ₹945 – ₹999 | 🔍 **Matches `platformio.ini` line 19–20 & `direct_mode.h`**:<br>• Flash: 16 MB (`default_16MB.csv`)<br>• PSRAM: 8 MB Octal (`qio_opi`). Direct Mode allocates at runtime in PSRAM **3.47 MiB** (3,634,173 B) of audio buffers, leaving approximately 4.53 MiB of nominal PSRAM capacity before other allocations and overhead; Gateway Mode allocates **276.6 KiB** in PSRAM.<br>• Ports: Dual Type-C (`USB` native CDC on GPIO 19/20 + `UART`). Always plug into the port marked **`USB`**.<br>❌ **DO NOT BUY**: `N4` (0 PSRAM) or non-PSRAM clones. |
| **2. I²S Digital Microphone** | **INMP441** Omnidirectional MEMS mic (6-pin digital I²S breakout) | • [INMP441 MEMS Omnidirectional I2S Mic Module](https://www.amazon.in/dp/B0HKSFP54P)<br>• *Super-Value Combo:* [MAX98357A Amp + INMP441 Mic Audio Combo](https://www.amazon.in/dp/B0H3M78HQV) | ₹199 (or ₹407 for combo) | 🔍 **Matches `include/pins.h` lines 12–14 & `gate3_mic`**:<br>• `PIN_MIC_BCLK` = GPIO 4 (SCK)<br>• `PIN_MIC_WS` = GPIO 5 (WS)<br>• `PIN_MIC_SD` = GPIO 6 (SD)<br>• Tie `L/R` to `GND` for Left channel (matches `I2S_CHANNEL_FMT_ONLY_LEFT`).<br>• Power from **3.3V ONLY** (5V destroys the MEMS chip). Add `0.1 µF` ceramic capacitor across `VDD–GND`. |
| **3. I²S Class-D Audio Amp** | **MAX98357A** Mono I²S DAC + 3W Class-D amplifier breakout (7-pin) | • [MAX98357 I2S 3W Class D Audio Amplifier Module](https://www.amazon.in/dp/B0H36Z79CD)<br>• *Alternate:* [MAX98357 I2S 3W Audio Module](https://www.amazon.in/dp/B0D5VG962Q) | ₹249 | 🔍 **Matches `include/pins.h` lines 24–26 & `gate4_speaker`**:<br>• `PIN_AMP_BCLK` = GPIO 15<br>• `PIN_AMP_LRC` = GPIO 16<br>• `PIN_AMP_DIN` = GPIO 7<br>• `GAIN`: Leave **unconnected / floating** for standard **9 dB** default (or 100kΩ to VIN for 3 dB, direct to VIN for 6 dB).<br>• `SD` (SD_MODE): Check your exact breakout board's pull-up arrangement before wiring. On breakouts with an onboard pull-up resistor to VIN, leaving `SD` unconnected keeps the amp enabled. If your module lacks an onboard pull-up or outputs no sound when floating, tie `SD` to `VIN` through a 100 kΩ resistor.<br>• Connect `VIN` to **`5V` (`VBUS`)**. Add `1000 µF 25V/16V` capacitor across `5V–GND` rail to help cushion supply dips during audio peaks. |
| **4. Miniature Speaker** | **8Ω 2W** (Preferred) or **8Ω 3W** miniature speaker (28–40 mm diameter) | • [Mini 2-Inch 8 Ohm 3W Speaker Driver](https://www.amazon.in/dp/B0HNGZX1KV)<br>• *Budget Alternate:* [Electronic Spices 40mm 8 Ohm Speaker](https://www.amazon.in/dp/B07MTK843Z) | ₹106 – ₹290 | 🔍 **Power & Impedance Notes (`gate4_speaker` & `robot_main`)**:<br>• An 8 Ω speaker is recommended as a sensible conservative load for USB-powered prototypes compared to 4 Ω. Current draw depends on volume setting and audio dynamics, but 8 Ω presents a gentler load on the supply rail.<br>• Directly wire speaker terminals to the `+` and `-` outputs of the MAX98357A. |
| **5. IPS SPI Color Display** | **ST7789** 1.3" or 1.54" **240×240 IPS TFT** (SPI Interface, 7-pin or 8-pin) | • **Recommended (7-Pin):** [1.3" 240×240 RGB TFT IPS Module (7-Pin ST7789)](https://www.amazon.in/dp/B0CTQXNQWY)<br>• *Alternate (8-Pin):* [1.54" SPI TFT LCD 240x240 RGB 8-Pin ST7789](https://www.amazon.in/dp/B0DFWL5Q9F) | ₹580 – ₹799 | 🔍 **Pinout & Configuration (`include/pins.h` lines 30–43 & `gate2_display`)**:<br>• `PIN_TFT_MOSI` = GPIO 11 (SDA), `PIN_TFT_SCLK` = GPIO 12 (SCL)<br>• `PIN_TFT_DC` = GPIO 9, `PIN_TFT_RST` = GPIO 14, `PIN_TFT_BL` = GPIO 13<br>• **7-Pin display (no CS pin)**: Matches current code default (`#define TFT_HAS_CS_PIN 0`, SPI Mode 3, `pin_cs = -1`).<br>• **8-Pin display (with CS)**: Requires changing line 30 in `include/pins.h` to `#define TFT_HAS_CS_PIN 1` (SPI Mode 0, `pin_cs = 10`) and wiring `CS` to `GPIO 10`. Verify module SPI mode compatibility.<br>• Power from **3.3V ONLY**. |
| **6. Prototyping Essentials** | **830-Point Breadboard** + Dupont Wires + Tactile Button + 1000µF & 0.1µF Capacitors | • **Breadboard & Wires:** [OSFT 830 Point Breadboard with 30-pc Jumper Wire Kit](https://www.amazon.in/dp/B0FRGB33KB)<br>• **Passives & Filtering Pack:** [Robodo 1000uF 25V Electrolytic Capacitor (Pack of 10)](https://www.amazon.in/dp/B09VL23ZP1) + 6x6mm Tactile Buttons / 0.1µF Ceramic Pack | ₹249 + ₹149 | 🔍 **Matches `include/pins.h` line 50 & Audio Filtering**:<br>• **1× 1000 µF 25V/16V electrolytic capacitor** across breadboard `5V–GND` rail (recommended to help reduce supply dips during audio peaks; does not guarantee immunity against high cable resistance or strict host port limits).<br>• **1× 0.1 µF ceramic capacitor** across `INMP441` `VDD–GND` (decouples 2.4 GHz Wi-Fi RF noise).<br>• **Tactile pushbutton** connects `PIN_PTT_BTN` (GPIO 1) to `GND` (uses internal `INPUT_PULLUP`). |
| **7. Basic Soldering Kit** | **25W–60W Soldering Iron** + 60/40 rosin-core solder wire + flux paste | • [Aptechdeals Soldering Kit Beginner 6-in-1](https://www.amazon.in/dp/B07D1PQ7TM)<br>• *Alternate:* [Themisto Beginners 8-in-1 Soldering Iron Kit](https://www.amazon.in/dp/B07PM6134P) | ₹220 – ₹242 | 🔍 **Physical Reliability Guardrail**:<br>• `INMP441` and `MAX98357A` ship with loose header pins. Solder the headers before inserting into the breadboard to eliminate I²S clock jitter and intermittent reboot loops.<br>• Solder joints should look like clean, shiny little cones.<br>💡 *Zero-iron option*: A local mobile phone repair shop will solder all 3 headers for ₹50 in 2 minutes. |

---

### 💰 Total Kit Cost Estimate

| Item | Selected Product | Cost (₹) |
| :--- | :--- | :---: |
| **ESP32-S3 N16R8 Board** | [VIREXON ESP32-S3 N16R8 (16MB Flash, 8MB PSRAM)](https://www.amazon.in/dp/B0H94PHFSW) | ₹945 |
| **Audio Modules Combo** | [MAX98357A I²S Amp + INMP441 Mic Audio Combo](https://www.amazon.in/dp/B0H3M78HQV) *(or separately ₹199 + ₹249)* | ₹407 |
| **8Ω 3W Speaker** | [Mini 2-Inch 8 Ohm 3W Speaker Driver](https://www.amazon.in/dp/B0HNGZX1KV) | ₹290 |
| **ST7789 IPS Display** | [1.3-inch 240×240 RGB TFT IPS LCD Module (7-Pin ST7789)](https://www.amazon.in/dp/B0CTQXNQWY) | ₹580 |
| **830-Pt Breadboard Kit** | [OSFT 830 Point Breadboard + 30-pc Jumper Wires](https://www.amazon.in/dp/B0FRGB33KB) | ₹249 |
| **Passives Filtering Pack**| [Robodo 1000uF 25V Electrolytic Capacitors (Pack of 10)](https://www.amazon.in/dp/B09VL23ZP1) + Buttons/Caps | ₹149 |
| **Soldering Iron Kit** | [Aptechdeals Soldering Kit Beginner 6-in-1](https://www.amazon.in/dp/B07D1PQ7TM) | ₹242 |
| **TOTAL** | **Estimated Hardware Component Cost (Amazon.in Listings)** | **~₹2,862 INR** |

---

### 📋 4-Step Pre-Flight Checklist (When Your Packages Arrive)

Before plugging anything into USB or power, verify these 4 items:
1. **Verify the ESP32-S3 Metal Can**: Look closely at the laser engraving on the metal shield. It **must** read `ESP32-S3-WROOM-1-N16R8` (preferred) or `N8R8` (8 MB Octal PSRAM).
   * ⚠️ **Flash Setting for `N8R8`**: The project defaults to `16MB` flash (`default_16MB.csv`). If your board is an `N8R8` (8 MB Flash), you **must** change `platformio.ini` lines 19–20 to `board_upload.flash_size = 8MB` and `board_build.partitions = default_8MB.csv` before uploading, or the chip will continuously bootloop!
   * If the shield reads `N4` (0 PSRAM) or `ESP32-WROOM-32` (old ESP32), initiate an immediate return.
2. **Solder the Header Pins**: Solder the 6-pin header on the `INMP441` and the 7-pin header on the `MAX98357A`. Solder joints should look like clean, shiny little cones.
3. **Power Rails Grounding**: Double check that `INMP441` and `ST7789` are plugged into the **3.3V** rail, and `MAX98357A` is plugged into the **5V (`VBUS`)** rail with the `1000 µF` capacitor installed across `5V` and `GND`.
4. **USB Port Selection**: When connecting to your PC/laptop, plug into the USB-C port labeled **`USB`** (native USB CDC on GPIO 19/20). The port labeled `UART` will not display the runtime serial logs.

---

## 📂 3. Purpose of Every File in This Repository

```text
RoboAssistant/
├── README.md                        # Complete How-To Guide & file-by-file reference (this file)
├── ROADMAP.md                       # Master BOM (parts list), pin map, and 12-Gate engineering checklist
├── platformio.ini                   # Pinned PlatformIO build config (espressif32 @ 6.9.0) with 8 environments
├── .gitignore                       # Blocks build artifacts, audio scratch files, and any secret files from Git
│
├── scripts/
│   └── load_secrets_inc.py          # Pre-build script that auto-discovers ~/.robo_secrets across any OS/user
│
├── include/
│   ├── pins.h                       # Single source of truth for all ESP32-S3 GPIOs & hardware toggles
│   ├── certs.h                      # Multi-root TLS CA bundle (ISRG Root X2, Root YE, ISRG Root X1, GlobalSign R4)
│   └── secrets.h.example            # Template showing what goes inside ~/.robo_secrets/robot_secrets.h
│
├── src/
│   ├── gate1_board/main.cpp         # Gate 1: Verifies ESP32-S3 chip, 16 MB Flash, and 8 MB Octal PSRAM R/W
│   ├── gate2_display/main.cpp       # Gate 2: Verifies ST7789 240x240 TFT & 30+ FPS PSRAM blinking eye sprite
│   ├── gate3_mic/main.cpp           # Gate 3: Verifies INMP441 I2S mic (16 kHz), DC-blocker, and Serial Plotter RMS
│   ├── gate4_speaker/main.cpp       # Gate 4: Verifies MAX98357A amp + speaker (24 kHz stereo [L,L], zero brownout)
│   ├── gate5_loopback/main.cpp      # Gate 5: Records 3s from mic into PSRAM on GPIO 1 hold & plays it back locally
│   ├── gate6_health/main.cpp        # Gate 6: Verifies 2.4 GHz Wi-Fi, NTP clock sync, and TLS GET /health
│   ├── gate7_echo/main.cpp          # Gate 7: Streams 16 kHz mic audio to POST /echo & plays back chunked response
│   └── robot_main/
│       ├── main.cpp                 # Gates 9–12 + VAD: Dual-core robot (Hands-Free VAD + PTT + Face + Direct/Gateway)
│       ├── direct_mode.h            # Direct On-Chip pipeline (ESP32 PSRAM -> Gemini STT -> Gemini/Chit LLM -> Gemini TTS)
│       └── robot_config.h           # Live NVS Flash config store + authenticated GET/POST http://<robot-ip>/config API
│
└── gateway/
    ├── app.py                       # FastAPI server exposing /health, /echo, and /talk with per-device sessions
    ├── adapters.py                  # Pluggable async adapters for Gemini, Chit (api.chitt.online), OpenAI, and Mock
    ├── requirements.txt             # Lightweight Python dependencies (fastapi, uvicorn, httpx, python-dotenv)
    ├── .env.example                 # Template showing what goes inside ~/.robo_secrets/gateway.env
    ├── test_gateway.py              # Offline unit & fault-injection test suite (auth, odd-byte, 504/502, mid-stream, Chit)
    ├── test_live_gemini.py          # Live end-to-end test using your real Gemini key (plays voice on laptop speakers)
    ├── Containerfile                # Lightweight Python 3.13 container build file for AWS EC2 deployment
    └── DEPLOY.md                    # Step-by-step instructions to deploy alongside Chit behind Caddy on AWS EC2
```

### Detailed File Reference Table

| File Path | Purpose & When You Touch It |
| :--- | :--- |
| [`README.md`](README.md) | Step-by-step setup, wiring, flashing, and operational guide. |
| [`ROADMAP.md`](ROADMAP.md) | Shopping list (Phase 0 BOM with ₹ prices and soldering notes), forbidden ESP32-S3 pins, and pass/fail criteria for all 12 verification gates. |
| [`platformio.ini`](platformio.ini) | Pins `espressif32 @ 6.9.0` (Arduino-ESP32 v2.0.17 legacy I²S driver) and defines 8 isolated build targets (`gate1_board` through `robot_main`). Edit lines 19–20 only if you buy an `N8R8` (8 MB Flash) board instead of `N16R8` (16 MB Flash). |
| [`scripts/load_secrets_inc.py`](scripts/load_secrets_inc.py) | Automatically adds `~/.robo_secrets` (or `$ROBO_SECRETS_DIR`) to the C++ include path at compile time so no Windows username is ever hardcoded in `platformio.ini`. |
| [`include/pins.h`](include/pins.h) | Defines every GPIO pin (`INMP441`, `MAX98357A`, `ST7789`, `GPIO 1` button, `GPIO 18` servo) plus three 1-line toggles: `TFT_HAS_CS_PIN` (`0` for standard 7-pin CS-less display default, `1` for 8-pin display), `ENABLE_AUTO_VAD_DEFAULT` (`1` for hands-free listening on boot), and `ENABLE_SERVO_ARM` (`0` until external 5V 2A power is attached). |
| [`include/certs.h`](include/certs.h) | Pinned Root CA certificates (`ISRG Root X2`, `ISRG Root YE`, `ISRG Root X1`, `GlobalSign ECC Root CA - R4`) used by `WiFiClientSecure` so HTTPS never uses `setInsecure()`. |
| [`include/secrets.h.example`](include/secrets.h.example) | Safe example template for `~/.robo_secrets/robot_secrets.h`. |
| [`src/gate1_board/main.cpp`](src/gate1_board/main.cpp) | **Hardware Test 1**: Prints chip model, Flash size, and PSRAM size, and runs a 1 MB R/W verification across Octal PSRAM. Run this immediately when your ESP32-S3 arrives. |
| [`src/gate2_display/main.cpp`](src/gate2_display/main.cpp) | **Hardware Test 2**: Initializes the ST7789 240×240 display via LovyanGFX and animates blinking cyan eyes from a PSRAM sprite at ~33 FPS. |
| [`src/gate3_mic/main.cpp`](src/gate3_mic/main.cpp) | **Hardware Test 3**: Reads 16 kHz 32-bit I²S samples from `INMP441` on `I2S_NUM_0`, removes DC offset via a fixed-point IIR filter, and prints live RMS + peak audio levels for the Serial Plotter. |
| [`src/gate4_speaker/main.cpp`](src/gate4_speaker/main.cpp) | **Hardware Test 4**: Plays a 440 Hz tone and two-note chime at 24 kHz stereo `[L, L]` on `MAX98357A` (`I2S_NUM_1`) with click-free attack/release envelopes to verify USB power stability. |
| [`src/gate5_loopback/main.cpp`](src/gate5_loopback/main.cpp) | **Hardware Test 5**: Offline voice recorder—hold `GPIO 1` to record up to 3 seconds into PSRAM (`96 KB`), release to hear your own voice played back through the speaker. |
| [`src/gate6_health/main.cpp`](src/gate6_health/main.cpp) | **Network Test 6**: Connects to 2.4 GHz Wi-Fi, syncs the ESP32 clock via NTP (required for TLS certificate validation), and calls `GET /health` every 10 seconds while monitoring free heap. |
| [`src/gate7_echo/main.cpp`](src/gate7_echo/main.cpp) | **Network Test 7**: Sends recorded 16 kHz PCM audio to `POST /echo` and streams the chunked 16 kHz response back through the 400 ms PSRAM pre-buffer + 1-byte odd-chunk alignment guard. |
| [`src/robot_main/main.cpp`](src/robot_main/main.cpp) | **Production Firmware**: Full dual-core voice robot combining Core 0 30 FPS eye expressions (`BOOT`, `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `ERROR`) with Core 1 Hands-Free Always-Listening Energy VAD, `GPIO 1` Push-to-Talk override, and runtime dispatch between Direct On-Chip Mode and Gateway Mode. |
| [`src/robot_main/direct_mode.h`](src/robot_main/direct_mode.h) | **Zero-Hop Direct Cloud Engine**: Uses 8 MB Octal PSRAM buffers (`g_directWavPsram`, `g_directReqPsram`, `g_directRespPsram`, `g_directPcmPsram`) + `mbedtls_base64` to call **Gemini STT**, **Gemini or Chit LLM**, and **Gemini TTS** directly from the ESP32-S3 over verified TLS (`GTS Root R4`/`R1` & `ISRG`). |
| [`src/robot_main/robot_config.h`](src/robot_main/robot_config.h) | **Live Over-the-Air Config API (`GET/POST /config`)**: Persists `mode`, `llm_provider`, `vad_enabled`, models, voice, URLs, and API keys in ESP32 NVS Flash (`Preferences`), protected by Bearer authentication and constant-time token comparison, with secrets masked on `GET /config`. |
| [`gateway/app.py`](gateway/app.py) | FastAPI server implementing `GET /health`, `POST /echo`, and `POST /talk` (plus `/voice/*` aliases for Caddy), Bearer authentication, per-device LRU session history (`X-Session-Id`), and pre-stream (`504`/`502`) + mid-stream exception guards. |
| [`gateway/adapters.py`](gateway/adapters.py) | Implements `GeminiSTT`, `GeminiLLM`, `GeminiTTS` (`gemini-3.1-flash-lite` + `gemini-3.8-flash-tts`), `ChitLLM` (`POST /chat` with `X-API-Key`, `session_id` reuse, and `429` exponential backoff), `OpenAICompat*`, and `Mock*` adapters. |
| [`gateway/test_gateway.py`](gateway/test_gateway.py) | Automated test suite verifying auth rejection (`401`), `/echo` odd-chunk streaming, `<400 ms` short-press filtering (`204`), per-device session isolation, odd-byte input alignment, upstream `504`/`502` handling, mid-stream LLM/TTS fault recovery, and the `ChitLLM` contract. |
| [`gateway/test_live_gemini.py`](gateway/test_live_gemini.py) | Live end-to-end test that calls Google's Gemini API with your key, plays the synthesized 24 kHz robot voice on your laptop speakers, and tests the full HTTP `POST /talk` pipeline. |
| [`gateway/Containerfile`](gateway/Containerfile) | Docker/Podman build file for running the Gateway as a non-root user on your AWS EC2 `t4g.small` instance. |
| [`gateway/DEPLOY.md`](gateway/DEPLOY.md) | Step-by-step guide for local laptop execution and AWS EC2 (`t4g.small`) Docker + Caddy deployment alongside **Chit**. |

---

## 🛠️ 4. Complete Step-by-Step "How-To" Guide

### Step 1 — Create Your Outside-Repo Secrets (`~/.robo_secrets/`)

Create a folder named `.robo_secrets` in your user home directory (`C:\Users\<YourUsername>\.robo_secrets\` on Windows, or `~/.robo_secrets/` on macOS/Linux) containing two files:

1. **`~/.robo_secrets/gateway.env`** (copied from [`gateway/.env.example`](gateway/.env.example)):
   ```ini
   GATEWAY_BEARER_TOKEN=your-random-32-byte-hex-token
   STT_PROVIDER=gemini
   LLM_PROVIDER=gemini
   TTS_PROVIDER=gemini

   GEMINI_API_KEY=your-google-ai-studio-api-key
   GEMINI_STT_MODEL=gemini-3.1-flash-lite
   GEMINI_LLM_MODEL=gemini-3.1-flash-lite
   GEMINI_TTS_MODEL=gemini-3.8-flash-tts
   GEMINI_TTS_VOICE=Puck

   CHIT_BASE_URL=https://api.chitt.online
   CHIT_API_KEY=your-chit-api-key
   CHIT_THINK=false
   ```

2. **`~/.robo_secrets/robot_secrets.h`** (copied from [`include/secrets.h.example`](include/secrets.h.example)):
   ```cpp
   #pragma once
   #define WIFI_SSID             "YOUR_2.4GHZ_WIFI_NAME"
   #define WIFI_PASSWORD         "YOUR_WIFI_PASSWORD"

   // Gateway Server settings (used in Gateway Mode and in Gates 6-7):
   #define GATEWAY_BASE_URL      "http://192.168.1.40:8002"
   #define GATEWAY_BEARER_TOKEN  "your-random-32-byte-hex-token"

   // Operating mode & Direct On-Chip cloud credentials:
   #define ROBOT_DEFAULT_MODE    "direct"   // "direct" (no Gateway hop) or "gateway"
   #define DIRECT_GEMINI_API_KEY "your-google-ai-studio-api-key"
   #define DIRECT_CHIT_API_KEY   ""         // optional custom Chit API key
   ```
   *(Make sure `GATEWAY_BEARER_TOKEN` is identical in both files!)*

---

### Step 2 — Test & Run the Voice Gateway on Your Laptop (Zero Hardware Needed)

1. Install Python dependencies:
   ```powershell
   cd c:\Robo\gateway
   pip install -r requirements.txt
   ```
2. Run the offline unit & fault-injection test suite (9 tests):
   ```powershell
   python test_gateway.py
   ```
3. Run the live Gemini voice test (speaks aloud through your laptop speakers and tests `POST /talk`):
   ```powershell
   python test_live_gemini.py
   ```
4. Start the live Voice Gateway server on port `8002`:
   ```powershell
   uvicorn app:app --host 0.0.0.0 --port 8002
   ```

---

### Step 3 — Wire the Hardware on the Breadboard

> **Important Electrical Rules**:
> * Power **`INMP441`** and **`ST7789`** from **`3.3V`** only.
> * Power **`MAX98357A`** from **`5V` (`VBUS`)** and place a **`1000 µF` electrolytic capacitor** across the breadboard `5V` and `GND` rails (watch polarity: `-` stripe to `GND`).
> * Place a **`0.1 µF` ceramic capacitor** right next to the `INMP441` `VDD` and `GND` pins.
> * Connect the USB-C cable to the ESP32-S3 port labeled **`USB`** (native USB CDC on GPIO 19/20).

| Module | Module Pin | ESP32-S3 Pin | Notes |
| :--- | :--- | :--- | :--- |
| **INMP441 Mic** | `VDD` / `GND` | `3.3V` / `GND` | Add `0.1 µF` cap across `VDD–GND` |
| | `L/R` | `GND` | Selects Left channel |
| | `SCK` (BCLK) | `GPIO 4` | `I2S_NUM_0` clock |
| | `WS` (LRCLK) | `GPIO 5` | `I2S_NUM_0` word select |
| | `SD` (DOUT) | `GPIO 6` | `I2S_NUM_0` data in |
| **MAX98357A Amp** | `VIN` / `GND` | `5V` / `GND` | Add `1000 µF` cap across `5V–GND` |
| | `GAIN` | Floating (or `VIN`) | Leave floating for **9 dB default** (or wire direct to `VIN` for **6 dB**, 100kΩ to `VIN` for **3 dB**) |
| | `SD` (SD_MODE) | Floating (or 100kΩ to `VIN`) | Check breakout pull-up: leave floating if board has onboard pull-up to `VIN`; if module lacks pull-up or remains muted, connect `SD` to `VIN` through a 100 kΩ resistor. Firmware outputs stereo `[L, L]` frames. |
| | `BCLK` | `GPIO 15` | `I2S_NUM_1` bit clock |
| | `LRC` | `GPIO 16` | `I2S_NUM_1` word select |
| | `DIN` | `GPIO 7` | `I2S_NUM_1` data out |
| **ST7789 Display** | `VCC` / `GND` | `3.3V` / `GND` | 240×240 IPS SPI |
| | `SDA` (MOSI) | `GPIO 11` | `SPI2_HOST` MOSI |
| | `SCL` (SCLK) | `GPIO 12` | `SPI2_HOST` SCLK |
| | `CS` (8-Pin only) | `GPIO 10` | Standard 7-Pin display has no `CS` (`TFT_HAS_CS_PIN 0` default). If using 8-Pin, wire to `GPIO 10` and set `TFT_HAS_CS_PIN 1`. |
| | `DC` | `GPIO 9` | Data/Command |
| | `RST` (RES) | `GPIO 14` | Reset |
| | `BLK` (BL) | `GPIO 13` | Backlight PWM |
| **PTT / Mode Button** | Pin A / Pin B | `GPIO 1` / `GND` | Uses internal pull-up (`INPUT_PULLUP`) |
| **Servo Arm (Optional)** | `Signal` | `GPIO 18` | Power servo `VCC` from external 5V 2A supply (common `GND`) |

---

### Step 4 — Compile, Flash & Verify Each Hardware Gate in Order

Run all commands from `c:\Robo`. **Never skip ahead**—verify each gate before flashing the next:

```powershell
# 1. Pre-compile all 8 environments at once (optional sanity check):
python -m platformio run -e gate1_board -e gate2_display -e gate3_mic -e gate4_speaker -e gate5_loopback -e gate6_health -e gate7_echo -e robot_main

# 2. Flash Gate 1 (Board, 16MB Flash & 8MB Octal PSRAM check):
python -m platformio run -e gate1_board -t upload -t monitor

# 3. Flash Gate 2 (ST7789 Display & 30+ FPS blinking eye animation):
python -m platformio run -e gate2_display -t upload -t monitor

# 4. Flash Gate 3 (INMP441 Mic capture & DC-offset RMS printout):
python -m platformio run -e gate3_mic -t upload -t monitor

# 5. Flash Gate 4 (MAX98357A Speaker 440Hz tone & chime test):
python -m platformio run -e gate4_speaker -t upload -t monitor

# 6. Flash Gate 5 (Hold GPIO 1 to record 3s to PSRAM -> release to hear playback):
python -m platformio run -e gate5_loopback -t upload -t monitor

# 7. Flash Gate 6 (Wi-Fi + NTP clock sync + Gateway GET /health check):
python -m platformio run -e gate6_health -t upload -t monitor

# 8. Flash Gate 7 (Hold GPIO 1 -> stream 16kHz PCM to POST /echo -> hear echoed voice):
python -m platformio run -e gate7_echo -t upload -t monitor

# 9. Flash Production Robot Firmware (Full Dual-Core Hands-Free VAD + PTT + AI Voice):
python -m platformio run -e robot_main -t upload -t monitor
```

---

### Step 5 — How to Use Your Robot (`robot_main`)

Before booting, ensure your chosen operating mode prerequisites are met:
* **Mode A — Direct On-Chip Cloud Mode (`mode = "direct"`, Default)**:
  - Requires a valid `DIRECT_GEMINI_API_KEY` in `~/.robo_secrets/robot_secrets.h` (or configured live via `POST /config`).
  - **No local gateway needed**: The ESP32 connects directly to Google Gemini APIs over TLS.
* **Mode B — Voice Gateway Mode (`mode = "gateway"`)**:
  - Requires the local FastAPI Voice Gateway running on your laptop (`uvicorn app:app --host 0.0.0.0 --port 8002` from Step 2) with `GATEWAY_BASE_URL` reachable on your LAN.

Once `robot_main` is booted in either mode:
1. **Boot & Calibration (`STATE_BOOT` → `STATE_IDLE`)**:
   * The robot displays amber booting eyes while connecting to Wi-Fi and syncing NTP time, then samples ~0.5 seconds of ambient room noise to calibrate its Hands-Free VAD trigger threshold.
   * When ready, the eyes turn **cyan** and blink naturally (`STATE_IDLE`).
2. **Two Ways to Talk to the Robot**:
   * **Hands-Free Always-Listening Mode (Default ON)**: Just start speaking! When your voice exceeds the calibrated room noise floor for ~48 ms, the eyes turn **wide green (`STATE_LISTENING`)**, prepending the last **250 ms** of pre-roll audio from PSRAM so your first syllable is never cut off. Stop speaking for **900 ms** and the robot automatically processes your voice.
   * **Push-to-Talk Override (`GPIO 1` Hold >= 400 ms)**: Hold down the `GPIO 1` button at any time, speak, and release.
   * **Toggle Hands-Free VAD ON/OFF (`GPIO 1` Quick Tap < 350 ms)**: Tap the `GPIO 1` button briefly while idle to switch between **Hands-Free Always-Listening Mode** and **Push-to-Talk-Only Mode** (recalibrates noise floor on enable).
3. **Thinking & Speaking (`STATE_THINKING` → `STATE_SPEAKING`)**:
   * Eyes turn **amber and scan left-right (`STATE_THINKING`)** while the robot processes your query (Direct Mode: ESP32 calls Gemini STT → LLM → TTS directly; Gateway Mode: streams audio to `/talk` and receives 24 kHz chunks).
   * Once audio is pre-buffered in PSRAM (400 ms), the robot speaks while its **cyan mouth and eyes bounce in sync with voice energy (`STATE_SPEAKING`)**.
   * After speaking, the robot waits **450 ms** and flushes its mic DMA buffer (`flushMicAfterPlayback()`) so it never echoes its own voice.

---

### Step 6 — Change Robot Settings Live Over Wi-Fi (`GET /config` & `POST /config`)

When `robot_main` boots, it prints its local Config API address on Serial (e.g., `http://192.168.1.55/config`). You can inspect or update settings **live from your laptop or phone without re-flashing** (all changes are saved permanently in ESP32 NVS Flash):

1. **Inspect Current Robot Config (API keys are automatically masked as `****xxxx`)**:
   ```powershell
   curl.exe http://<robot-ip>/config -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>"
   ```

2. **Switch to Direct On-Chip Mode (Zero Middleman Hop — Robot Calls Gemini Directly)**:
   ```powershell
   curl.exe -X POST http://<robot-ip>/config `
     -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>" `
     -H "Content-Type: application/json" `
     -d '{"mode":"direct","llm_provider":"gemini","tts_voice":"Puck","vad_enabled":true}'
   ```

3. **Switch the Robot's Brain to Your Custom "Chit" API (`https://api.chitt.online/chat`) in Direct Mode**:
   ```powershell
   curl.exe -X POST http://<robot-ip>/config `
     -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>" `
     -H "Content-Type: application/json" `
     -d '{"mode":"direct","llm_provider":"chit","chit_api_key":"YOUR_CHIT_API_KEY"}'
   ```

4. **Switch to Gateway Mode (`gateway/app.py` for Sentence-by-Sentence Streaming)**:
   ```powershell
   curl.exe -X POST http://<robot-ip>/config `
     -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>" `
     -H "Content-Type: application/json" `
     -d '{"mode":"gateway","gateway_url":"http://192.168.1.40:8002"}'
   ```

5. **Factory Reset NVS Back to Compile-Time Defaults**:
   ```powershell
   curl.exe -X POST http://<robot-ip>/config `
     -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>" `
     -H "Content-Type: application/json" `
     -d '{"factory_reset":true}'
   ```

For deploying the optional Voice Gateway onto your AWS EC2 `t4g.small` instance alongside **Chit** behind Caddy, see [`gateway/DEPLOY.md`](gateway/DEPLOY.md).
