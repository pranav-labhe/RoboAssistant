#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <driver/i2s.h>
#include <time.h>
#include <math.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include "pins.h"
#include "certs.h"
#include "robot_secrets.h"

// ============================================================================
// Gates 9-12 + Phase 5 VAD: Dual-Core Tabletop Voice Robot (`[env:robot_main]`)
// Build & Upload: pio run -e robot_main -t upload -t monitor
// Architecture:
//   - Core 0 (DisplayTask, priority 1): Non-blocking ~30 FPS LovyanGFX PSRAM eye
//     expression state machine (BOOT, IDLE, LISTENING, THINKING, SPEAKING, ERROR);
//     yields 33 ms per frame so Core 0 Wi-Fi/LwIP tasks run unimpeded.
//   - Core 1 (Arduino loop() — Audio & Network):
//     * Hands-Free Always-Listening Energy VAD (auto-calibrated noise floor,
//       250 ms PSRAM pre-roll ring buffer, 900 ms silence boundary, 8 s cap,
//       and 450 ms post-playback half-duplex echo cooldown + DMA flush)
//     * Push-to-Talk override on GPIO 1 (hold >= 400 ms to talk, quick tap
//       < 350 ms to toggle Hands-Free VAD ON/OFF at runtime)
//     * HTTPS POST to Gateway `/talk`, 400 ms PSRAM playback pre-buffer,
//       1-byte odd-chunk alignment carryover, and stereo [L, L] I2S output.
// ============================================================================

enum RobotState : uint8_t {
    STATE_BOOT = 0,
    STATE_IDLE,
    STATE_LISTENING,
    STATE_THINKING,
    STATE_SPEAKING,
    STATE_ERROR
};

static volatile RobotState g_robotState       = STATE_BOOT;
static volatile int32_t    g_speechBouncePx   = 0; // 0..18 px vertical bounce during speech

static constexpr i2s_port_t MIC_I2S_PORT       = I2S_NUM_0;
static constexpr i2s_port_t AMP_I2S_PORT       = I2S_NUM_1;
static constexpr uint32_t   MIC_SAMPLE_RATE    = 16000;
static constexpr uint32_t   TTS_DEFAULT_RATE   = 24000;
static constexpr uint32_t   MAX_RECORD_SECONDS = 8; // Gate 12 guardrail: 8-second cap
static constexpr size_t     MAX_RECORD_SAMPLES = MIC_SAMPLE_RATE * MAX_RECORD_SECONDS; // 128,000 samples (256 KB)
static constexpr size_t     MIN_VALID_SAMPLES  = (MIC_SAMPLE_RATE * 400) / 1000;       // 400 ms min
static constexpr size_t     IO_CHUNK_SAMPLES   = 256;                                  // 16 ms @ 16 kHz
static constexpr int        MIC_BIT_SHIFT      = 11;
static constexpr float      PLAYBACK_VOLUME    = 0.35f;
static constexpr size_t     PREBUFFER_BYTES    = TTS_DEFAULT_RATE * 2 * 400 / 1000;    // 400 ms @ 24 kHz (19.2 KB)

// Hands-Free Half-Duplex VAD Parameters
static constexpr size_t     VAD_PREROLL_SAMPLES          = (MIC_SAMPLE_RATE * 250) / 1000; // 250 ms pre-roll (4,000 samples)
static constexpr uint32_t   VAD_ONSET_FRAMES_REQUIRED    = 3;                              // 3 x 16 ms = 48 ms speech onset
static constexpr uint32_t   VAD_SILENCE_HANGOVER_MS      = 900;                            // Stop 900 ms after speech ends
static constexpr uint32_t   VAD_POST_PLAYBACK_COOLDOWN_MS = 450;                           // Half-duplex deaf window after TTS
static constexpr int32_t    VAD_MIN_RMS_THRESHOLD        = 750;
static constexpr int32_t    VAD_MAX_VALID_AMBIENT_RMS    = 6000;

