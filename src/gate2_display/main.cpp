#include <Arduino.h>
#include <math.h>
#include <string.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include "pins.h"

// ============================================================================
// Gate 2: ST7789 240x240 SPI Display & PSRAM Eye Sprite Renderer
// Build & Upload: pio run -e gate2_display -t upload -t monitor
// Wiring:
//   MOSI (SDA) -> GPIO 11, SCLK (SCL) -> GPIO 12, DC -> GPIO 9, RST -> GPIO 14, BL -> GPIO 13
//   7-pin display (no CS):   Default (TFT_HAS_CS_PIN 0 in include/pins.h, uses SPI_MODE3)
//   8-pin display (with CS): CS -> GPIO 10, set TFT_HAS_CS_PIN 1 in include/pins.h (uses SPI_MODE0)
// Pass Criteria:
//   - Animated robot eyes render crisply with zero tearing or flicker
//   - Serial prints sustained > 25 FPS using partial eye-band push from PSRAM
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
            cfg.freq_write = 40000000; // 40 MHz SPI
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
            cfg.invert           = true; // IPS ST7789 panels use inverted color
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
static constexpr int FACE_BAND_H = 170;
static constexpr int FACE_BAND_Y = 35;

enum DemoEmotion : uint8_t {
    EMO_IDLE = 0,
    EMO_LISTENING,
    EMO_THINKING,
    EMO_HAPPY,
    EMO_SURPRISED,
    EMO_SAD,
    EMO_SPEAKING,
    EMO_ERROR,
    EMO_COUNT
};

static const char* const kEmotionNames[EMO_COUNT] = {
    "IDLE", "LISTENING", "THINKING", "HAPPY", "SURPRISED", "SAD", "SPEAKING", "ERROR"
};

static bool        g_autoCycle      = true;
static uint8_t     g_manualEmotion  = EMO_IDLE;
static uint32_t    g_autoStartMs    = 0;
static char        g_cmdBuf[32]     = {0};
static size_t      g_cmdLen         = 0;

static uint32_t g_frameCount = 0;
static uint32_t g_lastFpsMs  = 0;

