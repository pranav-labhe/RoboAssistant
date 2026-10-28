# 🤖 ESP32-S3 Tabletop Voice Robot — Master Engineering Roadmap & Checklist

## Phase 0: Buy the Right Parts (Target Budget: ~₹2,000–₹2,800)

Order from a seller with a clear return window (e.g., Robu.in, Probots, or verified Amazon.in listings). **Run Gate 1 (PSRAM check) immediately upon arrival.**

| Part | Exact Spec to Insist On | Est. ₹ | Critical Buying & Hardware Notes |
| :--- | :--- | :--- | :--- |
| **ESP32-S3 Dev Board** | Metal shield laser-etched **`ESP32-S3-WROOM-1-N16R8`** (preferred) or **`N8R8`** (8 MB Octal PSRAM) | 850–1,300 | **Must have `R8` (8 MB Octal PSRAM).** `N16R8` uses `16MB` / `default_16MB.csv`; if you buy `N8R8` (8 MB Flash), you **must** change `platformio.ini` to `8MB` / `default_8MB.csv` or it will bootloop! |
| **INMP441 I²S Mic** | 6-pin breakout (`SCK, WS, SD, L/R, VDD, GND`) | 240–400 | Ships with **unsoldered headers**. Since you don't own a soldering iron, either **add a ₹220 25W soldering iron + solder wire kit** to your order, or buy pre-soldered/all-in-one hardware. |
| **MAX98357A Amp** | 7-pin I²S Class-D (`LRC, BCLK, DIN, GAIN, SD, GND, VIN`) | 200–400 | Leave **`GAIN` unconnected (floating)** for standard **9 dB** default (simplest for breadboards). *Official Datasheet: 100kΩ to `VIN` = 3 dB (quietest), direct to `VIN` = 6 dB, floating = 9 dB (default), direct to `GND` = 12 dB, 100kΩ to `GND` = 15 dB (loudest).* |
| **Speaker** | **8Ω 2W** (preferred for USB power) or **4Ω 3W**, 28–40 mm | 100–200 | 8Ω draws half the peak current of 4Ω and sounds cleaner at low power. |
| **ST7789 SPI Display** | 1.3" or 1.54" **240×240 IPS SPI** | 320–500 | **Inspect listing photo for pin labels, not screen size:**<br>• **8-pin (`CS` present)**: Standard `SPI_MODE0`, `pin_cs = 10`.<br>• **7-pin (No `CS` pin)**: Needs `pin_cs = -1` and `spi_mode = 3` in LovyanGFX. |
| **Passives & Breadboard** | 830-pt breadboard, M-M/M-F jumpers, **1000 µF 16V** electrolytic cap, **0.1 µF** ceramic cap, **1× tactile pushbutton** | ~200 | • **1000 µF electrolytic** across `5V–GND` rail (prevents USB brownout resets).<br>• **0.1 µF ceramic** right across `INMP441` `VDD–GND` (kills Wi-Fi whine).<br>• **Breadboard pushbutton** prevents flexing wires when pressing PTT. |
| **Basic Soldering Kit** | **25W–60W soldering iron + 60/40 rosin-core solder wire** (Mandatory for breadboard modules unless buying pre-soldered) | 200–300 | `INMP441`, `MAX98357A`, and `ST7789` ship with loose header pins in the bag. Un-soldered pins cause I²S clock static and dead screens. |

* **Skip for now**: Servo (`SG90`/`MG90S`), external 5V 2A supply, and 3D-printed body. Use a simple cardboard box prototype first.
* **Soldering & Budget Options**:
  * **Keep the Iron on the List (~₹200–₹300 basic 25–40W, or ₹500–₹700 adjustable-temp + rosin-core solder + flux)**: Best long-term investment since you will also need it at the servo stage. Practice 6–8 header joints on scrap first (aim for shiny cone-shaped joints; dull cold joints cause I²S clock static).
  * **Ways to Trim ₹200–₹300 if Needed**: Look for `INMP441` / `MAX98357A` listings explicitly labeled **"Pre-Soldered"** (often ₹20–₹60 extra; many `ESP32-S3 DevKitC` and `ST7789` boards already arrive with headers soldered—verify in listing photos), or pay a local mobile/electronics repair shop ₹50–₹150 to solder the 3 headers for you.