static int16_t* g_recordPsramBuf  = nullptr;
static int16_t* g_prerollPsramBuf = nullptr;
static uint8_t* g_prebufPsram     = nullptr;
static size_t   g_prerollWriteIdx = 0;
static bool     g_prerollFull     = false;
static bool     g_autoVadEnabled  = (ENABLE_AUTO_VAD_DEFAULT != 0);
static int32_t  g_vadThresholdRms = 1200;
static uint32_t g_vadOnsetCount   = 0;
static int32_t  g_prevX = 0;
static int32_t  g_prevY = 0;

// ============================================================================
// Core 0: LovyanGFX Display & PSRAM Eye Expression Engine
// ============================================================================

class RobotDisplay : public lgfx::LGFX_Device {
    lgfx::Panel_ST7789 _panel;
    lgfx::Bus_SPI      _bus;
    lgfx::Light_PWM    _light;

public:
    RobotDisplay() {
        {
            auto cfg = _bus.config();
            cfg.spi_host   = SPI2_HOST;
            cfg.spi_mode   = TFT_SPI_MODE;
            cfg.freq_write = 40000000;
            cfg.freq_read  = 16000000;
            cfg.spi_3wire  = true;
            cfg.use_lock   = true;
            cfg.dma_channel = SPI_DMA_CH_AUTO;
            cfg.pin_sclk   = PIN_TFT_SCLK;
            cfg.pin_mosi   = PIN_TFT_MOSI;
            cfg.pin_miso   = -1;
            cfg.pin_dc     = PIN_TFT_DC;
            _bus.config(cfg);
            _panel.setBus(&_bus);
        }
        {
            auto cfg = _panel.config();
            cfg.pin_cs           = PIN_TFT_CS;
            cfg.pin_rst          = PIN_TFT_RST;
            cfg.pin_busy         = -1;
            cfg.panel_width      = 240;
            cfg.panel_height     = 240;
            cfg.offset_x         = 0;
            cfg.offset_y         = 0;
            cfg.offset_rotation  = 0;
            cfg.dummy_read_pixel = 8;
            cfg.dummy_read_bits  = 1;
            cfg.readable         = false;
            cfg.invert           = true;
            cfg.rgb_order        = false;
            cfg.dlen_16bit       = false;
            cfg.bus_shared       = false;
            _panel.config(cfg);
        }
        {
            auto cfg = _light.config();
            cfg.pin_bl      = PIN_TFT_BL;
            cfg.invert      = false;
            cfg.freq        = 12000;
            cfg.pwm_channel = 7;
            _light.config(cfg);
            _panel.setLight(&_light);
        }
        setPanel(&_panel);
    }
};

static RobotDisplay      g_tft;
static lgfx::LGFX_Sprite g_faceSprite(&g_tft);

static constexpr int FACE_BAND_W = 240;
static constexpr int FACE_BAND_H = 140;
static constexpr int FACE_BAND_Y = 50;

