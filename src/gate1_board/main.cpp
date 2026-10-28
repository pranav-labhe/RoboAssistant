#include <Arduino.h>
#include <esp_system.h>

// ============================================================================
// Gate 1: Board, Flash & Octal PSRAM Verification
// Build & Upload: pio run -e gate1_board -t upload -t monitor
// Pass Criteria:
//   1. Chip Model reports ESP32-S3
//   2. Flash Size reports 16 MB (or 8 MB for N8R8)
//   3. PSRAM Size reports ~8 MB and 1 MB R/W integrity check passes
// ============================================================================

static bool g_psramTestPassed = false;
static size_t g_psramTotal = 0;

static const char* resetReasonToString(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON:   return "POWERON_RESET";
        case ESP_RST_SW:        return "SOFTWARE_RESET";
        case ESP_RST_PANIC:     return "EXCEPTION_PANIC";
        case ESP_RST_TASK_WDT:  return "TASK_WATCHDOG";
        case ESP_RST_BROWNOUT:  return "BROWNOUT_RESET (Check 5V USB power!)";
        default:                return "OTHER_RESET";
    }
}

static bool runPsramIntegrityTest() {
    const size_t testBytes = 1024 * 1024; // 1 MB test block in PSRAM
    uint32_t* buf = static_cast<uint32_t*>(ps_malloc(testBytes));
    if (buf == nullptr) {
        Serial.println("[FAIL] ps_malloc(1 MB) returned NULL!");
        return false;
    }

    const size_t words = testBytes / sizeof(uint32_t);
    for (size_t i = 0; i < words; ++i) {
        buf[i] = static_cast<uint32_t>(i ^ 0xA5A55A5Au);
    }

    bool ok = true;
    for (size_t i = 0; i < words; ++i) {
        uint32_t expected = static_cast<uint32_t>(i ^ 0xA5A55A5Au);
        if (buf[i] != expected) {
            Serial.printf("[FAIL] PSRAM bit error at word %u: got 0x%08X, expected 0x%08X\n",
                          static_cast<unsigned>(i), buf[i], expected);
            ok = false;
            break;
        }
    }

    free(buf);
    return ok;
}

static void printBoardReport() {
    Serial.println("\n============================================================");
    Serial.println("  GATE 1: ESP32-S3 BOARD & PSRAM DIAGNOSTIC REPORT");
    Serial.println("============================================================");
    Serial.printf("Reset Reason   : %s\n", resetReasonToString(esp_reset_reason()));
    Serial.printf("Chip Model     : %s (Rev %d, %d Cores @ %u MHz)\n",
                  ESP.getChipModel(), ESP.getChipRevision(),
                  ESP.getChipCores(), ESP.getCpuFreqMHz());
    Serial.printf("Flash Size     : %.2f MB\n",
                  ESP.getFlashChipSize() / (1024.0f * 1024.0f));
    Serial.printf("Internal Heap  : %u KB free / %u KB total\n",
                  ESP.getFreeHeap() / 1024, ESP.getHeapSize() / 1024);
    Serial.printf("PSRAM Total    : %.2f MB (%u bytes)\n",
                  g_psramTotal / (1024.0f * 1024.0f),
                  static_cast<unsigned>(g_psramTotal));
    Serial.printf("PSRAM Free     : %.2f MB (%u bytes)\n",
                  ESP.getFreePsram() / (1024.0f * 1024.0f),
                  static_cast<unsigned>(ESP.getFreePsram()));

    if (g_psramTotal >= 4 * 1024 * 1024 && g_psramTestPassed) {
        Serial.println("------------------------------------------------------------");
        Serial.println(">>> GATE 1 STATUS: [PASS] Octal PSRAM verified & healthy! <<<");
        Serial.println("------------------------------------------------------------");
    } else {
        Serial.println("------------------------------------------------------------");
        Serial.println(">>> GATE 1 STATUS: [FAIL] PSRAM missing or failed R/W!   <<<");
        Serial.println("    1. Verify board_build.arduino.memory_type = qio_opi");
        Serial.println("    2. If module tin lacks R8 (N16R8/N8R8), return board.");
        Serial.println("------------------------------------------------------------");
    }
}

void setup() {
    Serial.begin(115200);
    // Allow up to 3 seconds for native USB CDC Serial monitor to attach
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    g_psramTotal = ESP.getPsramSize();
    if (g_psramTotal > 0) {
        g_psramTestPassed = runPsramIntegrityTest();
    }
    printBoardReport();
}

void loop() {
    delay(5000);
    printBoardReport();
}
