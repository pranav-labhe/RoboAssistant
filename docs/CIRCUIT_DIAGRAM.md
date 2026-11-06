# ⚡ Complete Circuit Schematic & Hardware Interconnect Guide

This document is the single source of truth for the **RoboAssistant** hardware wiring, power distribution, and component pinouts. It is locked to `include/pins.h` and has zero GPIO or bus conflicts.

---

## 📐 1. Complete ASCII Circuit Schematic

```text
========================================================================================================
                                     POWER RAILS & CAPACITOR DECOUPLING
========================================================================================================

  USB-C 5V (VBUS) -------------------------------------------------+----------------------> [MAX98357A VIN]
                                                                   |
                                                                +-----+
                                                                |     |  1000 µF 25V Electrolytic Cap
                                                                | === |  (Absorbs 1.5A Class-D spikes)
                                                                |  -  |  (Grey stripe with '-' to GND)
                                                                +-----+
                                                                   |
  SYSTEM GROUND   -------------------------------------------------+----------------------> [ALL MODULE GNDs]

  3.3V LDO RAIL   ----------------------------------+-------------------------------------> [INMP441 & ST7789]
                                                    |
                                                  +---+
                                                  |   |  0.1 µF (104) Ceramic Disc Cap
                                                  | = |  (Bypasses 2.4 GHz Wi-Fi RF noise)
                                                  |   |  (Non-polarized, place close to mic)
                                                  +---+
                                                    |
  SYSTEM GROUND   ----------------------------------+

========================================================================================================
                                    ESP32-S3-WROOM-1-N16R8 CORE SCHEMATIC
========================================================================================================

           +------------------------- [ TOP / USB-C ] -------------------------+
           |                                                                   |
           |  [LEFT PIN ROW]                                  [RIGHT PIN ROW]  |
           |                                                                   |
           |  3V3 ---------+ (3.3V Rail)                 GPIO 12 -------------+---> ST7789 SCL (SPI Clock)
           |               |                                                   |
           |  GPIO 4 ------+---> INMP441 SCK (BCLK)      GPIO 11 -------------+---> ST7789 SDA (MOSI)
           |  GPIO 5 ------+---> INMP441 WS  (LRCLK)     GPIO 14 -------------+---> ST7789 RES (Reset)
           |  GPIO 6 ------+---> INMP441 SD  (DOUT)      GPIO 9  -------------+---> ST7789 DC  (Data/Cmd)
           |                                             GPIO 13 -------------+---> ST7789 BLK (Backlight)
           |  GPIO 1 ------+---> PTT Tactile Button                            |
           |               |     (Active LOW to GND)     GPIO 15 -------------+---> MAX98357A BCLK (Bit Clock)
           |  GND ---------+---> SYSTEM GND              GPIO 16 -------------+---> MAX98357A LRC  (LRCLK/WS)
           |  5V (VBUS) ---+---> 5V Rail (from USB)      GPIO 7  -------------+---> MAX98357A DIN  (Audio Data)
           |                                                                   |
           |                                             GND -----------------+---> SYSTEM GND
           |                                                                   |
           +-------------------- [ STRADDLES CENTER TRENCH ] ------------------+

========================================================================================================
                                     PERIPHERAL MODULE CONNECTIONS
========================================================================================================

  1. INMP441 Digital MEMS Microphone (I2S_NUM_0 RX, 16 kHz Master)
     +-------------+
     |   INMP441   |
     |         VDD | <--- 3.3V Rail (STRICTLY 3.3V! NEVER 5V!)
     |         GND | ---> System GND
     |         L/R | ---> System GND (Selects Left Audio Channel)
     |         SCK | <--- ESP32 GPIO 4 (Bit Clock)
     |          WS | <--- ESP32 GPIO 5 (Word Select / LRCLK)
     |          SD | ---> ESP32 GPIO 6 (Serial Data Output to ESP32)
     +-------------+

  2. ST7789 1.3" 240x240 IPS Color Display (SPI2_HOST, 40 MHz)
     +-------------+
     |   ST7789    |
     |         VCC | <--- 3.3V Rail (STRICTLY 3.3V!)
     |         GND | ---> System GND
     |         SCL | <--- ESP32 GPIO 12 (SPI Clock)
     |         SDA | <--- ESP32 GPIO 11 (SPI MOSI Data)
     |         RES | <--- ESP32 GPIO 14 (Hardware Reset)
     |          DC | <--- ESP32 GPIO 9  (Data/Command Select)
     |         BLK | <--- ESP32 GPIO 13 (PWM Backlight Control)
     |         [CS]| <--- [Unconnected / Floating on 7-pin display]
     +-------------+

  3. MAX98357A 3.2W Class-D Mono Amplifier (I2S_NUM_1 TX, 24 kHz)
     +-------------+
     |  MAX98357A  |
     |         VIN | <--- 5V USB Rail (VBUS for full 3.2W headroom)
     |         GND | ---> System GND
     |        BCLK | <--- ESP32 GPIO 15 (Bit Clock)
     |         LRC | <--- ESP32 GPIO 16 (Word Select / LRCLK)
     |         DIN | <--- ESP32 GPIO 7  (Serial Data Input from ESP32)
     |        GAIN | <--- Unconnected / Floating (Default 9 dB volume)
     |     SD_MODE | <--- Unconnected / Floating (Firmware sends stereo [L, L])
     |       OUT + | =====================> [ 8Ω 3W Speaker (+) Red Wire ]
     |       OUT - | =====================> [ 8Ω 3W Speaker (-) Black Wire ]
     +-------------+

  4. Push-to-Talk (PTT) Switch (6x6mm 4-Pin Tactile Button)
     +-------------+
     | 4-PIN BTN   |
     |       Pin A | <--- ESP32 GPIO 1 (Internal Pullup: HIGH when idle, LOW when pressed)
     |       Pin B | ---> System GND
     +-------------+

  5. [Optional Phase 5] Waving Arm Servo Motor (SG90 / MG90S)
     +-------------+
     |   SERVO     |
     |         PWM | <--- ESP32 GPIO 18 (PWM Servo Signal)
     |         VCC | <--- External 5V 2A DC Supply (+) (NEVER laptop USB!)
     |         GND | ---> Common GND (External Supply GND tied to ESP32 GND)
     +-------------+

========================================================================================================
             6. HOST & CLOUD CONNECTIONS: DIRECT ON-CHIP MODE (ZERO EXTERNAL SERVER!)
========================================================================================================

  [1] Physical Wired Connection: USB-C Cable (Power & Flashing Only)
      +---------------------+                    +------------------------------------+
      |  Laptop / PC Host   | =================> |  ESP32-S3 Board USB-C "USB" Port   |
      |  (5V 1A+ USB Port)  |   USB-C D+/D- & 5V |  (GPIO 19/20 Native USB CDC)       |
      +---------------------+                    +------------------------------------+
      * Delivers 5.0V DC to the breadboard power rail.
      * Transmits PlatformIO firmware flashing binaries and serial logs.

  [2] Wireless Over-the-Air Connection: Direct On-Chip Cloud AI (NO External Server Needed!)
      +----------------------------------------+
      |      ESP32-S3 Onboard PCB Antenna      |
      |   (Meandered copper trace on module)   |
      +----------------------------------------+
                          |
             ((( 2.4 GHz Wi-Fi Radio Waves )))
                          |
                          v
      +----------------------------------------+
      |       Home / Office Wi-Fi Router       |
      +----------------------------------------+
                          |
                     (HTTPS / TLS)
                          |
                          v
      +-----------------------------------------------------------------------+
      |               GOOGLE GEMINI CLOUD API (DIRECT ON-CHIP)                |
      |             https://generativelanguage.googleapis.com                 |
      +-----------------------------------------------------------------------+
      * ZERO EXTERNAL SERVER! No laptop gateway, no AWS EC2, and no Docker!
      * The ESP32-S3 uses its 8 MB Octal PSRAM to encode audio, call Gemini directly,
        receive the synthesized voice, and play it directly through the speaker.
      * Optional Gateway Mode: An optional secondary mode (`ROBOT_DEFAULT_MODE = "gateway"`)
        remains available if a developer ever wants to run `gateway/app.py` on a server.
```