static void renderFaceFrame(RobotState state, uint32_t nowMs) {
    g_faceSprite.fillScreen(TFT_BLACK);

    const uint16_t cyanColor  = g_tft.color565(0, 225, 255);
    const uint16_t greenColor = g_tft.color565(40, 255, 130);
    const uint16_t amberColor = g_tft.color565(255, 180, 30);
    const uint16_t redColor   = g_tft.color565(255, 70, 70);

    int leftX  = 70;
    int rightX = 170;
    int eyeY   = 55;
    int eyeW   = 56;
    int eyeH   = 64;

    switch (state) {
        case STATE_BOOT: {
            int dot = (nowMs / 300) % 3;
            g_faceSprite.fillRoundRect(leftX - 24, eyeY - 12, 48, 24, 8, amberColor);
            g_faceSprite.fillRoundRect(rightX - 24, eyeY - 12, 48, 24, 8, amberColor);
            for (int i = 0; i <= dot; ++i) {
                g_faceSprite.fillCircle(104 + i * 16, 115, 4, amberColor);
            }
            break;
        }
        case STATE_IDLE: {
            uint32_t blinkCycle = nowMs % 3400;
            if (blinkCycle < 80) {
                eyeH = 64 - static_cast<int>((blinkCycle * 58) / 80);
            } else if (blinkCycle < 160) {
                eyeH = 6 + static_cast<int>(((blinkCycle - 80) * 58) / 80);
            }
            int lookX = 0;
            uint32_t phase = (nowMs / 2200) % 4;
            if (phase == 1) lookX = -8;
            if (phase == 3) lookX = 8;

            int r = min(16, eyeH / 2);
            g_faceSprite.fillRoundRect(leftX + lookX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, r, cyanColor);
            g_faceSprite.fillRoundRect(rightX + lookX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, r, cyanColor);
            break;
        }
        case STATE_LISTENING: {
            // Wide attentive green eyes + subtle pulse
            int pulse = static_cast<int>(4.0f * sinf(nowMs * 0.012f));
            int w = 62 + pulse;
            int h = 74 + pulse;
            g_faceSprite.fillRoundRect(leftX - w / 2, eyeY - h / 2, w, h, 18, greenColor);
            g_faceSprite.fillRoundRect(rightX - w / 2, eyeY - h / 2, w, h, 18, greenColor);
            break;
        }
        case STATE_THINKING: {
            // Looking up with animated horizontal scan
            int scanX = static_cast<int>(12.0f * sinf(nowMs * 0.008f));
            int thinkY = eyeY - 14;
            g_faceSprite.fillRoundRect(leftX + scanX - 26, thinkY - 22, 52, 44, 14, amberColor);
            g_faceSprite.fillRoundRect(rightX + scanX - 26, thinkY - 22, 52, 44, 14, amberColor);
            break;
        }
        case STATE_SPEAKING: {
            int bounce = g_speechBouncePx;
            int h = 60 + (bounce / 2);
            int y = eyeY - (bounce / 3);
            g_faceSprite.fillRoundRect(leftX - eyeW / 2, y - h / 2, eyeW, h, 16, cyanColor);
            g_faceSprite.fillRoundRect(rightX - eyeW / 2, y - h / 2, eyeW, h, 16, cyanColor);

            // Animated mouth bar synced to voice energy
            int mouthW = 44 + bounce * 2;
            int mouthH = 6 + bounce;
            g_faceSprite.fillRoundRect(120 - mouthW / 2, 114 - mouthH / 2, mouthW, mouthH, mouthH / 2, cyanColor);
            break;
        }
        case STATE_ERROR: {
            // Sad slanted eyes
            g_faceSprite.fillRoundRect(leftX - 28, eyeY - 14, 56, 28, 8, redColor);
            g_faceSprite.fillRoundRect(rightX - 28, eyeY - 14, 56, 28, 8, redColor);
            g_faceSprite.fillRoundRect(96, 112, 48, 6, 3, redColor);
            break;
        }
    }

    g_faceSprite.pushSprite(0, FACE_BAND_Y);
}

static void displayTask(void* param) {
    g_tft.init();
    g_tft.setRotation(0);
    g_tft.setBrightness(220);
    g_tft.fillScreen(TFT_BLACK);

    g_faceSprite.setPsram(true);
    g_faceSprite.setColorDepth(16);
    g_faceSprite.createSprite(FACE_BAND_W, FACE_BAND_H);

    while (true) {
        renderFaceFrame(g_robotState, millis());
        vTaskDelay(pdMS_TO_TICKS(33)); // ~30 FPS non-blocking on Core 0 (yields to Wi-Fi)
    }
}

// ============================================================================
// Optional Servo Waving Arm Helper (Phase 5 Extra on GPIO 18)
// ============================================================================

static void triggerServoWaveIfEnabled() {
#if ENABLE_SERVO_ARM
    // 50 Hz standard servo PWM on LEDC channel 2 (GPIO 18)
    ledcSetup(2, 50, 16);
    ledcAttachPin(PIN_SERVO_PWM, 2);
    const uint32_t dutyMin = 3277; // ~1.0 ms (45 deg)
    const uint32_t dutyMax = 6553; // ~2.0 ms (135 deg)
    const uint32_t dutyMid = 4915; // ~1.5 ms (90 deg neutral)
    for (int i = 0; i < 2; ++i) {
        ledcWrite(2, dutyMin);
        delay(180);
        ledcWrite(2, dutyMax);
        delay(180);
    }
    ledcWrite(2, dutyMid);
    delay(150);
    ledcDetachPin(PIN_SERVO_PWM);
#endif
}

