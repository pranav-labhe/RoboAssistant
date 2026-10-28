#include <Arduino.h>
#include <driver/i2s.h>
#include <esp_system.h>
#include <math.h>
#include "pins.h"

// ============================================================================
// Gate 4: MAX98357A Speaker & USB Power Stability Test
// Build & Upload: pio run -e gate4_speaker -t upload -t monitor
// Wiring:
//   BCLK -> GPIO 15, LRC -> GPIO 16, DIN -> GPIO 7, VIN -> 5V, GND -> GND
//   GAIN -> Leave unconnected / floating for default 9 dB (or direct to VIN for 6 dB)
//   SD   -> Leave floating (firmware sends identical [Left, Right] samples)
//   Place 470-1000 uF electrolytic capacitor across 5V and GND rails.
// Pass Criteria:
//   - Plays 440 Hz tone + two-note robot chime cleanly every 4 seconds
//   - Zero brownout resets on USB power and zero trailing DMA buzz
// ============================================================================

static constexpr i2s_port_t AMP_I2S_PORT = I2S_NUM_1;
static constexpr uint32_t   AMP_SAMPLE_RATE = 24000; // Matches OpenAI PCM TTS rate
static constexpr float      SOFTWARE_VOLUME = 0.30f; // Start safely at 30% volume
static constexpr size_t     CHUNK_FRAMES = 256;

static void initSpeakerI2S() {
    i2s_config_t cfg = {};
    cfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
    cfg.sample_rate = AMP_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    cfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT; // Stereo [L, R] slots
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = CHUNK_FRAMES;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true; // Auto-zero DMA descriptor on underrun
    cfg.fixed_mclk = 0;

    i2s_pin_config_t pins = {};
    pins.bck_io_num = PIN_AMP_BCLK;
    pins.ws_io_num = PIN_AMP_LRC;
    pins.data_out_num = PIN_AMP_DIN;
    pins.data_in_num = I2S_PIN_NO_CHANGE;

    esp_err_t err = i2s_driver_install(AMP_I2S_PORT, &cfg, 0, nullptr);
    if (err != ESP_OK) {
        Serial.printf("[ERROR] Speaker i2s_driver_install failed: %d\n", err);
        while (true) { delay(1000); }
    }

    err = i2s_set_pin(AMP_I2S_PORT, &pins);
    if (err != ESP_OK) {
        Serial.printf("[ERROR] Speaker i2s_set_pin failed: %d\n", err);
        while (true) { delay(1000); }
    }

    i2s_zero_dma_buffer(AMP_I2S_PORT);
}

// Plays a sine wave with linear attack/release envelope to prevent speaker clicks
static void playToneHz(float freqHz, uint32_t durationMs, float volume) {
    const uint32_t totalFrames = (AMP_SAMPLE_RATE * durationMs) / 1000;
    const uint32_t fadeFrames = AMP_SAMPLE_RATE / 100; // 10 ms click-free fade-in/out
    int16_t stereoBuf[CHUNK_FRAMES * 2];               // Interleaved [L, R]

    uint32_t frameIdx = 0;
    const float twoPiF = 2.0f * static_cast<float>(M_PI) * freqHz / AMP_SAMPLE_RATE;

    while (frameIdx < totalFrames) {
        size_t batch = totalFrames - frameIdx;
        if (batch > CHUNK_FRAMES) {
            batch = CHUNK_FRAMES;
        }

        for (size_t i = 0; i < batch; ++i) {
            uint32_t cur = frameIdx + i;
            float env = 1.0f;
            if (cur < fadeFrames) {
                env = static_cast<float>(cur) / fadeFrames;
            } else if (totalFrames - cur < fadeFrames) {
                env = static_cast<float>(totalFrames - cur) / fadeFrames;
            }

            float raw = sinf(twoPiF * cur);
            int16_t sample = static_cast<int16_t>(raw * 32767.0f * volume * env);

            // Duplicate mono sample into both Left and Right channels
            stereoBuf[i * 2]     = sample;
            stereoBuf[i * 2 + 1] = sample;
        }

        size_t bytesWritten = 0;
        i2s_write(AMP_I2S_PORT, stereoBuf, batch * 2 * sizeof(int16_t), &bytesWritten, portMAX_DELAY);
        frameIdx += batch;
    }

    // Critical guardrail: always flush DMA ring buffer on exit so last buffer never loops
    i2s_zero_dma_buffer(AMP_I2S_PORT);
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    Serial.println("\n============================================================");
    Serial.println("  GATE 4: MAX98357A SPEAKER & POWER STABILITY TEST");
    Serial.printf("  Last Reset Reason: %d (BROWNOUT = %d)\n",
                  static_cast<int>(esp_reset_reason()),
                  static_cast<int>(ESP_RST_BROWNOUT));
    Serial.println("============================================================");

    initSpeakerI2S();
}

void loop() {
    Serial.printf("[Gate 4] Playing 440 Hz test tone (Vol: %.0f%%) | Free Heap: %u bytes\n",
                  SOFTWARE_VOLUME * 100.0f, ESP.getFreeHeap());
    playToneHz(440.0f, 500, SOFTWARE_VOLUME);
    delay(250);

    Serial.println("[Gate 4] Playing two-note robot chime (554 Hz -> 659 Hz)...");
    playToneHz(554.37f, 180, SOFTWARE_VOLUME);
    playToneHz(659.25f, 260, SOFTWARE_VOLUME);

    Serial.println("[Gate 4] Playback complete. DMA buffer zeroed. Waiting 4s...");
    delay(4000);
}