---

## 📊 2. Master Interconnection & Pinout Table

| Module | Pin Label | Wire Color | ESP32-S3 Pin | Bus / Hardware Resource | Voltage Level | Notes / Function |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **INMP441** | `VDD` | Orange | `3V3` | Power | **3.3V DC** | Strictly 3.3V! (5V burns MEMS element) |
| **INMP441** | `GND` | Black | `GND` | Ground | 0V | System Common Ground |
| **INMP441** | `L/R` | Black | `GND` | Ground | 0V | Tied to GND selects Left audio channel |
| **INMP441** | `SCK` | Green | `GPIO 4` | `I2S_NUM_0` RX | 3.3V Logic | Bit Clock (1.024 MHz @ 16kHz/32-bit) |
| **INMP441** | `WS` | Yellow | `GPIO 5` | `I2S_NUM_0` RX | 3.3V Logic | Word Select / Frame Clock (16 kHz) |
| **INMP441** | `SD` | Blue | `GPIO 6` | `I2S_NUM_0` RX | 3.3V Logic | Serial Data Out -> ESP32 Data In |
| **Capacitor** | `0.1 µF (104)`| Ceramic | `3V3` & `GND`| Passive Filter | 3.3V DC | Non-polarized; place across mic VDD & GND |
| **ST7789** | `VCC` | Orange | `3V3` | Power | **3.3V DC** | Strictly 3.3V! (Protects display driver) |
| **ST7789** | `GND` | Black | `GND` | Ground | 0V | System Common Ground |
| **ST7789** | `SCL` | Yellow | `GPIO 12` | `SPI2_HOST` | 3.3V Logic | SPI Clock (40 MHz) |
| **ST7789** | `SDA` | Blue | `GPIO 11` | `SPI2_HOST` | 3.3V Logic | SPI MOSI (Master Out Slave In) |
| **ST7789** | `RES` | White | `GPIO 14` | GPIO Output | 3.3V Logic | Hardware Reset line |
| **ST7789** | `DC` | Green | `GPIO 9` | GPIO Output | 3.3V Logic | Data / Command Select |
| **ST7789** | `BLK` | Purple | `GPIO 13` | LEDC PWM | 3.3V Logic | Display Backlight Brightness Control |
| **ST7789** | `CS` | — | Unconnected | — | — | Floating on standard 7-pin CS-less display |
| **MAX98357A**| `VIN` | Red | `5V / VBUS` | Power | **5.0V DC** | Powered from USB 5V for 3.2W audio output |
| **MAX98357A**| `GND` | Black | `GND` | Ground | 0V | System Common Ground |
| **MAX98357A**| `BCLK` | Green | `GPIO 15` | `I2S_NUM_1` TX | 3.3V Logic | Bit Clock (768 kHz @ 24kHz/16-bit) |
| **MAX98357A**| `LRC` | Yellow | `GPIO 16` | `I2S_NUM_1` TX | 3.3V Logic | Word Select / Frame Clock (24 kHz) |
| **MAX98357A**| `DIN` | Blue | `GPIO 7` | `I2S_NUM_1` TX | 3.3V Logic | Audio Data: ESP32 Out -> Amp In |
| **MAX98357A**| `GAIN` | — | Unconnected | Configuration | Float | Left floating = 9 dB default gain |
| **MAX98357A**| `SD_MODE`| — | Unconnected | Configuration | Float | Floating: plays stereo `[L, L]` audio |
| **Capacitor** | `1000 µF 25V`| Cylindrical| `5V` & `GND` | Power Reservoir| 5.0V DC | **Minus (-) stripe to GND; Long leg to 5V** |
| **Speaker** | `Red Wire (+)`| Screw Term | `OUT +` | Audio Output | Analog Class-D | Terminal block on MAX98357A |
| **Speaker** | `Black (-) ` | Screw Term | `OUT -` | Audio Output | Analog Class-D | Terminal block on MAX98357A |
| **PTT Button**| `Pin A` | Yellow | `GPIO 1` | GPIO Input | 3.3V Pullup | Active LOW; hold >=400ms to speak |
| **PTT Button**| `Pin B` | Black | `GND` | Ground | 0V | System Common Ground |
| **[Phase 5]** | `Servo PWM` | Orange | `GPIO 18` | LEDC PWM | 3.3V Logic | Waving Arm Servo Control (SG90) |
| **[Phase 5]** | `Servo VCC` | Red | Ext +5V | External PSU | **5.0V 2A** | Strictly external supply; NEVER from USB! |
| **[Phase 5]** | `Servo GND` | Black | Ext GND | External PSU | 0V | Tie Ext GND to ESP32 GND |
| **Host PC/Laptop** | `USB-C Cable` | Black/White | `USB` Port | Native USB CDC | **5.0V / D+/D-** | Connects to ESP32-S3 `USB` port for power & flashing |
| **Direct Cloud AI** | `Over-The-Air` | Wireless RF | PCB Antenna | 2.4 GHz Wi-Fi | 802.11 b/g/n | **ZERO SERVER!** Calls Google Gemini API directly over Wi-Fi (No Gateway needed) |
| *[Optional Gateway]*| `Over-The-Air` | Wireless RF | PCB Antenna | 2.4 GHz Wi-Fi | 802.11 b/g/n | Optional alternative: connects to `gateway/app.py` if mode="gateway" |

