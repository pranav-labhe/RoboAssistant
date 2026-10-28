#pragma once

// ============================================================================
// Direct Mode: ESP32-S3 -> Gemini / Chit with no Gateway hop
//   1. STT : 16 kHz PCM -> WAV -> base64 (PSRAM) -> Gemini generateContent
//   2. LLM : transcript (+ last 3 turns) -> Gemini generateContent  OR  Chit /chat
//   3. TTS : reply text -> Gemini TTS -> base64 JSON captured in PSRAM ->
//            decoded to 24 kHz s16le PCM in PSRAM -> I2S speaker
//
// Included ONLY by src/robot_main/main.cpp, AFTER RobotVoiceI2SSink,
// triggerServoWaveIfEnabled(), ensureWiFiAndClock() and flushMicAfterPlayback().
//
// Known tradeoffs vs Gateway Mode (measure with the per-stage Serial timings):
//   - 3 sequential TLS sessions from the ESP32 (one per stage) instead of 1.
//   - TTS is NOT streamed: playback starts only after the whole reply's audio
//     has been downloaded and decoded (Gateway streams sentence by sentence).
//   - API keys live in NVS on the chip (unencrypted flash).
//   - Zero automatic retries (Gate 12 rule): a 429/5xx shows the error face.
// ============================================================================

#include <mbedtls/base64.h>

static constexpr size_t DIRECT_WAV_HEADER_BYTES = 44;
static constexpr size_t DIRECT_WAV_CAP          = DIRECT_WAV_HEADER_BYTES + MAX_RECORD_SAMPLES * sizeof(int16_t);
static constexpr size_t DIRECT_B64_WAV_CAP      = ((DIRECT_WAV_CAP + 2) / 3) * 4 + 1;
static constexpr size_t DIRECT_REQ_CAP          = DIRECT_B64_WAV_CAP + 1024;          // + JSON prefix/suffix
static constexpr size_t DIRECT_RESP_CAP         = 1536 * 1024;                        // 1.5 MB (~23 s of base64 TTS)
static constexpr size_t DIRECT_PCM_CAP          = (DIRECT_RESP_CAP / 4) * 3;          // decoded PCM upper bound
static constexpr size_t DIRECT_MAX_HISTORY      = 6;                                  // last 3 user+model turns

static const char* const DIRECT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/";
static const char* const DIRECT_SYSTEM_PROMPT =
    "You are a cheerful small tabletop robot. Reply in plain spoken English "
    "under 25 words without markdown. If waving hello or goodbye, prefix your "
    "reply with [WAVE].";

static uint8_t* g_directWavPsram  = nullptr;
static uint8_t* g_directReqPsram  = nullptr;
static uint8_t* g_directRespPsram = nullptr;
static uint8_t* g_directPcmPsram  = nullptr;

static String g_directHistRole[DIRECT_MAX_HISTORY];
static String g_directHistText[DIRECT_MAX_HISTORY];
static size_t g_directHistCount = 0;
static String g_chitSessionId;

static bool directModeAllocate() {
    g_directWavPsram  = static_cast<uint8_t*>(ps_malloc(DIRECT_WAV_CAP));
    g_directReqPsram  = static_cast<uint8_t*>(ps_malloc(DIRECT_REQ_CAP));
    g_directRespPsram = static_cast<uint8_t*>(ps_malloc(DIRECT_RESP_CAP));
    g_directPcmPsram  = static_cast<uint8_t*>(ps_malloc(DIRECT_PCM_CAP));
    return g_directWavPsram && g_directReqPsram && g_directRespPsram && g_directPcmPsram;
}

// Captures an entire HTTP response body (chunked or Content-Length) into PSRAM.
// Returns fewer bytes than offered on overflow so HTTPClient::writeToStream fails
// loudly instead of silently truncating audio.
class PsramCaptureSink : public Stream {
    uint8_t* _buf;
    size_t   _cap;
    size_t   _len = 0;
    bool     _overflow = false;

public:
    PsramCaptureSink(uint8_t* buf, size_t cap) : _buf(buf), _cap(cap) { _buf[0] = 0; }