---

## Phase 1: Locked Toolchain (`PlatformIO` + Pinned Arduino-ESP32 v2.x)

* **Why Pin the Platform Version?**
  * Official `espressif32 @ 6.9.0` uses **Arduino-ESP32 v2.0.17** (legacy `driver/i2s.h` API: `i2s_driver_install`, `i2s_read`, `i2s_write`).
  * Arduino-ESP32 v3.x (`pioarduino`) replaces the legacy I²S driver with a completely different API. We explicitly pin `espressif32 @ 6.9.0` in `platformio.ini` so I²S code never breaks across updates.
* **Per-Gate Environments**: Use dedicated environments (`[env:gate1_board]`, `[env:gate2_display]`, etc.) with `build_src_filter` so every hardware test remains runnable in isolation.
* **Dual USB-C Port Rule**: With `-DARDUINO_USB_CDC_ON_BOOT=1`, `Serial` output appears **only** on the port labeled **`USB`** (GPIO 19/20), not the `UART` port.

---

## Phase 2: Conflict-Free Pin Map (`include/pins.h`)

**Forbidden Pins on `ESP32-S3-N16R8`**:
* **GPIO 19, 20**: Native USB D-/D+
* **GPIO 26–37**: Internal Octal Flash & Octal PSRAM bus (touching GPIO 35–37 crashes immediately)
* **GPIO 0, 3, 45, 46**: Boot-strapping pins (holding `GPIO 0` low across a reset traps the chip in ROM bootloader mode)

| Subsystem | Peripheral | Module Pin | ESP32-S3 Pin | Power Rail | Electrical & Software Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **INMP441 Mic** | `I2S_NUM_0` (RX) | `SCK` (BCLK) | `GPIO 4` | **3.3V ONLY** | Place `0.1 µF` ceramic cap across `VDD–GND`. |
| | | `WS` (LRCLK) | `GPIO 5` | | |
| | | `SD` (DOUT) | `GPIO 6` | | |
| | | `L/R` | `GND` | | Selects Left channel. |
| **MAX98357A Amp** | `I2S_NUM_1` (TX) | `BCLK` | `GPIO 15` | **5V (VBUS)** | Place `1000 µF` cap across `5V–GND` rail. |
| | | `LRC` | `GPIO 16` | | |
| | | `DIN` | `GPIO 7` | | |
| | | `GAIN` | Floating (or `VIN`) | | Leave floating for **9 dB default** (or wire direct to `VIN` for 6 dB, 100kΩ to `VIN` for 3 dB). |
| | | `SD` | Floating | | Send stereo-duplicated `[L, L]` frames in software so any clone works. |
| **ST7789 Display** | `SPI2_HOST` | `MOSI` (SDA) | `GPIO 11` | **3.3V** | 40 MHz SPI clock. |
| | | `SCLK` (SCL) | `GPIO 12` | | |
| | | `CS` | `GPIO 10` (or `-1`) | | Set `-1` and `spi_mode = 3` if using a 7-pin CS-less module. |
| | | `DC` | `GPIO 9` | | |
| | | `RST` | `GPIO 14` | | |
| | | `BL` (BLK) | `GPIO 13` | | |
| **Push-to-Talk** | Digital In | Button Pin A | **`GPIO 1`** | `GND` (Pin B) | Configured as `INPUT_PULLUP` (active LOW) with 30 ms debounce. Safe across resets. |
| **Waving Servo** | `LEDC PWM` | `Signal` | `GPIO 18` | **Ext 5V 2A** | Added only after Phase 3 completes. |

---

## Phase 3: The 12-Gate Build & Verification Protocol

**Hard Rule**: Do not advance to Gate $N+1$ until Gate $N$ passes its verification criteria.