static void drawDemoEmotion(DemoEmotion emo, uint32_t nowMs) {
    g_faceSprite.fillScreen(TFT_BLACK);

    const uint16_t cyanColor  = g_tft.color565(0, 225, 255);
    const uint16_t greenColor = g_tft.color565(40, 255, 130);
    const uint16_t amberColor = g_tft.color565(255, 180, 30);
    const uint16_t redColor   = g_tft.color565(255, 70, 70);

    const int leftX  = 70;
    const int rightX = 170;
    const int eyeY   = 55;
    const int eyeW   = 56;

    // Animated lip-sync bounce for expressive speaking faces
    int bounce = static_cast<int>(7.0f + 7.0f * sinf(nowMs * 0.015f));
    uint16_t labelColor = cyanColor;

    switch (emo) {
        case EMO_IDLE: {
            int eyeH = 64;
            uint32_t blinkCycle = nowMs % 3400;
            if (blinkCycle < 80) {
                eyeH = 64 - static_cast<int>((blinkCycle * 58) / 80);
            } else if (blinkCycle < 160) {
                eyeH = 6 + static_cast<int>(((blinkCycle - 80) * 58) / 80);
            }
            int lookX = 0;
            uint32_t phase = (nowMs / 1200) % 4;
            if (phase == 1) lookX = -8;
            if (phase == 3) lookX = 8;

            int r = min(16, eyeH / 2);
            g_faceSprite.fillRoundRect(leftX + lookX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, r, cyanColor);
            g_faceSprite.fillRoundRect(rightX + lookX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, r, cyanColor);
            break;
        }
        case EMO_LISTENING: {
            int pulse = static_cast<int>(4.0f * sinf(nowMs * 0.012f));
            int w = 62 + pulse;
            int h = 74 + pulse;
            g_faceSprite.fillRoundRect(leftX - w / 2, eyeY - h / 2, w, h, 18, greenColor);
            g_faceSprite.fillRoundRect(rightX - w / 2, eyeY - h / 2, w, h, 18, greenColor);
            labelColor = greenColor;
            break;
        }
        case EMO_THINKING: {
            int scanX = static_cast<int>(12.0f * sinf(nowMs * 0.008f));
            int thinkY = eyeY - 14;
            g_faceSprite.fillRoundRect(leftX + scanX - 26, thinkY - 22, 52, 44, 14, amberColor);
            g_faceSprite.fillRoundRect(rightX + scanX - 26, thinkY - 22, 52, 44, 14, amberColor);
            labelColor = amberColor;
            break;
        }
        case EMO_HAPPY: {
            int y = eyeY - (bounce / 3);
            g_faceSprite.fillCircle(leftX, y, 28, cyanColor);
            g_faceSprite.fillCircle(leftX, y + 16, 26, TFT_BLACK);
            g_faceSprite.fillCircle(rightX, y, 28, cyanColor);
            g_faceSprite.fillCircle(rightX, y + 16, 26, TFT_BLACK);

            int smileR = 16 + (bounce / 2);
            g_faceSprite.fillCircle(120, 108, smileR, cyanColor);
            g_faceSprite.fillRect(120 - smileR - 2, 108 - smileR - 2, (smileR + 2) * 2, smileR - (bounce / 3), TFT_BLACK);
            break;
        }
        case EMO_SURPRISED: {
            int y = eyeY - (bounce / 3);
            int rad = 28 + (bounce / 3);
            g_faceSprite.fillCircle(leftX, y, rad, cyanColor);
            g_faceSprite.fillCircle(leftX, y, 10, TFT_BLACK);
            g_faceSprite.fillCircle(rightX, y, rad, cyanColor);
            g_faceSprite.fillCircle(rightX, y, 10, TFT_BLACK);

            int mouthR = 10 + (bounce / 2);
            g_faceSprite.fillCircle(120, 114, mouthR, cyanColor);
            g_faceSprite.fillCircle(120, 114, max(4, mouthR - 5), TFT_BLACK);
            break;
        }
        case EMO_SAD: {
            int y = eyeY - (bounce / 3);
            int h = 50 + (bounce / 3);
            g_faceSprite.fillRoundRect(leftX - eyeW / 2, y - h / 2, eyeW, h, 14, cyanColor);
            g_faceSprite.fillRoundRect(rightX - eyeW / 2, y - h / 2, eyeW, h, 14, cyanColor);
            g_faceSprite.fillTriangle(leftX - 30, y - 28, leftX + 30, y - 28, leftX - 30, y - 8, TFT_BLACK);
            g_faceSprite.fillTriangle(rightX - 30, y - 28, rightX + 30, y - 28, rightX + 30, y - 8, TFT_BLACK);

            int mouthW = 36 + bounce;
            int mouthH = 5 + (bounce / 2);
            g_faceSprite.fillRoundRect(120 - mouthW / 2, 116 - mouthH / 2, mouthW, mouthH, mouthH / 2, cyanColor);
            break;
        }
        case EMO_SPEAKING: {
            int y = eyeY - (bounce / 3);
            int h = 60 + (bounce / 2);
            g_faceSprite.fillRoundRect(leftX - eyeW / 2, y - h / 2, eyeW, h, 16, cyanColor);
            g_faceSprite.fillRoundRect(rightX - eyeW / 2, y - h / 2, eyeW, h, 16, cyanColor);

            int mouthW = 44 + bounce * 2;
            int mouthH = 6 + bounce;
            g_faceSprite.fillRoundRect(120 - mouthW / 2, 114 - mouthH / 2, mouthW, mouthH, mouthH / 2, cyanColor);
            break;
        }
        case EMO_ERROR:
        default: {
            g_faceSprite.fillRoundRect(leftX - 28, eyeY - 14, 56, 28, 8, redColor);
            g_faceSprite.fillRoundRect(rightX - 28, eyeY - 14, 56, 28, 8, redColor);
            g_faceSprite.fillRoundRect(96, 112, 48, 6, 3, redColor);
            labelColor = redColor;
            break;
        }
    }

    g_faceSprite.setTextDatum(TC_DATUM);
    g_faceSprite.setTextSize(2);
    g_faceSprite.setTextColor(labelColor, TFT_BLACK);
    g_faceSprite.drawString(kEmotionNames[emo], 120, 146);

    g_faceSprite.pushSprite(0, FACE_BAND_Y);
}

