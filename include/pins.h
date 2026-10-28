#pragma once

// ============================================================================
// Locked, Conflict-Free Pin Map for ESP32-S3-WROOM-1-N16R8
// Avoids:
//   - GPIO 19, 20 (Native USB D-/D+)
//   - GPIO 26..37 (Internal Octal Flash & Octal PSRAM)
//   - GPIO 0, 3, 45, 46 (Strapping pins)
// ============================================================================

// --- 1. INMP441 Digital Microphone (I2S_NUM_0 RX, 3.3V ONLY) ---
#define PIN_MIC_BCLK    4   // SCK
#define PIN_MIC_WS      5   // WS / LRCLK
#define PIN_MIC_SD      6   // SD / DOUT (tie module L/R pin to GND)

// --- 2. MAX98357A Class-D Amplifier (I2S_NUM_1 TX, 5V VIN) ---
// Hardware wiring note (Official MAX98357A datasheet gain table):
//   - 100k resistor GAIN -> VIN:      3 dB (quietest)
//   - Direct jumper GAIN -> VIN (5V): 6 dB
//   - GAIN left floating / unconnected: 9 dB (RECOMMENDED DEFAULT: simplest for breadboard)
//   - Direct jumper GAIN -> GND:     12 dB
//   - 100k resistor GAIN -> GND:     15 dB (loudest)
//   - Leave module SD pin floating (firmware sends stereo [L, L] samples)
#define PIN_AMP_BCLK    15
#define PIN_AMP_LRC     16
#define PIN_AMP_DIN     7

// --- 3. ST7789 240x240 SPI TFT Display (SPI2_HOST, 3.3V) ---
// Set TFT_HAS_CS_PIN to 1 for 8-pin displays (with CS), or 0 for 7-pin CS-less displays
#define TFT_HAS_CS_PIN  1

#define PIN_TFT_MOSI    11  // SDA
#define PIN_TFT_SCLK    12  // SCL
#if TFT_HAS_CS_PIN
  #define PIN_TFT_CS    10  // CS (8-pin module, uses SPI_MODE0)
  #define TFT_SPI_MODE  0
#else
  #define PIN_TFT_CS    -1  // No CS pin (7-pin module, requires SPI_MODE3)
  #define TFT_SPI_MODE  3
#endif
#define PIN_TFT_DC      9
#define PIN_TFT_RST     14
#define PIN_TFT_BL      13  // Backlight

// --- 4. Push-to-Talk & Hands-Free VAD Mode Control (GPIO 1, Active LOW) ---
// Uses GPIO 1 instead of boot-strapping GPIO 0 so holding the button during
// a reset never traps the ESP32-S3 in ROM download mode.
//   - Hold GPIO 1 (>= 400 ms): Push-to-Talk manual recording
//   - Quick tap GPIO 1 (< 350 ms): Toggles Hands-Free Always-Listening VAD ON/OFF
#define PIN_PTT_BTN             1
#define ENABLE_AUTO_VAD_DEFAULT 1

// --- 5. Waving Arm Servo (Phase 5 Extra, External 5V 2A Supply) ---
// Set ENABLE_SERVO_ARM to 1 only after connecting a dedicated 5V 2A supply!
#define ENABLE_SERVO_ARM 0
#define PIN_SERVO_PWM    18
