#pragma once

// ============================================================================
// Runtime Robot Configuration (NVS flash) + LAN HTTP API
//   GET  http://<robot-ip>/config   -> current config (API keys MASKED)
//   POST http://<robot-ip>/config   -> partial JSON update, validated atomically
//   Both require: Authorization: Bearer <ROBOT_CONFIG_TOKEN>
//
// Included ONLY by src/robot_main/main.cpp (single translation unit).
//
// Security model (read before enabling Direct Mode):
//   - LAN only. Never port-forward port 80 on your router.
//   - Plain HTTP: the token and any keys you POST cross your Wi-Fi in clear
//     text inside the WPA2 tunnel. Rotate the token if the LAN is shared.
//   - NVS is NOT encrypted: anyone with physical USB access can dump flash and
//     read stored API keys. Use a restricted, quota-capped Gemini key.
// ============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include "robot_secrets.h"

// Optional overrides in ~/.robo_secrets/robot_secrets.h (all have safe defaults)
#ifndef ROBOT_CONFIG_TOKEN
#define ROBOT_CONFIG_TOKEN GATEWAY_BEARER_TOKEN
#endif
#ifndef ROBOT_DEFAULT_MODE
#define ROBOT_DEFAULT_MODE "gateway"
#endif
#ifndef DIRECT_GEMINI_API_KEY
#define DIRECT_GEMINI_API_KEY ""
#endif
#ifndef DIRECT_CHIT_API_KEY
#define DIRECT_CHIT_API_KEY ""
#endif

static constexpr size_t CONFIG_MAX_BODY_BYTES = 2048;
static constexpr size_t CONFIG_MIN_TOKEN_LEN  = 16;

struct RobotConfig {
    String mode;          // "gateway" | "direct"
    String llm;           // "gemini" | "chit"   (Direct Mode only)
    bool   vad;           // Hands-Free VAD enabled at boot
    String gatewayUrl;    // Gateway Mode base URL
    String gatewayToken;  // Gateway Mode Bearer token
    String geminiKey;     // Direct Mode (STT + TTS, and LLM when llm=gemini)
    String sttModel;
    String llmModel;
    String ttsModel;
    String ttsVoice;
    String chitUrl;       // Direct Mode, llm=chit
    String chitKey;
};

static RobotConfig g_cfg;
static Preferences g_prefs;
static WebServer   g_configServer(80);
static bool        g_cfgVadChangedPending = false;

static void configSetDefaults(RobotConfig& c) {
    c.mode         = ROBOT_DEFAULT_MODE;
    c.llm          = "gemini";
    c.vad          = (ENABLE_AUTO_VAD_DEFAULT != 0);
    c.gatewayUrl   = GATEWAY_BASE_URL;
    c.gatewayToken = GATEWAY_BEARER_TOKEN;
    c.geminiKey    = DIRECT_GEMINI_API_KEY;
    c.sttModel     = "gemini-3.1-flash-lite";
    c.llmModel     = "gemini-3.1-flash-lite";
    c.ttsModel     = "gemini-3.8-flash-tts";
    c.ttsVoice     = "Puck";
    c.chitUrl      = "https://api.chitt.online";
    c.chitKey      = DIRECT_CHIT_API_KEY;
}

static String prefStr(const char* key, const String& fallback) {
    return g_prefs.isKey(key) ? g_prefs.getString(key, fallback) : fallback;
}

static void configLoad() {
    configSetDefaults(g_cfg);
    // Read-write open creates the namespace on first boot (read-only open fails if absent)
    if (!g_prefs.begin("robo", false)) {
        Serial.println("[CONFIG] NVS open failed -> using compile-time defaults");
        return;
    }
    g_cfg.mode         = prefStr("mode", g_cfg.mode);
    g_cfg.llm          = prefStr("llm", g_cfg.llm);
    g_cfg.vad          = g_prefs.isKey("vad") ? g_prefs.getBool("vad", g_cfg.vad) : g_cfg.vad;
    g_cfg.gatewayUrl   = prefStr("gw_url", g_cfg.gatewayUrl);
    g_cfg.gatewayToken = prefStr("gw_tok", g_cfg.gatewayToken);
    g_cfg.geminiKey    = prefStr("gem_key", g_cfg.geminiKey);
    g_cfg.sttModel     = prefStr("stt_m", g_cfg.sttModel);
    g_cfg.llmModel     = prefStr("llm_m", g_cfg.llmModel);
    g_cfg.ttsModel     = prefStr("tts_m", g_cfg.ttsModel);
    g_cfg.ttsVoice     = prefStr("tts_v", g_cfg.ttsVoice);
    g_cfg.chitUrl      = prefStr("chit_url", g_cfg.chitUrl);
    g_cfg.chitKey      = prefStr("chit_key", g_cfg.chitKey);
    g_prefs.end();
}