### Gate 1 — Board & PSRAM Check (`[env:gate1_board]`)
* **Test**: Boot via the `USB` port, print `ESP.getChipModel()`, `ESP.getFlashChipSize()`, `ESP.getPsramSize()`, and `ESP.getFreePsram()`, and run the 1 MB R/W pattern test.
* **Pass Gate**: Serial prints `Flash: 16 MB` (or `8 MB` for `N8R8`) and `PSRAM: ~8 MB`.
* **Failure Diagnosis**:
  * **Continuous Bootloop at startup**: Wrong flash size (`16MB` config on an `8MB` `N8R8` board) OR wrong PSRAM bus mode (`qio_opi` fails during early PSRAM init on Quad-PSRAM `R2`/`R4` or 0-PSRAM boards and looks like a dead/bootlooping board).
  * **Prints `PSRAM: 0`**: Missing `-DBOARD_HAS_PSRAM` flag or board has no PSRAM—return the board.

### Gate 2 — Display & Sprite Engine (`[env:gate2_display]`)
* **Test**: Configure `LovyanGFX` for your exact ST7789 variant (8-pin `SPI_MODE0` vs. 7-pin `SPI_MODE3`). Allocate off-screen eye sprites in PSRAM and animate blinking eyes.
* **Optimization**: Full 240×240×16-bit push at 40 MHz SPI takes ~23 ms (~40 FPS theoretical ceiling). Push only dirty/eye bounding boxes (`~240×100`) to easily sustain 30+ FPS with minimal SPI bus time.
* **Pass Gate**: Crisp rendering, zero tearing or flicker, sustained **25+ FPS** printed on Serial.

### Gate 3 — Microphone & DC-Offset Removal (`[env:gate3_mic]`)
* **Test**: Run `I2S_NUM_0` at **16 kHz**, 32-bit slots, Left channel (`I2S_CHANNEL_FMT_ONLY_LEFT`).
* **Signal Chain**: Shift raw 32-bit signed words right (`sample >> 11` or `>> 12`) to fit `int16_t` with headroom, apply a lightweight IIR DC-blocking filter, and print live RMS energy at 20 Hz to the Serial Plotter.
* **Pass Gate**: Flat baseline (< 150 RMS) in a quiet room; clear spike (> 1,500 RMS) when speaking at 30–50 cm.

### Gate 4 — Speaker & Power Stability (`[env:gate4_speaker]`)
* **Test**: Run `I2S_NUM_1` at **24 kHz**, 16-bit stereo slots (`I2S_CHANNEL_FMT_RIGHT_LEFT`), with `tx_desc_auto_clear = true`. Duplicate each mono sample into both `[Left, Right]` slots (guarantees audio regardless of how clone `MAX98357A` boards wire their `SD` pin). Play a 440 Hz sine wave and a 1-second PCM chime at 30% software volume, then call `i2s_zero_dma_buffer(I2S_NUM_1)`.
* **Pass Gate**: Clean tone with **zero brownout resets** and zero trailing DMA buzz on Dell USB power.

### Gate 5 — PSRAM Record & Playback Loopback (`[env:gate5_loopback]`)
* **Test**: Hold the breadboard PTT button (`GPIO 1`), capture up to 3 seconds of 16 kHz mono PCM from `INMP441` into a `ps_malloc()` buffer (`96 KB`), switch `I2S_NUM_1` clock to 16 kHz via `i2s_set_clk()`, play it back as stereo `[L, L]` frames through `MAX98357A`, and call `i2s_zero_dma_buffer(I2S_NUM_1)` on exit.
* **Pass Gate**: Your recorded voice plays back clearly at normal pitch with no clicks, static, or hum.

### Gate 6 — NTP Time Sync, Gateway Health & Verified TLS (`GET /health`)
* **Test**: Connect to 2.4 GHz Wi-Fi, call `WiFi.setSleep(false)`, **sync system clock via NTP (`configTime(0, 0, "pool.ntp.org", "time.cloudflare.com")` and wait until `time(nullptr) > 1700000000` so TLS certificate `notBefore` checks don't fail at 1970)**, and make an authenticated `GET https://api.chitt.online/voice/health` (or `/health`) request using `WiFiClientSecure`.
* **Security Rule**: Inspect your gateway domain's live certificate chain (`openssl s_client -connect api.chitt.online:443 -showcerts` — Caddy Let's Encrypt chains to **ISRG Root X2** and **ISRG Root X1**) and pin both roots or use the certificate bundle. **Never use `setInsecure()` in normal operation, and never silently fall back to `setInsecure()` on a TLS error—show the `ERROR` face instead.**
* **Pass Gate**: NTP time synced, `200 OK` received; `ESP.getFreeHeap()` returns to within 1 KB of baseline after connection close.