---

## 🌳 3. System Power Tree & Current Budget

```text
Laptop USB-C Port / 5V Adapter (Supplies 5.0V @ 500mA - 1500mA)
  |
  +---> [ESP32-S3 VBUS / 5V Pin]
  |       |
  |       +---> Onboard 3.3V LDO Regulator (ME6211 / AMS1117-3.3)
  |               |  (Provides up to 600-800 mA @ 3.3V)
  |               |
  |               +---> ESP32-S3 Chip (Core 0 + Core 1 + Radio): ~120 - 240 mA
  |               +---> Octal PSRAM + Flash (High-Speed Burst):   ~40 mA
  |               +---> ST7789 IPS Display + LED Backlight:       ~45 mA
  |               +---> INMP441 Digital MEMS Microphone:          ~1.4 mA
  |               +---> PTT Tactile Switch Pullup:                ~0.1 mA
  |               +======================================================
  |               TOTAL 3.3V RAIL LOAD:                           ~220 - 330 mA (Well within LDO limits)
  |
  +---> [MAX98357A Audio Amplifier VIN] (5.0V Unregulated Rail)
  |       |
  |       +---> Quiescent Idle:  ~2.4 mA
  |       +---> Speech Playback: ~200 - 450 mA RMS
  |       +---> Instantaneous Bass Transient Peak: up to 1.2 - 1.5 A
  |               (Buffered by the 1000 µF 25V Electrolytic Capacitor directly across the rails)
  |
  +======================================================================
  TOTAL SYSTEM CURRENT: ~450 - 780 mA (Safe on standard USB 3.0 / USB-C laptop ports)
```

