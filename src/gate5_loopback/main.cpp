#include <Arduino.h>
#include <driver/i2s.h>
#include "pins.h"

// ============================================================================
// Gate 5: PSRAM Record & Playback Loopback (Push-to-Talk on GPIO 1)
// Build & Upload: pio run -e gate5_loopback -t upload -t monitor
// Wiring:
//   - INMP441 Mic on I2S_NUM_0 (GPIO 4, 5, 6)
//   - MAX98357A Amp on I2S_NUM_1 (GPIO 15, 16, 7)
//   - Tactile Button between GPIO 1 (PIN_PTT_BTN) and GND
// Pass Criteria:
//   - Hold button, speak for 1-3 seconds, release
//   - Recorded voice plays back clearly at normal pitch with zero brownout
// ============================================================================

static constexpr i2s_port_t MIC_I2S_PORT = I2S_NUM_0;
static constexpr i2s_port_t AMP_I2S_PORT = I2S_NUM_1;

static constexpr uint32_t   LOOPBACK_SAMPLE_RATE = 16000;
static constexpr uint32_t   MAX_RECORD_SECONDS   = 3;
static constexpr size_t     MAX_RECORD_SAMPLES   = LOOPBACK_SAMPLE_RATE * MAX_RECORD_SECONDS;
static constexpr size_t     MIN_VALID_SAMPLES    = (LOOPBACK_SAMPLE_RATE * 400) / 1000; // 400 ms min
static constexpr size_t     IO_CHUNK_SAMPLES     = 256;
static constexpr int        MIC_BIT_SHIFT        = 11;
static constexpr float      PLAYBACK_VOLUME      = 0.35f;

static int16_t* g_psramAudioBuf = nullptr;
static int32_t  g_prevX = 0;
static int32_t  g_prevY = 0;

static void initMicI2S() {
    i2s_config_t cfg = {};
    cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
    cfg.sample_rate = LOOPBACK_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = IO_CHUNK_SAMPLES;
    cfg.use_apll = false;

    i2s_pin_config_t pins = {};
    pins.bck_io_num = PIN_MIC_BCLK;
    pins.ws_io_num = PIN_MIC_WS;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = PIN_MIC_SD;

    ESP_ERROR_CHECK(i2s_driver_install(MIC_I2S_PORT, &cfg, 0, nullptr));
    ESP_ERROR_CHECK(i2s_set_pin(MIC_I2S_PORT, &pins));
    i2s_zero_dma_buffer(MIC_I2S_PORT);
}

static void initAmpI2S() {
    i2s_config_t cfg = {};
    cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
    cfg.sample_rate = 24000; // Default TTS clock; switched via i2s_set_clk() for 16 kHz loopback
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = IO_CHUNK_SAMPLES;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true;

    i2s_pin_config_t pins = {};
    pins.bck_io_num = PIN_AMP_BCLK;
    pins.ws_io_num = PIN_AMP_LRC;
    pins.data_out_num = PIN_AMP_DIN;
    pins.data_in_num = I2S_PIN_NO_CHANGE;

    ESP_ERROR_CHECK(i2s_driver_install(AMP_I2S_PORT, &cfg, 0, nullptr));
    ESP_ERROR_CHECK(i2s_set_pin(AMP_I2S_PORT, &pins));
    i2s_zero_dma_buffer(AMP_I2S_PORT);
}