### Gate 7 — Raw 16 kHz PCM Audio Echo Over HTTPS (`POST /echo`)
* **Test**: Hold PTT (`GPIO 1`), record 16 kHz, 16-bit mono PCM into PSRAM, and `POST` the raw PCM bytes in 4 KB chunks (`Content-Type: audio/pcm;rate=16000`) to `/echo`. The gateway streams the exact 16 kHz PCM bytes back in chunks with header `X-Sample-Rate: 16000` (zero resampling; no `audioop`, which was removed in Python 3.13).
* **Streaming & Pre-Buffer Guardrail**:
  1. Read `X-Sample-Rate` from the HTTP response headers and call `i2s_set_clk(I2S_NUM_1, sampleRate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO)`.
  2. Use `HTTPClient::writeToStream(&i2sPcmSink)` to decode `Transfer-Encoding: chunked` (used by both `StreamingResponse` and Caddy).
  3. Inside `i2sPcmSink::write()`, hold back a **1-byte carryover** on odd-length network chunks, buffer **300–500 ms of PCM in a PSRAM ring buffer** before starting playback to prevent underruns, duplicate `[L, R]`, feed `i2s_write(I2S_NUM_1, ...)`, and call `i2s_zero_dma_buffer(I2S_NUM_1)` on exit.
* **Pass Gate**: Full HTTPS chunked-transfer + odd-byte guard + audio round-trip works cleanly at 16 kHz with zero AI involved.

### Gate 8 — Provider-Agnostic Gateway Pipeline (`STT → LLM → TTS`, Tested via `curl` First)
* **Architecture**: The ESP32-S3 **never** talks to OpenAI or Gemini directly and holds zero third-party API keys. It talks **only** to your thin Python Gateway (`gateway/`), which runs on your laptop during development and on your AWS EC2 `t4g.small` (`2 GiB RAM, 2 vCPU`, ARM64) behind Caddy alongside **Chit (`pranav.chit.api` on port 8000)**.
* **Three Mix-and-Match Adapters (Selected via `.env`: `STT_PROVIDER`, `LLM_PROVIDER`, `TTS_PROVIDER`)**:
  ```python
  class STT: async def transcribe(self, pcm16k: bytes) -> str
  class LLM: async def reply(self, history, text: str) -> AsyncIterator[str]  # streams sentences
  class TTS: async def speak(self, text: str) -> AsyncIterator[bytes]         # yields 24 kHz s16le mono
  ```
  * **Chit LLM Adapter (`LLM_PROVIDER=chit`)**: Calls your Chit API (`POST /chat` with header `X-API-Key: $CHIT_API_KEY` and strict JSON body `{"message": text, "session_id": session_id, "think": false}`). Preserves `session_id` across turns, handles `HTTP 429` queue overload with `1s, 2s, 4s` backoff, and splits `response["text"]` into sentences.
  * **Gemini Adapter (`STT_PROVIDER=gemini`, `LLM_PROVIDER=gemini`, `TTS_PROVIDER=gemini`)**: Keeps strict adapter separation (`STT` and `LLM` are separate calls so transcripts are always logged and saved). `GeminiTTS` decodes base64-in-JSON TTS output on the server into raw 24 kHz s16le mono PCM.
  * **OpenAI / OpenAI-Compatible Adapter (`openai`)**: Shared by OpenAI and any `/v1/...` compatible server (`base_url`, `api_key`, `model`).
* **Pass Gate (Before Hardware Arrives)**: Send a 16 kHz PCM file from your laptop via `curl -X POST .../talk --data-binary @test.pcm -D headers.txt -o reply.pcm` and verify `X-Sample-Rate: 24000` and clean 24 kHz audio output.