    size_t write(const uint8_t* data, size_t size) override {
        size_t space = (_cap - 1) - _len;  // keep 1 byte for NUL terminator
        size_t n = (size < space) ? size : space;
        memcpy(_buf + _len, data, n);
        _len += n;
        _buf[_len] = 0;
        if (n < size) _overflow = true;
        return n;
    }
    size_t write(uint8_t b) override { return write(&b, 1); }
    int available() override { return 0; }
    int read() override { return -1; }
    int peek() override { return -1; }
    void flush() override {}

    const char* c_str() const { return reinterpret_cast<const char*>(_buf); }
    size_t length() const { return _len; }
    bool overflowed() const { return _overflow; }
};

// One HTTPS (or HTTP for a LAN Chit URL) POST. Returns HTTP status, or a negative
// HTTPClient error code (including stream failure / PSRAM overflow).
static int directPost(const String& url, const char* rootCaPem,
                      const char* authHeaderName, const String& authHeaderValue,
                      const uint8_t* body, size_t bodyLen, PsramCaptureSink& sink) {
    HTTPClient http;
    WiFiClient plainClient;
    WiFiClientSecure tlsClient;

    http.setReuse(false);
    http.setConnectTimeout(10000);
    http.setTimeout(30000);
    if (url.startsWith("https://")) {
        tlsClient.setCACert(rootCaPem);
        tlsClient.setTimeout(30);
        if (!http.begin(tlsClient, url)) return HTTPC_ERROR_CONNECTION_REFUSED;
    } else {
        if (!http.begin(plainClient, url)) return HTTPC_ERROR_CONNECTION_REFUSED;
    }
    http.addHeader("Content-Type", "application/json");
    http.addHeader(authHeaderName, authHeaderValue);

    int status = http.POST(const_cast<uint8_t*>(body), bodyLen);
    if (status > 0) {
        int streamed = http.writeToStream(&sink);
        if (streamed < 0 || sink.overflowed()) {
            Serial.printf("[DIRECT] Response stream failed (%d, overflow=%d, %u bytes)\n",
                          streamed, sink.overflowed() ? 1 : 0, static_cast<unsigned>(sink.length()));
            status = (streamed < 0) ? streamed : HTTPC_ERROR_STREAM_WRITE;
        }
    }
    http.end();
    return status;
}

// Extracts concatenated non-thought text parts from a Gemini generateContent response
static bool extractGeminiText(const PsramCaptureSink& sink, String& out) {
    JsonDocument filter;
    filter["candidates"][0]["content"]["parts"][0]["text"] = true;
    filter["candidates"][0]["content"]["parts"][0]["thought"] = true;
    filter["error"]["message"] = true;

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, sink.c_str(), sink.length(),
                                               DeserializationOption::Filter(filter));
    if (err) {
        Serial.printf("[DIRECT] Gemini JSON parse error: %s\n", err.c_str());
        return false;
    }
    if (!doc["error"]["message"].isNull()) {
        Serial.printf("[DIRECT] Gemini error: %s\n", doc["error"]["message"].as<const char*>());
        return false;
    }
    out = "";
    for (JsonObject part : doc["candidates"][0]["content"]["parts"].as<JsonArray>()) {
        if (part["thought"] | false) continue;
        out += part["text"] | "";
    }
    out.trim();
    return true;
}

static void writeWavHeader16kMono(uint8_t* h, uint32_t pcmBytes) {
    auto le32 = [](uint8_t* p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; };
    auto le16 = [](uint8_t* p, uint16_t v) { p[0] = v; p[1] = v >> 8; };
    memcpy(h, "RIFF", 4);       le32(h + 4, 36 + pcmBytes);
    memcpy(h + 8, "WAVEfmt ", 8); le32(h + 16, 16);
    le16(h + 20, 1);            le16(h + 22, 1);                 // PCM, mono
    le32(h + 24, MIC_SAMPLE_RATE); le32(h + 28, MIC_SAMPLE_RATE * 2);
    le16(h + 32, 2);            le16(h + 34, 16);                // block align, bits
    memcpy(h + 36, "data", 4);  le32(h + 40, pcmBytes);
}