// ============================================================================
// Core 1: Pre-Buffered Chunk-Safe I2S PCM Stream Sink
// ============================================================================

class RobotVoiceI2SSink : public Stream {
    uint8_t* _prebuf;
    size_t   _prebufCap;
    size_t   _prebufLen = 0;
    bool     _startedPlayback = false;
    bool     _hasCarry = false;
    uint8_t  _carryByte = 0;
    size_t   _totalPlayedBytes = 0;

    void writeAlignedPcmToI2S(const uint8_t* data, size_t evenLen) {
        const int16_t* samples = reinterpret_cast<const int16_t*>(data);
        size_t sampleCount = evenLen / sizeof(int16_t);
        int16_t stereoBatch[IO_CHUNK_SAMPLES * 2];

        size_t idx = 0;
        while (idx < sampleCount) {
            size_t batch = sampleCount - idx;
            if (batch > IO_CHUNK_SAMPLES) batch = IO_CHUNK_SAMPLES;

            uint64_t sumSq = 0;
            for (size_t i = 0; i < batch; ++i) {
                int16_t scaled = static_cast<int16_t>(samples[idx + i] * PLAYBACK_VOLUME);
                stereoBatch[i * 2]     = scaled;
                stereoBatch[i * 2 + 1] = scaled;
                sumSq += static_cast<uint64_t>( static_cast<int32_t>(scaled) * scaled );
            }

            int32_t rms = static_cast<int32_t>(sqrtf(static_cast<float>(sumSq) / batch));
            int32_t bounce = min<int32_t>(18, rms / 350);
            g_speechBouncePx = bounce;

            size_t bytesWritten = 0;
            i2s_write(AMP_I2S_PORT, stereoBatch, batch * 2 * sizeof(int16_t), &bytesWritten, portMAX_DELAY);
            idx += batch;
        }
        _totalPlayedBytes += evenLen;
    }

public:
    RobotVoiceI2SSink(uint8_t* prebuf, size_t prebufCap)
        : _prebuf(prebuf), _prebufCap(prebufCap) {}

    void reset() {
        _prebufLen = 0;
        _startedPlayback = false;
        _hasCarry = false;
        _carryByte = 0;
        _totalPlayedBytes = 0;
        g_speechBouncePx = 0;
    }

    size_t write(uint8_t b) override {
        return write(&b, 1);
    }

    size_t write(const uint8_t* buffer, size_t size) override {
        if (size == 0) return 0;
        const uint8_t* ptr = buffer;
        size_t remaining = size;

        if (_hasCarry && remaining > 0) {
            uint8_t pair[2] = {_carryByte, ptr[0]};
            _hasCarry = false;
            ptr++;
            remaining--;
            write(pair, 2);
        }

        if ((remaining % 2) != 0) {
            _carryByte = ptr[remaining - 1];
            _hasCarry = true;
            remaining--;
        }

        if (remaining == 0) return size;

        if (!_startedPlayback) {
            size_t space = _prebufCap - _prebufLen;
            size_t toCopy = (remaining < space) ? remaining : space;
            memcpy(_prebuf + _prebufLen, ptr, toCopy);
            _prebufLen += toCopy;
            ptr += toCopy;
            remaining -= toCopy;

            if (_prebufLen >= _prebufCap) {
                _startedPlayback = true;
                g_robotState = STATE_SPEAKING;
                writeAlignedPcmToI2S(_prebuf, _prebufLen);
                _prebufLen = 0;
            }
        }

        if (_startedPlayback && remaining > 0) {
            writeAlignedPcmToI2S(ptr, remaining);
        }

        return size;
    }

    void flushRemaining() {
        if (!_startedPlayback && _prebufLen > 0) {
            _startedPlayback = true;
            g_robotState = STATE_SPEAKING;
            writeAlignedPcmToI2S(_prebuf, _prebufLen);
            _prebufLen = 0;
        }
        g_speechBouncePx = 0;
        i2s_zero_dma_buffer(AMP_I2S_PORT);
    }