static size_t recordWhileButtonHeld() {
    int32_t rawBuf[IO_CHUNK_SAMPLES];
    size_t recordedSamples = 0;
    g_prevX = 0;
    g_prevY = 0;

    // Flush stale mic DMA data before recording starts
    i2s_zero_dma_buffer(MIC_I2S_PORT);

    Serial.println("[Gate 5] RECORDING... (Hold GPIO 1 button and speak, up to 3.0s)");
    while (digitalRead(PIN_PTT_BTN) == LOW && recordedSamples < MAX_RECORD_SAMPLES) {
        size_t bytesRead = 0;
        esp_err_t err = i2s_read(MIC_I2S_PORT, rawBuf, sizeof(rawBuf), &bytesRead, pdMS_TO_TICKS(100));
        if (err != ESP_OK || bytesRead == 0) {
            continue;
        }

        size_t count = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < count && recordedSamples < MAX_RECORD_SAMPLES; ++i) {
            int32_t x = rawBuf[i] >> MIC_BIT_SHIFT;
            int32_t y = (x - g_prevX) + ((g_prevY * 255) >> 8);
            g_prevX = x;
            g_prevY = y;

            if (y > 32767)  y = 32767;
            if (y < -32768) y = -32768;

            g_psramAudioBuf[recordedSamples++] = static_cast<int16_t>(y);
        }
    }

    return recordedSamples;
}

static void playRecordedBuffer(size_t sampleCount) {
    // Switch Speaker I2S clock to 16 kHz to match the microphone recording rate
    ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, LOOPBACK_SAMPLE_RATE,
                                I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));

    int16_t stereoChunk[IO_CHUNK_SAMPLES * 2];
    size_t idx = 0;

    Serial.printf("[Gate 5] PLAYING BACK %u samples (%.2f sec)...\n",
                  static_cast<unsigned>(sampleCount),
                  static_cast<float>(sampleCount) / LOOPBACK_SAMPLE_RATE);

    while (idx < sampleCount) {
        size_t batch = sampleCount - idx;
        if (batch > IO_CHUNK_SAMPLES) {
            batch = IO_CHUNK_SAMPLES;
        }

        for (size_t i = 0; i < batch; ++i) {
            int16_t scaled = static_cast<int16_t>(g_psramAudioBuf[idx + i] * PLAYBACK_VOLUME);
            stereoChunk[i * 2]     = scaled;
            stereoChunk[i * 2 + 1] = scaled;
        }

        size_t bytesWritten = 0;
        i2s_write(AMP_I2S_PORT, stereoChunk, batch * 2 * sizeof(int16_t), &bytesWritten, portMAX_DELAY);
        idx += batch;
    }

    // Zero DMA buffer on exit and restore 24 kHz TTS clock
    i2s_zero_dma_buffer(AMP_I2S_PORT);
    ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, 24000,
                                I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));
    Serial.println("[Gate 5] Playback finished. Ready for next press.");
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    pinMode(PIN_PTT_BTN, INPUT_PULLUP);

    Serial.println("\n============================================================");
    Serial.println("  GATE 5: PSRAM MIC-TO-SPEAKER LOOPBACK TEST");
    Serial.println("============================================================");

    const size_t bufBytes = MAX_RECORD_SAMPLES * sizeof(int16_t);
    g_psramAudioBuf = static_cast<int16_t*>(ps_malloc(bufBytes));
    if (g_psramAudioBuf == nullptr) {
        Serial.println("[FAIL] Failed to allocate 96 KB audio buffer in PSRAM!");
        while (true) { delay(1000); }
    }
    Serial.printf("[OK] Allocated %u bytes in PSRAM (Free PSRAM: %u bytes)\n",
                  static_cast<unsigned>(bufBytes),
                  static_cast<unsigned>(ESP.getFreePsram()));

    initMicI2S();
    initAmpI2S();
    Serial.println("[READY] Press and hold the button on GPIO 1 to record.");
}

void loop() {
    if (digitalRead(PIN_PTT_BTN) == LOW) {
        delay(30); // 30 ms mechanical debounce
        if (digitalRead(PIN_PTT_BTN) == LOW) {
            size_t samples = recordWhileButtonHeld();

            // Wait for button release if 3s cap was reached while still held
            while (digitalRead(PIN_PTT_BTN) == LOW) {
                delay(10);
            }
            delay(30); // Release debounce

            if (samples < MIN_VALID_SAMPLES) {
                Serial.printf("[SKIP] Press too short (%u samples < 400 ms). Ignored.\n",
                              static_cast<unsigned>(samples));
            } else {
                playRecordedBuffer(samples);
            }
        }
    }
    delay(10);
}