### Gate 9 — Full Robot Push-to-Talk + Sentence-by-Sentence Streaming (`POST /talk`)
* **Test**: Robot `POST`s recorded 16 kHz PCM from PSRAM to `/talk`. Gateway runs `STT.transcribe()`, pulls Sentence 1 from `LLM.reply()`, sets headers `X-Sample-Rate: 24000` and optional `X-Action` (e.g., `WAVE` decided from Sentence 1—no `X-Reply-Text` in v1 headers; body is pure 24 kHz s16le mono PCM), and streams synthesized PCM chunks sentence-by-sentence.
* **Robot Pre-Buffer**: Robot pre-buffers **300–500 ms of 24 kHz PCM in PSRAM** before starting I²S playback so inter-sentence synthesis pauses never underrun the speaker.
* **Pass Gate**: Robot starts speaking smoothly after Sentence 1 without inter-sentence clicks or underruns.

### Gate 10 — 5-Turn Stability & Heap Leak Audit
* **Test**: Hold PTT (`GPIO 1`) → Speak → Release → Gateway streams reply → Speaker plays → Return to Idle.
* **Pass Gate**: Complete **5 back-to-back exchanges** with zero crashes; internal SRAM free heap and PSRAM return to baseline after every turn.

### Gate 11 — Dual-Core Face State Machine
* **Architecture**:
  * **Core 0 (`DisplayTask`, priority 1)**: Reads `RobotState` + current audio amplitude (for mouth/eye bounce during `SPEAKING`), renders non-blocking ~30 FPS partial-redraw eye animations via SPI DMA, and yields every frame (`vTaskDelay(33ms)`) to Core 0 Wi-Fi/LwIP tasks.
  * **Core 1 (`loop()` — Audio & Network)**: Handles PTT button polling, Mic capture, HTTPS Gateway requests, PSRAM pre-buffering, and I²S speaker streaming. Updates the volatile `RobotState` enum (`BOOT`, `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `ERROR`).
* **Pass Gate**: Eyes animate smoothly throughout HTTPS TLS handshakes and audio playback without causing I²S buffer underruns.

### Gate 12 — Edge-Case Polish & Fault Recovery
* **Guardrails**:
  * Enforce an **8-second max recording cap** in PSRAM (`256 KB`).
  * Discard button presses shorter than **400 ms** locally (zero network call on accidental taps).
  * Handle **HTTP 401, 429, 5xx, and socket timeouts** cleanly: display a `?` or sad face for 2 seconds, enforce a **0 automatic retry policy** on the robot so a fault never loops, and auto-reconnect Wi-Fi if dropped.

---

## Phase 4: Security, API Keys & Server Guardrails

1. **Zero Cloud Keys on the Chip**: The ESP32-S3 stores only `GATEWAY_BASE_URL` and `GATEWAY_BEARER_TOKEN` in `include/secrets.h` (git-ignored). All provider keys (`CHIT_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) stay in `.env` on your gateway server.
2. **2 GiB RAM EC2 (`t4g.small`) Guardrails**: Keep the Gateway dependencies strictly to `fastapi`, `uvicorn`, `httpx`, and Python 3.13 standard library (`wave`, `base64`, `struct`—never `audioop`). Run with a Docker memory limit (`--memory=192m`), verify swap is active on the EC2 host, and measure actual footprint with `docker stats` since Chit (`pranav.chit.api` + `pranav.chit.ui`) shares the 2 GiB instance.
3. **Stay on Push-to-Talk (`GPIO 1`) First**: Do not enable continuous VAD or wake-word listening until Gates 1–12 are 100% stable.

---

## Phase 5: Later Extras (In Order)
1. **Local Wake-Word + Half-Duplex VAD**: Use ESP-SR (`WakeNet`) or energy VAD with a 400 ms post-playback deaf cooldown + DMA RX flush so the robot never hears its own voice.
2. **Servo Waving Arm (`GPIO 18`)**: Powered from an external **5V 2A wall supply** (common `GND` with ESP32-S3, never from laptop USB 5V).
3. **3D-Printed Enclosure**: Move from the cardboard prototype to the MakerWorld OttoRobot / XiaoZhi shell.