    size_t totalPlayedBytes() const { return _totalPlayedBytes; }

    int available() override { return 0; }
    int read() override { return -1; }
    int peek() override { return -1; }
    void flush() override {}
};

// ============================================================================
// Core 1: Audio Hardware & Network Pipeline
// ============================================================================

static void initAudioI2S() {
    i2s_config_t micCfg = {};
    micCfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
    micCfg.sample_rate = MIC_SAMPLE_RATE;
    micCfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    micCfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    micCfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    micCfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    micCfg.dma_buf_count = 6;
    micCfg.dma_buf_len = IO_CHUNK_SAMPLES;

    i2s_pin_config_t micPins = {};
    micPins.bck_io_num = PIN_MIC_BCLK;
    micPins.ws_io_num = PIN_MIC_WS;
    micPins.data_out_num = I2S_PIN_NO_CHANGE;
    micPins.data_in_num = PIN_MIC_SD;

    ESP_ERROR_CHECK(i2s_driver_install(MIC_I2S_PORT, &micCfg, 0, nullptr));
    ESP_ERROR_CHECK(i2s_set_pin(MIC_I2S_PORT, &micPins));
    i2s_zero_dma_buffer(MIC_I2S_PORT);

    i2s_config_t ampCfg = {};
    ampCfg.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
    ampCfg.sample_rate = TTS_DEFAULT_RATE;
    ampCfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    ampCfg.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
    ampCfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    ampCfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    ampCfg.dma_buf_count = 6;
    ampCfg.dma_buf_len = IO_CHUNK_SAMPLES;
    ampCfg.tx_desc_auto_clear = true;

    i2s_pin_config_t ampPins = {};
    ampPins.bck_io_num = PIN_AMP_BCLK;
    ampPins.ws_io_num = PIN_AMP_LRC;
    ampPins.data_out_num = PIN_AMP_DIN;
    ampPins.data_in_num = I2S_PIN_NO_CHANGE;

    ESP_ERROR_CHECK(i2s_driver_install(AMP_I2S_PORT, &ampCfg, 0, nullptr));
    ESP_ERROR_CHECK(i2s_set_pin(AMP_I2S_PORT, &ampPins));
    i2s_zero_dma_buffer(AMP_I2S_PORT);
}

static bool ensureWiFiAndClock() {
    if (WiFi.status() == WL_CONNECTED && time(nullptr) > 1700000000) {
        return true;
    }

    g_robotState = STATE_BOOT;
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t startMs = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - startMs < 15000)) {
        delay(250);
    }
    if (WiFi.status() != WL_CONNECTED) {
        g_robotState = STATE_ERROR;
        delay(2000);
        g_robotState = STATE_IDLE;
        return false;
    }

    if (time(nullptr) < 1700000000) {
        configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
        uint32_t ntpStart = millis();
        while (time(nullptr) < 1700000000 && (millis() - ntpStart < 10000)) {
            delay(200);
        }
    }

    g_robotState = STATE_IDLE;
    return time(nullptr) > 1700000000;
}

static int32_t readAndFilterMicChunk(int16_t* outSamples, size_t maxSamples, size_t& outCount) {
    int32_t rawBuf[IO_CHUNK_SAMPLES];
    size_t bytesRead = 0;
    outCount = 0;
    if (i2s_read(MIC_I2S_PORT, rawBuf, sizeof(rawBuf), &bytesRead, pdMS_TO_TICKS(50)) != ESP_OK || bytesRead == 0) {
        return 0;
    }
    size_t count = bytesRead / sizeof(int32_t);
    if (count > maxSamples) count = maxSamples;

    uint64_t sumSq = 0;
    for (size_t i = 0; i < count; ++i) {
        int32_t x = rawBuf[i] >> MIC_BIT_SHIFT;
        int32_t y = (x - g_prevX) + ((g_prevY * 255) >> 8);
        g_prevX = x;
        g_prevY = y;
        if (y > 32767)  y = 32767;
        if (y < -32768) y = -32768;
        int16_t s = static_cast<int16_t>(y);
        outSamples[i] = s;
        sumSq += static_cast<uint64_t>(static_cast<int32_t>(s) * s);
    }
    outCount = count;
    return (count > 0) ? static_cast<int32_t>(sqrtf(static_cast<float>(sumSq) / count)) : 0;
}