static void handleSerialCommand(const char* cmd) {
    if (strcmp(cmd, "AUTO") == 0) {
        g_autoCycle = true;
        g_autoStartMs = millis();
        Serial.println("[Gate 2] Mode -> AUTO");
        return;
    }

    int match = -1;
    if (strcmp(cmd, "WAVE") == 0) {
        match = EMO_HAPPY;
    } else if (strcmp(cmd, "NEUTRAL") == 0) {
        match = EMO_SPEAKING;
    } else {
        for (int i = 0; i < EMO_COUNT; ++i) {
            if (strcmp(cmd, kEmotionNames[i]) == 0) {
                match = i;
                break;
            }
        }
    }

    if (match >= 0) {
        g_autoCycle = false;
        g_manualEmotion = static_cast<uint8_t>(match);
        Serial.printf("[Gate 2] Emotion -> %s\n", kEmotionNames[match]);
    }
}

static void pollSerialCommands() {
    while (Serial.available() > 0) {
        char ch = static_cast<char>(Serial.read());
        if (ch == '\r' || ch == '\n') {
            if (g_cmdLen > 0) {
                g_cmdBuf[g_cmdLen] = '\0';
                handleSerialCommand(g_cmdBuf);
                g_cmdLen = 0;
            }
        } else if (ch != ' ' && g_cmdLen < (sizeof(g_cmdBuf) - 1)) {
            if (ch >= 'a' && ch <= 'z') {
                ch = static_cast<char>(ch - ('a' - 'A'));
            }
            g_cmdBuf[g_cmdLen++] = ch;
        }
    }
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    Serial.println("\n============================================================");
    Serial.println("  GATE 2: ST7789 8-EMOTION DISPLAY & PSRAM SPRITE ENGINE");
    Serial.printf("  Config: PIN_TFT_CS=%d, TFT_SPI_MODE=%d\n", PIN_TFT_CS, TFT_SPI_MODE);
    Serial.println("============================================================");

    g_tft.init();
    g_tft.setRotation(0);
    g_tft.setBrightness(220);
    g_tft.fillScreen(TFT_BLACK);

    g_faceSprite.setPsram(true);
    g_faceSprite.setColorDepth(16);
    if (!g_faceSprite.createSprite(FACE_BAND_W, FACE_BAND_H)) {
        Serial.println("[FAIL] Could not allocate 240x170 face sprite in PSRAM!");
        while (true) { delay(1000); }
    }

    Serial.printf("[OK] Face sprite allocated in PSRAM (Free PSRAM: %u bytes)\n",
                  static_cast<unsigned>(ESP.getFreePsram()));
    g_autoStartMs = millis();
    g_lastFpsMs   = millis();
}

void loop() {
    pollSerialCommands();
    uint32_t now = millis();

    DemoEmotion active = static_cast<DemoEmotion>(g_manualEmotion);
    if (g_autoCycle) {
        active = static_cast<DemoEmotion>(((now - g_autoStartMs) / 3500) % EMO_COUNT);
    }

    drawDemoEmotion(active, now);
    g_frameCount++;

    if (now - g_lastFpsMs >= 2000) {
        float fps = (g_frameCount * 1000.0f) / (now - g_lastFpsMs);
        Serial.printf("[Gate 2] Render Speed: %.1f FPS | Emotion: %s | Free PSRAM: %u bytes\n",
                      fps, kEmotionNames[active], static_cast<unsigned>(ESP.getFreePsram()));
        g_frameCount = 0;
        g_lastFpsMs = now;
    }

    delay(16); // Target ~35-45 FPS
}