static bool directTranscribe(size_t sampleCount, String& transcript) {
    const uint32_t pcmBytes = sampleCount * sizeof(int16_t);
    writeWavHeader16kMono(g_directWavPsram, pcmBytes);
    memcpy(g_directWavPsram + DIRECT_WAV_HEADER_BYTES, g_recordPsramBuf, pcmBytes);

    static const char kPrefix[] =
        "{\"contents\":[{\"parts\":[{\"text\":\"Transcribe the spoken words in this audio "
        "accurately into plain text. Return ONLY the transcript, or an empty string if there "
        "is only silence or noise.\"},{\"inlineData\":{\"mimeType\":\"audio/wav\",\"data\":\"";
    static const char kSuffix[] =
        "\"}}]}],\"generationConfig\":{\"temperature\":0.0,"
        "\"thinkingConfig\":{\"thinkingLevel\":\"MINIMAL\"}}}";

    size_t pos = sizeof(kPrefix) - 1;
    memcpy(g_directReqPsram, kPrefix, pos);
    size_t b64Len = 0;
    int rc = mbedtls_base64_encode(g_directReqPsram + pos, DIRECT_REQ_CAP - pos - sizeof(kSuffix),
                                   &b64Len, g_directWavPsram, DIRECT_WAV_HEADER_BYTES + pcmBytes);
    if (rc != 0) {
        Serial.printf("[DIRECT] base64 encode failed (%d)\n", rc);
        return false;
    }
    pos += b64Len;
    memcpy(g_directReqPsram + pos, kSuffix, sizeof(kSuffix) - 1);
    pos += sizeof(kSuffix) - 1;

    PsramCaptureSink sink(g_directRespPsram, DIRECT_RESP_CAP);
    String url = String(DIRECT_GEMINI_BASE) + g_cfg.sttModel + ":generateContent";
    int status = directPost(url, GOOGLE_API_ROOT_CA_PEM, "x-goog-api-key", g_cfg.geminiKey,
                            g_directReqPsram, pos, sink);
    if (status != 200) {
        Serial.printf("[DIRECT] STT HTTP %d: %.200s\n", status, sink.c_str());
        return false;
    }
    return extractGeminiText(sink, transcript);
}

static void directHistoryAppend(const char* role, const String& text) {
    if (g_directHistCount == DIRECT_MAX_HISTORY) {
        for (size_t i = 1; i < DIRECT_MAX_HISTORY; ++i) {
            g_directHistRole[i - 1] = g_directHistRole[i];
            g_directHistText[i - 1] = g_directHistText[i];
        }
        g_directHistCount--;
    }
    g_directHistRole[g_directHistCount] = role;
    g_directHistText[g_directHistCount] = text;
    g_directHistCount++;
}

static bool directReplyGemini(const String& transcript, String& reply) {
    JsonDocument doc;
    doc["systemInstruction"]["parts"][0]["text"] = DIRECT_SYSTEM_PROMPT;
    JsonArray contents = doc["contents"].to<JsonArray>();
    for (size_t i = 0; i < g_directHistCount; ++i) {
        JsonObject turn = contents.add<JsonObject>();
        turn["role"] = g_directHistRole[i];
        turn["parts"][0]["text"] = g_directHistText[i];
    }
    JsonObject userTurn = contents.add<JsonObject>();
    userTurn["role"] = "user";
    userTurn["parts"][0]["text"] = transcript;
    doc["generationConfig"]["maxOutputTokens"] = 256;
    doc["generationConfig"]["temperature"] = 0.6;
    doc["generationConfig"]["thinkingConfig"]["thinkingLevel"] = "MINIMAL";

    String body;
    serializeJson(doc, body);

    PsramCaptureSink sink(g_directRespPsram, DIRECT_RESP_CAP);
    String url = String(DIRECT_GEMINI_BASE) + g_cfg.llmModel + ":generateContent";
    int status = directPost(url, GOOGLE_API_ROOT_CA_PEM, "x-goog-api-key", g_cfg.geminiKey,
                            reinterpret_cast<const uint8_t*>(body.c_str()), body.length(), sink);
    if (status != 200) {
        Serial.printf("[DIRECT] LLM HTTP %d: %.200s\n", status, sink.c_str());
        return false;
    }
    return extractGeminiText(sink, reply);
}