---

## 🛡️ 4. Paranoid Engineering: 6 Critical Failure Modes & Defenses

### Failure Mode 1: 5V Overvoltage on 3.3V Modules (Permanent Silicon Destruction)
* **Risk**: Accidentally plugging `INMP441` (`VDD`) or `ST7789` (`VCC`) into the `5V` (`VBUS`) rail instead of the `3.3V` rail. The MEMS microphone sensor and ST7789 display driver have an absolute maximum rating of $3.6\text{ V}$. Connecting $5\text{ V}$ destroys the internal silicon within milliseconds.
* **Defense**: Establish two distinct, color-coded power buses on the breadboard. Keep the `3.3V` rail strictly on one side (or dedicated bus strip) and `5V` isolated to the amplifier only.

### Failure Mode 2: 1000 µF Electrolytic Capacitor Reversed Polarity (Explosion / USB Port Trip)
* **Risk**: Electrolytic capacitors are polarized components. If plugged in backward (positive to `GND`, negative to `5V`), dielectric breakdown generates internal hydrogen gas. The capacitor will overheat, vent electrolyte, pop loudly, and create a dead short across the laptop's USB power line, tripping PC overcurrent protection.
* **Defense**: Check the silver/grey vertical stripe on the capacitor body. That stripe marks the **negative (-) terminal**. That leg must connect to `GND`. The longer, unmarked leg connects to `5V`.

### Failure Mode 3: Ground Bounce & Audio Whine (Power Rail Contention)
* **Risk**: The Class-D amplifier draws abrupt, fast-switching current pulses (up to 1.5 A). If the microphone and ESP32 share a long, thin, resistive ground jumper with the amplifier, the high-current return pulses elevate the ground potential, injecting a loud, irritating buzzing hum into the analog MEMS microphone frontend.
* **Defense**:
  1. The `1000 µF` capacitor is placed directly at the amplifier power pins on the breadboard, absorbing switching current locally.
  2. The `0.1 µF` ceramic capacitor is placed right across `INMP441` `VDD` and `GND`, bypassing high-frequency Wi-Fi RF pickup.

### Failure Mode 4: Boot Strapping Trap (GPIO 0 Avoidance)
* **Risk**: If the Push-to-Talk button were wired to `GPIO 0` (a common mistake on Arduino projects), holding the button while plugging in the USB cable or pressing reset pulls `GPIO 0` to `GND`. This forces the ESP32-S3 into ROM bootloader download mode, preventing normal firmware startup.
* **Defense**: The PTT button is strictly locked to **`GPIO 1`**, which has zero strapping functions on the ESP32-S3. Holding it during boot never interferes with normal application execution.

### Failure Mode 5: I2S Hardware Bus Contention (Separate RX & TX Channels)
* **Risk**: Trying to multiplex microphone input and speaker output onto a single I2S controller creates clock frequency and sample-rate mismatches (the mic records at 16 kHz 32-bit slot, while the speaker plays 24 kHz or 16 kHz stereo).
* **Defense**: The circuit uses two completely independent hardware I2S controllers:
  * `I2S_NUM_0` is locked strictly to **RX** on GPIO 4, 5, 6 (Microphone).
  * `I2S_NUM_1` is locked strictly to **TX** on GPIO 15, 16, 7 (Speaker Amplifier).
  They run completely asynchronous DMA channels with zero bus contention.

### Failure Mode 6: USB Brownout During Peak Speech Playback
* **Risk**: Laptop USB ports have internal current limiters. When the speaker plays loud audio peaks, the sudden current surge can cause the 5V rail to dip below 4.3V, causing the ESP32-S3 onboard LDO to drop below 3.0V, triggering a brownout reset loop.
* **Defense**: The firmware sets a software volume limiter (`SOFTWARE_VOLUME = 0.35f`), and the hardware circuit incorporates the `1000 µF` buffer reservoir across `5V` and `GND` to smooth out all peak demands.