static void flushMicAfterPlayback() {
    // Half-duplex echo protection: wait for speaker acoustic decay, flush DMA RX
    // buffers, reset IIR DC-blocker state, and clear the VAD pre-roll ring buffer.
    delay(VAD_POST_PLAYBACK_COOLDOWN_MS);
    i2s_zero_dma_buffer(MIC_I2S_PORT);
    g_prevX = 0;
    g_prevY = 0;
    g_prerollWriteIdx = 0;
    g_prerollFull = false;
    g_vadOnsetCount = 0;

    int16_t dummy[IO_CHUNK_SAMPLES];
    size_t count = 0;
    for (int i = 0; i < 4; ++i) {
        readAndFilterMicChunk(dummy, IO_CHUNK_SAMPLES, count);
    }
}

static void calibrateNoiseFloor() {
    flushMicAfterPlayback();
    int16_t chunk[IO_CHUNK_SAMPLES];
    size_t count = 0;
    uint64_t rmsSum = 0;
    uint32_t frames = 0;

    // Sample ~480 ms (30 frames x 16 ms) of ambient room sound
    for (int i = 0; i < 30; ++i) {
        int32_t rms = readAndFilterMicChunk(chunk, IO_CHUNK_SAMPLES, count);
        if (count > 0) {
            rmsSum += static_cast<uint64_t>(rms);
            frames++;
        }
    }
    int32_t ambientRms = (frames > 0) ? static_cast<int32_t>(rmsSum / frames) : 0;
    if (ambientRms <= 0 || ambientRms > VAD_MAX_VALID_AMBIENT_RMS) {
        // Disconnected or floating mic line safety fallback: disable auto-VAD so it never loops
        g_autoVadEnabled = false;
        g_vadThresholdRms = 1500;
        Serial.printf("[VAD] Ambient RMS=%ld out of range (mic disconnected/floating) -> Auto-VAD disabled; use GPIO 1 PTT.\n",
                      static_cast<long>(ambientRms));
        return;
    }

    g_vadThresholdRms = max<int32_t>(VAD_MIN_RMS_THRESHOLD, ambientRms * 3);
    Serial.printf("[VAD] Calibrated ambient RMS=%ld -> Speech trigger threshold RMS=%ld\n",
                  static_cast<long>(ambientRms), static_cast<long>(g_vadThresholdRms));
}

static size_t capturePttUtterance() {
    g_robotState = STATE_LISTENING;
    size_t recorded = 0;
    g_prevX = 0;
    g_prevY = 0;
    i2s_zero_dma_buffer(MIC_I2S_PORT);

    while (digitalRead(PIN_PTT_BTN) == LOW && recorded < MAX_RECORD_SAMPLES) {
        size_t count = 0;
        size_t space = MAX_RECORD_SAMPLES - recorded;
        size_t req = (space < IO_CHUNK_SAMPLES) ? space : IO_CHUNK_SAMPLES;
        readAndFilterMicChunk(g_recordPsramBuf + recorded, req, count);
        recorded += count;
    }
    return recorded;
}