static bool directReplyChit(const String& transcript, String& reply) {
    // Strict Chit schema: ONLY message, think, session_id (unknown fields -> HTTP 422)
    JsonDocument doc;
    doc["message"] = transcript;
    doc["think"] = false;
    if (g_chitSessionId.length() > 0) {
        doc["session_id"] = g_chitSessionId;
    }
    String body;
    serializeJson(doc, body);

    PsramCaptureSink sink(g_directRespPsram, DIRECT_RESP_CAP);
    int status = directPost(g_cfg.chitUrl + "/chat", GATEWAY_ROOT_CA_PEM, "X-API-Key", g_cfg.chitKey,
                            reinterpret_cast<const uint8_t*>(body.c_str()), body.length(), sink);
    if (status != 200) {
        // 429 = Chit queue full. Zero retries on the robot by design (Gate 12).
        Serial.printf("[DIRECT] Chit HTTP %d: %.200s\n", status, sink.c_str());
        return false;
    }

    JsonDocument filter;
    filter["text"] = true;
    filter["session_id"] = true;
    JsonDocument resp;
    if (deserializeJson(resp, sink.c_str(), sink.length(), DeserializationOption::Filter(filter))) {
        Serial.println("[DIRECT] Chit JSON parse error");
        return false;
    }
    const char* sid = resp["session_id"] | "";
    if (sid[0] != '\0') {
        g_chitSessionId = sid;
    }
    reply = resp["text"] | "";
    reply.trim();
    return true;
}

static bool extractWaveTag(String& reply) {
    bool found = false;
    while (true) {
        String upper = reply;
        upper.toUpperCase();
        int idx = upper.indexOf("[WAVE]");
        if (idx < 0) break;
        found = true;
        reply = reply.substring(0, idx) + " " + reply.substring(idx + 6);
        reply.trim();
    }
    return found;
}