static bool configSave(const RobotConfig& c) {
    if (!g_prefs.begin("robo", false)) {
        return false;
    }
    bool ok = true;
    ok &= g_prefs.putString("mode", c.mode) > 0;
    ok &= g_prefs.putString("llm", c.llm) > 0;
    ok &= g_prefs.putBool("vad", c.vad) > 0;
    ok &= g_prefs.putString("gw_url", c.gatewayUrl) > 0;
    // Empty strings are legal (key cleared); putString returns 0 for "" so don't AND them
    g_prefs.putString("gw_tok", c.gatewayToken);
    g_prefs.putString("gem_key", c.geminiKey);
    ok &= g_prefs.putString("stt_m", c.sttModel) > 0;
    ok &= g_prefs.putString("llm_m", c.llmModel) > 0;
    ok &= g_prefs.putString("tts_m", c.ttsModel) > 0;
    ok &= g_prefs.putString("tts_v", c.ttsVoice) > 0;
    ok &= g_prefs.putString("chit_url", c.chitUrl) > 0;
    g_prefs.putString("chit_key", c.chitKey);
    g_prefs.end();
    return ok;
}

// ---------------------------------------------------------------------------
// Validation (every value must pass BEFORE anything is written — atomic update)
// ---------------------------------------------------------------------------

// Model / voice names are interpolated into URL paths -> strict identifier charset
static bool isSafeIdent(const String& s, size_t maxLen) {
    if (s.length() == 0 || s.length() > maxLen) return false;
    for (size_t i = 0; i < s.length(); ++i) {
        char ch = s[i];
        if (!(isalnum(static_cast<unsigned char>(ch)) || ch == '.' || ch == '-' || ch == '_')) return false;
    }
    return true;
}

// Keys / tokens go into HTTP headers -> printable ASCII only (blocks CR/LF header injection)
static bool isSafeSecret(const String& s, size_t maxLen) {
    if (s.length() > maxLen) return false;
    for (size_t i = 0; i < s.length(); ++i) {
        char ch = s[i];
        if (ch < 0x21 || ch > 0x7E || ch == '"' || ch == '\\') return false;
    }
    return true;
}

static bool isSafeUrl(const String& s) {
    if (s.length() < 10 || s.length() > 128) return false;
    if (!(s.startsWith("http://") || s.startsWith("https://"))) return false;
    return isSafeSecret(s, 128);
}

static String stripTrailingSlash(String s) {
    while (s.endsWith("/")) s.remove(s.length() - 1);
    return s;
}

static String maskSecret(const String& s) {
    if (s.length() == 0) return "";
    if (s.length() <= 8) return "****";
    return String("****") + s.substring(s.length() - 4);
}

static bool constTimeEquals(const String& a, const String& b) {
    const size_t la = a.length();
    const size_t lb = b.length();
    const size_t n  = (la > lb) ? la : lb;
    uint8_t diff = (la != lb) ? 1 : 0;
    for (size_t i = 0; i < n; ++i) {
        uint8_t ca = (i < la) ? static_cast<uint8_t>(a[i]) : 0;
        uint8_t cb = (i < lb) ? static_cast<uint8_t>(b[i]) : 0;
        diff |= static_cast<uint8_t>(ca ^ cb);
    }
    return diff == 0;
}

static bool configAuthorized() {
    const String expected = ROBOT_CONFIG_TOKEN;
    if (expected.length() < CONFIG_MIN_TOKEN_LEN) {
        // Refuse to run an unauthenticated config API with a weak/placeholder token
        g_configServer.send(503, "application/json", "{\"error\":\"ROBOT_CONFIG_TOKEN too short (min 16 chars)\"}");
        return false;
    }
    String auth = g_configServer.header("Authorization");
    if (!auth.startsWith("Bearer ") || !constTimeEquals(auth.substring(7), expected)) {
        g_configServer.send(401, "application/json", "{\"error\":\"unauthorized\"}");
        return false;
    }
    return true;
}

static String configToMaskedJson(const RobotConfig& c) {
    JsonDocument doc;
    doc["mode"]           = c.mode;
    doc["llm_provider"]   = c.llm;
    doc["vad_enabled"]    = c.vad;
    doc["gateway_url"]    = c.gatewayUrl;
    doc["gateway_token"]  = maskSecret(c.gatewayToken);
    doc["gemini_api_key"] = maskSecret(c.geminiKey);
    doc["stt_model"]      = c.sttModel;
    doc["llm_model"]      = c.llmModel;
    doc["tts_model"]      = c.ttsModel;
    doc["tts_voice"]      = c.ttsVoice;
    doc["chit_url"]       = c.chitUrl;
    doc["chit_api_key"]   = maskSecret(c.chitKey);
    doc["ip"]             = WiFi.localIP().toString();
    doc["free_psram"]     = ESP.getFreePsram();
    doc["free_heap"]      = ESP.getFreeHeap();
    String out;
    serializeJson(doc, out);
    return out;
}

static void handleGetConfig() {
    if (!configAuthorized()) return;
    g_configServer.send(200, "application/json", configToMaskedJson(g_cfg));
}

