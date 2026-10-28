#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>
#include "certs.h"
#include "robot_secrets.h"

// ============================================================================
// Gate 6: Wi-Fi, NTP Clock Sync & Verified TLS Gateway `/health` Check
// Build & Upload: pio run -e gate6_health -t upload -t monitor
// Secrets loaded from: C:\Users\DELL\.robo_secrets\robot_secrets.h
// Pass Criteria:
//   1. Connects to 2.4 GHz Wi-Fi with WiFi.setSleep(false)
//   2. Syncs UTC clock via NTP (> year 2024) before first TLS handshake
//   3. GET {GATEWAY_BASE_URL}/health returns HTTP 200 with verified Root CA
//   4. Free heap returns to baseline after connection closes
// ============================================================================

static void connectWiFiAndSyncNTP() {
    Serial.printf("[Gate 6] Connecting to 2.4 GHz Wi-Fi SSID: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false); // Disable modem sleep to cut audio/TLS latency
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t startMs = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(300);
        Serial.print(".");
        if (millis() - startMs > 20000) {
            Serial.println("\n[FAIL] Wi-Fi connection timed out (Check 2.4 GHz SSID & password in robot_secrets.h)");
            return;
        }
    }
    Serial.printf("\n[OK] Wi-Fi Connected! IP: %s | RSSI: %d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());

    // Critical TLS requirement: sync clock via NTP so certificate notBefore check passes
    Serial.print("[Gate 6] Syncing clock via NTP...");
    configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    time_t now = time(nullptr);
    uint32_t ntpStart = millis();
    while (now < 1700000000 && (millis() - ntpStart < 15000)) {
        delay(250);
        Serial.print(".");
        now = time(nullptr);
    }
    Serial.println();
    if (now < 1700000000) {
        Serial.println("[FAIL] NTP sync timed out! TLS certificates cannot be verified at 1970.");
    } else {
        struct tm timeinfo;
        gmtime_r(&now, &timeinfo);
        Serial.printf("[OK] UTC Clock Synced: %04d-%02d-%02d %02d:%02d:%02d UTC\n",
                      timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
                      timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
    }
}

static void checkGatewayHealth() {
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFiAndSyncNTP();
        if (WiFi.status() != WL_CONNECTED) return;
    }

    const uint32_t heapBefore = ESP.getFreeHeap();
    String url = String(GATEWAY_BASE_URL) + "/health";
    Serial.printf("\n[Gate 6] Requesting %s | Heap Before: %u bytes\n", url.c_str(), heapBefore);

    HTTPClient http;
    WiFiClient plainClient;
    WiFiClientSecure tlsClient;

    bool began = false;
    if (url.startsWith("https://")) {
        // Strictly verify Root CA — NEVER use setInsecure()
        tlsClient.setCACert(GATEWAY_ROOT_CA_PEM);
        tlsClient.setTimeout(15);
        began = http.begin(tlsClient, url);
    } else {
        began = http.begin(plainClient, url);
    }

    if (!began) {
        Serial.println("[FAIL] HTTPClient.begin() failed!");
        return;
    }

    String authHeader = String("Bearer ") + GATEWAY_BEARER_TOKEN;
    http.addHeader("Authorization", authHeader);

    int statusCode = http.GET();
    if (statusCode == 200) {
        String body = http.getString();
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, body);
        if (!err) {
            Serial.printf("[PASS] Gateway /health 200 OK -> status=%s, llm=%s\n",
                          doc["status"] | "unknown",
                          doc["providers"]["llm"] | "unknown");
        } else {
            Serial.printf("[PASS] Gateway /health 200 OK -> %s\n", body.c_str());
        }
    } else {
        Serial.printf("[FAIL] Gateway /health failed with status: %d (%s)\n",
                      statusCode, http.errorToString(statusCode).c_str());
    }

    http.end();
    const uint32_t heapAfter = ESP.getFreeHeap();
    Serial.printf("[Gate 6] Heap After: %u bytes (Delta: %d bytes)\n",
                  heapAfter, static_cast<int>(heapAfter) - static_cast<int>(heapBefore));
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    Serial.println("\n============================================================");
    Serial.println("  GATE 6: WI-FI, NTP SYNC & VERIFIED TLS /health CHECK");
    Serial.println("============================================================");

    connectWiFiAndSyncNTP();
    checkGatewayHealth();
}

void loop() {
    delay(10000);
    checkGatewayHealth();
}
