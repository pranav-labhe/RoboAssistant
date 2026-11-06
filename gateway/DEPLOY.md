# 🚀 ESP32-S3 Voice Robot — Deployment & Local Testbench Guide

> [!NOTE]
> **Architecture Reality (100% Onboard)**:
> The tabletop voice robot runs **entirely onboard** within the ESP32-S3 microcontroller ([`src/robot_main/direct_mode.h`](../src/robot_main/direct_mode.h)). It captures audio from the INMP441 mic, connects directly to Google Gemini over Wi-Fi using verified TLS, decodes 24 kHz speech in 8 MB Octal PSRAM, and plays it out the MAX98357A speaker.
> 
> **There is NO external server, NO Docker container, and NO cloud VM required.**

---

## 1. Primary Deployment: USB-C Firmware Flashing (PlatformIO)

Deployment to a microcontroller is called **Flashing**. The Xtensa GCC toolchain compiles the C++ codebase into machine code, and `esptool.py` writes it directly into the ESP32-S3's 16 MB onboard Flash memory.

### Step-by-Step Bring-Up & Flashing

1. **Connect the ESP32-S3**:
   * Plug your ESP32-S3 board into your PC using a high-quality USB-C data cable (plug into the port labeled **USB** or **UART**).
2. **Initial Hardware Bring-Up (Gate 1 Diagnostics)**:
   * Run the Gate 1 self-test to verify the chip, 16 MB Flash, and 8 MB Octal PSRAM integrity:
     ```powershell
     pio run -e gate1_board -t upload
     pio device monitor -b 115200
     ```
3. **Deploy the Production Voice Robot Firmware**:
   * Build and flash the master dual-core robot firmware:
     ```powershell
     pio run -e robot_main -t upload
     ```
4. **Monitor Startup & Wi-Fi Connection**:
   * Open the serial console to watch the boot sequence, NTP clock synchronization, and display initialization:
     ```powershell
     pio device monitor -b 115200
     ```

---

## 2. Release Upgrades: Over-The-Air (OTA) Updates

Once your robot is fully assembled inside its desktop chassis, you do not need to open the enclosure or attach a USB cable to deploy updates.

### Dual-Slot A/B Partitioning
The ESP32-S3 16 MB Flash memory is partitioned into two independent application slots:
* **`ota_0`**: Active firmware slot.
* **`ota_1`**: Inactive staging slot for the next release.

### How Wireless Releases Work
1. Compile the new release in PlatformIO to produce `firmware.bin`.
2. Push the binary to the robot over your home Wi-Fi network (or upload it via the robot's onboard Web UI).
3. The ESP32-S3 writes the new code into the inactive slot and verifies the SHA-256 checksum.
4. The bootloader marks the new slot active and restarts the chip.

> [!TIP]
> **Anti-Bricking Guardrail**:
> If a newly deployed release crashes or fails to establish a Wi-Fi connection within 10 seconds of boot, the ESP32-S3 hardware Watchdog Timer (WDT) triggers an automatic rollback to the previous known-good partition. The robot cannot be bricked over the air.

---

## 3. Live Configuration Updates (Zero Re-Flashing)

For routine settings changes, you never need to recompile or reflash the firmware:

* **Onboard WebConfig Endpoint**: The ESP32-S3 hosts its own authenticated configuration server at `http://<robot-ip>/config`.
* **Instant NVS Persistence**: You can update your Wi-Fi credentials, switch Gemini API keys, or select a different robot voice from any web browser or terminal command:
  ```powershell
  curl.exe -X POST http://<robot-ip>/config `
    -H "Authorization: Bearer <YOUR_ROBOT_BEARER_TOKEN>" `
    -H "Content-Type: application/json" `
    -d '{"voice":"Puck"}'
  ```
* Changes are committed directly to internal Non-Volatile Storage (NVS) in milliseconds without interrupting normal operation.

---

## 4. Optional Local Development Testbench (Laptop Only)

The Python scripts in this `gateway/` folder are **strictly an optional offline developer testbench**. They allow you to test API keys, simulate audio streaming, and verify voice responses on your laptop speakers before physical hardware arrives.

### Testing Your Gemini API Key & Audio Playback
You can test Gemini's live speech synthesis directly on your computer:
1. Ensure your API key is in `~/.robo_secrets/gateway.env`:
   ```ini
   GEMINI_API_KEY=AIzaSy...
   ```
2. Run the live voice synthesis test:
   ```powershell
   cd c:\Robo\gateway
   python -m pip install -r requirements.txt
   python test_live_gemini.py
   ```
   *This will synthesize a response and play it out of your laptop's speakers at 24 kHz.*

### Running the Offline Fault-Injection Suite
To verify network error recovery (e.g. 504 timeouts, odd-byte audio chunk alignments, auth rejections):
```powershell
cd c:\Robo\gateway
pytest test_gateway.py
```

> [!NOTE]
> The optional local test server (`uvicorn app:app --port 8002`) can be run on your laptop for testing audio loopback over HTTP, but it is **never required** for the robot, which runs completely self-contained.
