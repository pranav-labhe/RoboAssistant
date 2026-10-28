#include <Arduino.h>
#include <driver/i2s.h>
#include <math.h>
#include "pins.h"

// ============================================================================
// Gate 3: INMP441 Microphone + DC-Offset Blocker + Serial Plotter RMS
// Build & Upload: pio run -e gate3_mic -t upload -t monitor
// Wiring:
//   SCK -> GPIO 4, WS -> GPIO 5, SD -> GPIO 6, L/R -> GND, VDD -> 3.3V
//   Place 0.1 uF ceramic cap right across INMP441 VDD and GND.
// Pass Criteria:
//   - Silence reads flat (< 150 RMS)
//   - Speaking at 30-50 cm spikes clearly (> 1500 RMS)
// ============================================================================

static constexpr i2s_port_t MIC_I2S_PORT = I2S_NUM_0;
static constexpr uint32_t   MIC_SAMPLE_RATE = 16000;
static constexpr size_t     FRAME_SAMPLES = 512; // 32 ms window at 16 kHz
static constexpr int        MIC_BIT_SHIFT = 11;  // Shifts 24-bit-in-32-bit word into int16 range
static constexpr int32_t    VAD_RMS_THRESHOLD = 800;

static int32_t g_rawSamples[FRAME_SAMPLES];
static int32_t g_prevX = 0;
static int32_t g_prevY = 0;

static void initMicrophoneI2S() {
    i2s_config_t cfg = {};
    cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
    cfg.sample_rate = MIC_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT; // L/R pin tied to GND
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = 256;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = false;
    cfg.fixed_mclk = 0;

    i2s_pin_config_t pins = {};
    pins.bck_io_num = PIN_MIC_BCLK;
    pins.ws_io_num = PIN_MIC_WS;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = PIN_MIC_SD;

    esp_err_t err = i2s_driver_install(MIC_I2S_PORT, &cfg, 0, nullptr);
    if (err != ESP_OK) {
        Serial.printf("[ERROR] i2s_driver_install failed: %d\n", err);
        while (true) { delay(1000); }
    }

    err = i2s_set_pin(MIC_I2S_PORT, &pins);
    if (err != ESP_OK) {
        Serial.printf("[ERROR] i2s_set_pin failed: %d\n", err);
        while (true) { delay(1000); }
    }

    i2s_zero_dma_buffer(MIC_I2S_PORT);
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    Serial.println("# Gate 3: INMP441 Mic Test starting (16 kHz, 32-bit -> 16-bit + DC blocker)");
    initMicrophoneI2S();

    // Discard initial 200 ms of I2S MEMS startup settling transient
    size_t bytesRead = 0;
    for (int i = 0; i < 6; ++i) {
        i2s_read(MIC_I2S_PORT, g_rawSamples, sizeof(g_rawSamples), &bytesRead, portMAX_DELAY);
    }
}

void loop() {
    size_t bytesRead = 0;
    esp_err_t err = i2s_read(MIC_I2S_PORT, g_rawSamples, sizeof(g_rawSamples), &bytesRead, portMAX_DELAY);
    if (err != ESP_OK || bytesRead == 0) {
        return;
    }

    const size_t samplesRead = bytesRead / sizeof(int32_t);
    uint64_t sumSquares = 0;
    int32_t peakAbs = 0;

    for (size_t i = 0; i < samplesRead; ++i) {
        // 1. Shift 24-bit signed value in 32-bit slot down to 16-bit range
        int32_t x = g_rawSamples[i] >> MIC_BIT_SHIFT;

        // 2. First-order IIR DC-blocking high-pass filter:
        //    y[n] = x[n] - x[n-1] + (255/256) * y[n-1]
        int32_t y = (x - g_prevX) + ((g_prevY * 255) >> 8);
        g_prevX = x;
        g_prevY = y;

        // 3. Clamp to int16_t limits
        if (y > 32767)  y = 32767;
        if (y < -32768) y = -32768;

        int32_t absY = (y < 0) ? -y : y;
        if (absY > peakAbs) {
            peakAbs = absY;
        }
        sumSquares += static_cast<uint64_t>(y * y);
    }

    int32_t rms = static_cast<int32_t>(sqrtf(static_cast<float>(sumSquares) / samplesRead));
    int32_t voiceFlag = (rms > VAD_RMS_THRESHOLD) ? 2000 : 0;

    // Formatted for Arduino / PlatformIO Serial Plotter
    Serial.printf("RMS:%d,Peak:%d,SpeechDetected:%d\n", rms, peakAbs, voiceFlag);
}