static size_t captureVadUtteranceFromPreroll() {
    g_robotState = STATE_LISTENING;
    size_t recorded = 0;

    // 1. Prepend the 250 ms circular pre-roll buffer so initial consonant is preserved
    if (g_prerollFull) {
        size_t tailLen = VAD_PREROLL_SAMPLES - g_prerollWriteIdx;
        memcpy(g_recordPsramBuf, g_prerollPsramBuf + g_prerollWriteIdx, tailLen * sizeof(int16_t));
        memcpy(g_recordPsramBuf + tailLen, g_prerollPsramBuf, g_prerollWriteIdx * sizeof(int16_t));
        recorded = VAD_PREROLL_SAMPLES;
    } else if (g_prerollWriteIdx > 0) {
        memcpy(g_recordPsramBuf, g_prerollPsramBuf, g_prerollWriteIdx * sizeof(int16_t));
        recorded = g_prerollWriteIdx;
    }
    g_prerollWriteIdx = 0;
    g_prerollFull = false;

    // 2. Record until 900 ms of trailing silence or MAX_RECORD_SAMPLES (8 s)
    uint32_t lastVoiceMs = millis();
    uint32_t activeVoiceFrames = VAD_ONSET_FRAMES_REQUIRED;
    while (recorded < MAX_RECORD_SAMPLES) {
        size_t count = 0;
        size_t space = MAX_RECORD_SAMPLES - recorded;
        size_t req = (space < IO_CHUNK_SAMPLES) ? space : IO_CHUNK_SAMPLES;
        int32_t rms = readAndFilterMicChunk(g_recordPsramBuf + recorded, req, count);
        recorded += count;

        if (rms >= g_vadThresholdRms) {
            lastVoiceMs = millis();
            activeVoiceFrames++;
        } else if ((millis() - lastVoiceMs) >= VAD_SILENCE_HANGOVER_MS) {
            break;
        }
    }

    // Require at least ~320 ms (20 chunks) of active voice frames above threshold
    if (activeVoiceFrames < 20) {
        return 0;
    }
    return recorded;
}

static void processTalkTurn(size_t sampleCount) {
    if (!ensureWiFiAndClock()) {
        return;
    }

    g_robotState = STATE_THINKING;
    const size_t payloadBytes = sampleCount * sizeof(int16_t);
    String url = String(GATEWAY_BASE_URL) + "/talk";

    HTTPClient http;
    WiFiClient plainClient;
    WiFiClientSecure tlsClient;

    http.setTimeout(30000);
    if (url.startsWith("https://")) {
        tlsClient.setCACert(GATEWAY_ROOT_CA_PEM);
        tlsClient.setTimeout(30);
        http.begin(tlsClient, url);
    } else {
        http.begin(plainClient, url);
    }

    const char* headerKeys[] = {"X-Sample-Rate", "X-Action"};
    http.collectHeaders(headerKeys, 2);
    http.addHeader("Authorization", String("Bearer ") + GATEWAY_BEARER_TOKEN);
    http.addHeader("X-Session-Id", String("esp32s3-") + String(static_cast<uint32_t>(ESP.getEfuseMac() & 0xFFFFFFFFULL), HEX));
    http.addHeader("Content-Type", "audio/pcm;rate=16000");

    // Zero automatic retries on error so a failure never loops and burns quota
    int status = http.POST(reinterpret_cast<uint8_t*>(g_recordPsramBuf), payloadBytes);
    if (status == 200) {
        uint32_t sampleRate = TTS_DEFAULT_RATE;
        if (http.hasHeader("X-Sample-Rate")) {
            uint32_t parsed = static_cast<uint32_t>(http.header("X-Sample-Rate").toInt());
            if (parsed >= 8000 && parsed <= 48000) {
                sampleRate = parsed;
            }
        }
        ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, sampleRate,
                                    I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));

        if (http.hasHeader("X-Action") && http.header("X-Action") == "WAVE") {
            triggerServoWaveIfEnabled();
        }

        RobotVoiceI2SSink sink(g_prebufPsram, PREBUFFER_BYTES);
        sink.reset();
        http.writeToStream(&sink);
        sink.flushRemaining();

        Serial.printf("[Robot] Turn complete (%u PCM bytes played) | Free Heap: %u\n",
                      static_cast<unsigned>(sink.totalPlayedBytes()), ESP.getFreeHeap());
        g_robotState = STATE_IDLE;
    } else if (status == 204) {
        // Silence or empty transcript
        i2s_zero_dma_buffer(AMP_I2S_PORT);
        g_robotState = STATE_IDLE;
    } else {
        i2s_zero_dma_buffer(AMP_I2S_PORT);
        Serial.printf("[ERROR] Gateway /talk returned HTTP %d\n", status);
        g_robotState = STATE_ERROR;
        delay(2500);
        g_robotState = STATE_IDLE;
    }

    http.end();
    flushMicAfterPlayback();
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    pinMode(PIN_PTT_BTN, INPUT_PULLUP);

    g_recordPsramBuf  = static_cast<int16_t*>(ps_malloc(MAX_RECORD_SAMPLES * sizeof(int16_t)));
    g_prerollPsramBuf = static_cast<int16_t*>(ps_malloc(VAD_PREROLL_SAMPLES * sizeof(int16_t)));
    g_prebufPsram     = static_cast<uint8_t*>(ps_malloc(PREBUFFER_BYTES));
    if (!g_recordPsramBuf || !g_prerollPsramBuf || !g_prebufPsram) {
        Serial.println("[FAIL] PSRAM allocation failed in robot_main!");
        while (true) { delay(1000); }
    }

    // Start Core 0 Display Task (priority 1, non-blocking ~30 FPS eye animation;
    // runs separately from Core 1 Arduino loop() so TLS & I2S never freeze the face)
    xTaskCreatePinnedToCore(displayTask, "DisplayTask", 6144, nullptr, 1, nullptr, 0);

    initAudioI2S();
    ensureWiFiAndClock();
    if (g_autoVadEnabled) {
        calibrateNoiseFloor();
    }
    Serial.printf("[READY] Tabletop Robot Online (Hands-Free VAD: %s). Speak or hold GPIO 1 (tap GPIO 1 to toggle VAD)!\n",
                  g_autoVadEnabled ? "ON" : "OFF");
}