static void handlePostConfig() {
    if (!configAuthorized()) return;

    const String body = g_configServer.arg("plain");
    if (body.length() == 0 || body.length() > CONFIG_MAX_BODY_BYTES) {
        g_configServer.send(413, "application/json", "{\"error\":\"body empty or > 2048 bytes\"}");
        return;
    }

    JsonDocument doc;
    if (deserializeJson(doc, body) || !doc.is<JsonObject>()) {
        g_configServer.send(400, "application/json", "{\"error\":\"invalid JSON object\"}");
        return;
    }
    JsonObject obj = doc.as<JsonObject>();

    // factory_reset: wipe NVS and return to compile-time defaults
    if (obj["factory_reset"] | false) {
        if (g_prefs.begin("robo", false)) {
            g_prefs.clear();
            g_prefs.end();
        }
        bool oldVad = g_cfg.vad;
        configSetDefaults(g_cfg);
        g_cfgVadChangedPending = (oldVad != g_cfg.vad);
        g_configServer.send(200, "application/json", configToMaskedJson(g_cfg));
        Serial.println("[CONFIG] Factory reset -> compile-time defaults");
        return;
    }

    static const char* const kAllowed[] = {
        "mode", "llm_provider", "vad_enabled", "gateway_url", "gateway_token",
        "gemini_api_key", "stt_model", "llm_model", "tts_model", "tts_voice",
        "chit_url", "chit_api_key"};

    // Strict schema: reject unknown fields (same rule as the Chit API)
    for (JsonPair kv : obj) {
        bool known = false;
        for (const char* k : kAllowed) {
            if (strcmp(kv.key().c_str(), k) == 0) { known = true; break; }
        }
        if (!known) {
            g_configServer.send(400, "application/json",
                                String("{\"error\":\"unknown field: ") + kv.key().c_str() + "\"}");
            return;
        }
    }

    RobotConfig next = g_cfg;  // Work on a copy; commit only if every field validates
    String err;

    auto takeStr = [&](const char* key, String& dst, bool (*check)(const String&)) {
        if (!obj[key].isNull()) {
            if (!obj[key].is<const char*>()) { err = String(key) + " must be a string"; return; }
            String v = obj[key].as<const char*>();
            v.trim();
            if (!check(v)) { err = String("invalid value for ") + key; return; }
            dst = v;
        }
    };

    takeStr("mode", next.mode, [](const String& v) { return v == "gateway" || v == "direct"; });
    takeStr("llm_provider", next.llm, [](const String& v) { return v == "gemini" || v == "chit"; });
    takeStr("gateway_url", next.gatewayUrl, [](const String& v) { return isSafeUrl(v); });
    takeStr("gateway_token", next.gatewayToken, [](const String& v) { return isSafeSecret(v, 128); });
    takeStr("gemini_api_key", next.geminiKey, [](const String& v) { return isSafeSecret(v, 128); });
    takeStr("stt_model", next.sttModel, [](const String& v) { return isSafeIdent(v, 64); });
    takeStr("llm_model", next.llmModel, [](const String& v) { return isSafeIdent(v, 64); });
    takeStr("tts_model", next.ttsModel, [](const String& v) { return isSafeIdent(v, 64); });
    takeStr("tts_voice", next.ttsVoice, [](const String& v) { return isSafeIdent(v, 32); });
    takeStr("chit_url", next.chitUrl, [](const String& v) { return isSafeUrl(v); });
    takeStr("chit_api_key", next.chitKey, [](const String& v) { return isSafeSecret(v, 128); });

    if (!obj["vad_enabled"].isNull()) {
        if (!obj["vad_enabled"].is<bool>()) {
            err = "vad_enabled must be true/false";
        } else {
            next.vad = obj["vad_enabled"].as<bool>();
        }
    }

    if (err.length() > 0) {
        g_configServer.send(400, "application/json", String("{\"error\":\"") + err + "\"}");
        return;
    }

    next.gatewayUrl = stripTrailingSlash(next.gatewayUrl);
    next.chitUrl    = stripTrailingSlash(next.chitUrl);

    if (!configSave(next)) {
        g_configServer.send(500, "application/json", "{\"error\":\"NVS write failed; running config unchanged (reboot re-reads NVS)\"}");
        return;
    }
    g_cfgVadChangedPending = (next.vad != g_cfg.vad);
    g_cfg = next;
    g_configServer.send(200, "application/json", configToMaskedJson(g_cfg));
    Serial.printf("[CONFIG] Updated -> mode=%s llm=%s vad=%s\n",
                  g_cfg.mode.c_str(), g_cfg.llm.c_str(), g_cfg.vad ? "on" : "off");
}

static void configServerBegin() {
    static const char* headerKeys[] = {"Authorization"};
    g_configServer.collectHeaders(headerKeys, 1);
    g_configServer.on("/config", HTTP_GET, handleGetConfig);
    g_configServer.on("/config", HTTP_POST, handlePostConfig);
    g_configServer.onNotFound([]() {
        g_configServer.send(404, "application/json", "{\"error\":\"not found\"}");
    });
    g_configServer.begin();
}
