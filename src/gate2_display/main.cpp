#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include "pins.h"

// ============================================================================
// Gate 2: ST7789 240x240 SPI Display & PSRAM Eye Sprite Renderer
// Build & Upload: pio run -e gate2_display -t upload -t monitor
// Wiring:
//   MOSI (SDA) -> GPIO 11, SCLK (SCL) -> GPIO 12, DC -> GPIO 9, RST -> GPIO 14, BL -> GPIO 13
//   8-pin display (with CS): CS -> GPIO 10, set TFT_HAS_CS_PIN 1 in include/pins.h
//   7-pin display (no CS):   set TFT_HAS_CS_PIN 0 in include/pins.h (uses SPI_MODE3)
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

static RobotDisplay g_tft;
static lgfx::LGFX_Sprite g_eyeSprite(&g_tft);

static constexpr int EYE_BAND_W = 240;
static constexpr int EYE_BAND_H = 110;
static constexpr int EYE_BAND_Y = 65;

static uint32_t g_frameCount = 0;
static uint32_t g_lastFpsMs  = 0;

static void drawRobotEyes(int eyeHeight, int pupilOffsetX, int pupilOffsetY) {
    g_eyeSprite.fillScreen(TFT_BLACK);

    const uint16_t eyeColor = g_tft.color565(0, 220, 255); // Bright cyan robot eyes
    const int leftCenterX   = 70 + pupilOffsetX;
    const int rightCenterX  = 170 + pupilOffsetX;
    const int centerY       = (EYE_BAND_H / 2) + pupilOffsetY;
    const int eyeWidth      = 56;
    const int radius        = min(16, eyeHeight / 2);

    g_eyeSprite.fillRoundRect(leftCenterX - eyeWidth / 2, centerY - eyeHeight / 2,
                              eyeWidth, eyeHeight, radius, eyeColor);
    g_eyeSprite.fillRoundRect(rightCenterX - eyeWidth / 2, centerY - eyeHeight / 2,
                              eyeWidth, eyeHeight, radius, eyeColor);

    // Push ONLY the 240x110 eye bounding box instead of the full 240x240 screen
    g_eyeSprite.pushSprite(0, EYE_BAND_Y);
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    Serial.println("\n============================================================");
    Serial.println("  GATE 2: ST7789 DISPLAY & PSRAM SPRITE ENGINE TEST");
    Serial.printf("  Config: PIN_TFT_CS=%d, TFT_SPI_MODE=%d\n", PIN_TFT_CS, TFT_SPI_MODE);
    Serial.println("============================================================");

    g_tft.init();
    g_tft.setRotation(0);
    g_tft.setBrightness(220);
    g_tft.fillScreen(TFT_BLACK);

    g_eyeSprite.setPsram(true);
    g_eyeSprite.setColorDepth(16);
    if (!g_eyeSprite.createSprite(EYE_BAND_W, EYE_BAND_H)) {
        Serial.println("[FAIL] Could not allocate 240x110 eye sprite in PSRAM!");
        while (true) { delay(1000); }
    }

    Serial.printf("[OK] Eye sprite allocated in PSRAM (Free PSRAM: %u bytes)\n",
                  static_cast<unsigned>(ESP.getFreePsram()));
    g_lastFpsMs = millis();
}

void loop() {
    uint32_t now = millis();

    // Blink every 3.2 seconds (160 ms blink cycle)
    uint32_t cycle = now % 3200;
    int eyeH = 68;
    if (cycle < 80) {
        eyeH = 68 - static_cast<int>((cycle * 62) / 80);
    } else if (cycle < 160) {
        eyeH = 6 + static_cast<int>(((cycle - 80) * 62) / 80);
    }

    // Subtle saccade look left/right every 4 seconds
    int offsetX = 0;
    uint32_t lookPhase = (now / 2000) % 4;
    if (lookPhase == 1) offsetX = -10;
    if (lookPhase == 3) offsetX = 10;

    drawRobotEyes(eyeH, offsetX, 0);
    g_frameCount++;

    if (now - g_lastFpsMs >= 2000) {
        float fps = (g_frameCount * 1000.0f) / (now - g_lastFpsMs);
        Serial.printf("[Gate 2] Render Speed: %.1f FPS | Free PSRAM: %u bytes\n",
                      fps, static_cast<unsigned>(ESP.getFreePsram()));
        g_frameCount = 0;
        g_lastFpsMs = now;
    }

    delay(16); // Target ~35-45 FPS
}