void loop() {
    // 1. Check GPIO 1 Button: Hold (>= 400 ms) = Push-to-Talk; Quick Tap (< 350 ms) = Toggle Hands-Free VAD
    if (digitalRead(PIN_PTT_BTN) == LOW) {
        delay(30); // 30 ms press debounce
        if (digitalRead(PIN_PTT_BTN) == LOW) {
            uint32_t pressStartMs = millis();
            size_t samples = capturePttUtterance();
            uint32_t heldMs = millis() - pressStartMs;
            while (digitalRead(PIN_PTT_BTN) == LOW) {
                delay(10);
            }
            delay(30); // Release debounce

            if (heldMs < 350 && samples < MIN_VALID_SAMPLES) {
                g_autoVadEnabled = !g_autoVadEnabled;
                if (g_autoVadEnabled) {
                    calibrateNoiseFloor();
                } else {
                    flushMicAfterPlayback();
                }
                g_robotState = STATE_IDLE;
                Serial.printf("[MODE] Hands-Free Always-Listening VAD toggled %s\n",
                              g_autoVadEnabled ? "ON" : "OFF (Push-to-Talk only)");
            } else if (samples >= MIN_VALID_SAMPLES) {
                processTalkTurn(samples);
            } else {
                flushMicAfterPlayback();
                g_robotState = STATE_IDLE;
            }
            return;
        }
    }

    // 2. Hands-Free Always-Listening Energy VAD with 250 ms Circular Pre-Roll
    if (g_autoVadEnabled) {
        int16_t chunk[IO_CHUNK_SAMPLES];
        size_t count = 0;
        int32_t rms = readAndFilterMicChunk(chunk, IO_CHUNK_SAMPLES, count);
        for (size_t i = 0; i < count; ++i) {
            g_prerollPsramBuf[g_prerollWriteIdx++] = chunk[i];
            if (g_prerollWriteIdx >= VAD_PREROLL_SAMPLES) {
                g_prerollWriteIdx = 0;
                g_prerollFull = true;
            }
        }

        if (rms >= g_vadThresholdRms) {
            g_vadOnsetCount++;
            if (g_vadOnsetCount >= VAD_ONSET_FRAMES_REQUIRED) {
                g_vadOnsetCount = 0;
                size_t vadSamples = captureVadUtteranceFromPreroll();
                if (vadSamples >= MIN_VALID_SAMPLES) {
                    processTalkTurn(vadSamples);
                } else {
                    flushMicAfterPlayback();
                    g_robotState = STATE_IDLE;
                }
            }
        } else {
            g_vadOnsetCount = 0;
        }
    } else {
        delay(10);
    }
}