// Locates the base64 audio inside the Gemini TTS JSON *in place* (no JSON DOM:
// the payload is ~0.5 MB and would exhaust internal SRAM), then decodes it into PSRAM.
static bool directSynthesize(const String& text, size_t& pcmBytes, uint32_t& sampleRate) {
    JsonDocument doc;
    doc["contents"][0]["parts"][0]["text"] = text;
    doc["generationConfig"]["responseModalities"][0] = "AUDIO";
    doc["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] = g_cfg.ttsVoice;
    String body;
    serializeJson(doc, body);

    PsramCaptureSink sink(g_directRespPsram, DIRECT_RESP_CAP);
    String url = String(DIRECT_GEMINI_BASE) + g_cfg.ttsModel + ":generateContent";
    int status = directPost(url, GOOGLE_API_ROOT_CA_PEM, "x-goog-api-key", g_cfg.geminiKey,
                            reinterpret_cast<const uint8_t*>(body.c_str()), body.length(), sink);
    if (status != 200) {
        Serial.printf("[DIRECT] TTS HTTP %d: %.200s\n", status, sink.c_str());
        return false;
    }

    const char* json = sink.c_str();
    const char* inlineObj = strstr(json, "\"inlineData\"");
    const char* dataKey = inlineObj ? strstr(inlineObj, "\"data\"") : nullptr;
    const char* colon = dataKey ? strchr(dataKey + 6, ':') : nullptr;
    const char* start = colon ? strchr(colon, '"') : nullptr;
    const char* end = start ? strchr(start + 1, '"') : nullptr;
    if (!end) {
        Serial.printf("[DIRECT] TTS response had no inlineData audio: %.200s\n", json);
        return false;
    }
    start++;

    // mimeType is e.g. "audio/L16;codec=pcm;rate=24000" — may appear before or after "data"
    sampleRate = TTS_DEFAULT_RATE;
    const char* rate = strstr(inlineObj, "rate=");
    if (rate && (rate < start || (rate > end && rate < end + 200))) {
        uint32_t parsed = static_cast<uint32_t>(atoi(rate + 5));
        if (parsed >= 8000 && parsed <= 48000) sampleRate = parsed;
    }

    size_t decoded = 0;
    int rc = mbedtls_base64_decode(g_directPcmPsram, DIRECT_PCM_CAP, &decoded,
                                   reinterpret_cast<const unsigned char*>(start),
                                   static_cast<size_t>(end - start));
    if (rc != 0) {
        Serial.printf("[DIRECT] base64 decode failed (%d)\n", rc);
        return false;
    }
    pcmBytes = decoded & ~static_cast<size_t>(1);  // s16le alignment guard
    return pcmBytes > 0;
}

static void directShowError(const char* stage) {
    Serial.printf("[DIRECT] Turn aborted at %s stage (no retry) | Free Heap: %u\n", stage, ESP.getFreeHeap());
    i2s_zero_dma_buffer(AMP_I2S_PORT);
    g_robotState = STATE_ERROR;
    delay(2500);
    g_robotState = STATE_IDLE;
}

static void processDirectTurn(size_t sampleCount) {
    if (g_cfg.geminiKey.length() == 0 || (g_cfg.llm == "chit" && g_cfg.chitKey.length() == 0)) {
        Serial.println("[DIRECT] Missing API key. POST gemini_api_key (and chit_api_key for llm=chit) to /config.");
        directShowError("config");
        return;
    }
    if (!ensureWiFiAndClock()) {
        return;
    }
    g_robotState = STATE_THINKING;

    const uint32_t t0 = millis();
    String transcript;
    if (!directTranscribe(sampleCount, transcript)) { directShowError("STT"); flushMicAfterPlayback(); return; }
    const uint32_t t1 = millis();
    if (transcript.length() == 0) {
        Serial.println("[DIRECT] Empty transcript (silence/noise) -> idle");
        g_robotState = STATE_IDLE;
        flushMicAfterPlayback();
        return;
    }
    Serial.printf("[DIRECT] You said: %s\n", transcript.c_str());

    String reply;
    bool llmOk = (g_cfg.llm == "chit") ? directReplyChit(transcript, reply)
                                       : directReplyGemini(transcript, reply);
    if (!llmOk || reply.length() == 0) { directShowError("LLM"); flushMicAfterPlayback(); return; }
    const uint32_t t2 = millis();

    const bool wave = extractWaveTag(reply);
    Serial.printf("[DIRECT] Robot says: %s%s\n", wave ? "[WAVE] " : "", reply.c_str());

    size_t pcmBytes = 0;
    uint32_t sampleRate = TTS_DEFAULT_RATE;
    if (!directSynthesize(reply, pcmBytes, sampleRate)) { directShowError("TTS"); flushMicAfterPlayback(); return; }
    const uint32_t t3 = millis();

    // Only commit history once the reply is actually going to be spoken
    directHistoryAppend("user", transcript);
    directHistoryAppend("model", reply);

    ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, sampleRate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));
    if (wave) {
        triggerServoWaveIfEnabled();
    }
    RobotVoiceI2SSink speaker(g_prebufPsram, PREBUFFER_BYTES);
    speaker.reset();
    speaker.write(g_directPcmPsram, pcmBytes);
    speaker.flushRemaining();

    Serial.printf("[DIRECT] Timings ms: STT=%lu LLM=%lu TTS=%lu (first audio after %lu) | %u PCM bytes @ %lu Hz | Free Heap: %u\n",
                  static_cast<unsigned long>(t1 - t0), static_cast<unsigned long>(t2 - t1),
                  static_cast<unsigned long>(t3 - t2), static_cast<unsigned long>(t3 - t0),
                  static_cast<unsigned>(pcmBytes), static_cast<unsigned long>(sampleRate), ESP.getFreeHeap());
    g_robotState = STATE_IDLE;
    flushMicAfterPlayback();
}
