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
static constexpr size_t DIRECT_REQ_CAP          = DIRECT_B64_WAV_CAP + 4096;          // + JSON prefix/suffix
static constexpr size_t DIRECT_RESP_CAP         = 1536 * 1024;                        // 1.5 MB (~23 s of base64 TTS)
static constexpr size_t DIRECT_PCM_CAP          = (DIRECT_RESP_CAP / 4) * 3;          // decoded PCM upper bound
static constexpr uint32_t DIRECT_SESSION_IDLE_MS   = 10UL * 60UL * 1000UL;            // 10 min idle timeout
static constexpr uint32_t DIRECT_SESSION_MAX_TURNS = 12;                              // Bounded server-side chain

static const char* const DIRECT_GEMINI_BASE      = "https://generativelanguage.googleapis.com/v1beta/models/";
static const char* const DIRECT_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions";
static const char* const DIRECT_CHAT_SYSTEM_PROMPT =
    "You are a warm, witty, cheerful small tabletop robot friend having a natural "
    "spoken conversation. Speak casually in 1 to 2 short natural sentences (under 30 words, "
    "plain spoken English, no markdown) and keep the chat flowing naturally. Prefix your "
    "reply with [WAVE] for greetings/goodbyes, or [HAPPY], [SURPRISED], or [SAD] to match "
    "emotion. CRITICAL NOISE FILTER: If the audio contains ONLY silence, fan or room noise, "
    "coughing, tapping, unintelligible mumbling, or distant background TV or people clearly "
    "not talking to you, reply ONLY with [SILENCE].";

static uint8_t* g_directWavPsram  = nullptr;
static uint8_t* g_directReqPsram  = nullptr;
static uint8_t* g_directRespPsram = nullptr;
static uint8_t* g_directPcmPsram  = nullptr;

static String   g_geminiInteractionId;
static uint32_t g_geminiTurnCount  = 0;
static uint32_t g_geminiLastTurnMs = 0;
static String   g_chitSessionId;

static void directResetSession(const char* reason) {
    if (g_geminiInteractionId.length() > 0 || g_geminiTurnCount > 0 || g_chitSessionId.length() > 0) {
        Serial.printf("[DIRECT] Session reset (%s)\n", reason);
    }
    g_geminiInteractionId = "";
    g_geminiTurnCount = 0;
    g_geminiLastTurnMs = 0;
    g_chitSessionId = "";
}

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

// Extracts interaction id and non-thought text from /v1beta/interactions JSON
static bool extractInteractionResponse(const PsramCaptureSink& sink, String& outId, String& outText) {
    JsonDocument filter;
    filter["id"] = true;
    filter["error"]["message"] = true;
    filter["steps"][0]["type"] = true;
    filter["steps"][0]["content"][0]["type"] = true;
    filter["steps"][0]["content"][0]["text"] = true;
    filter["outputs"][0]["type"] = true;
    filter["outputs"][0]["text"] = true;

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, sink.c_str(), sink.length(),
                                               DeserializationOption::Filter(filter));
    if (err) {
        Serial.printf("[DIRECT] Interact JSON parse error: %s\n", err.c_str());
        return false;
    }
    if (!doc["error"]["message"].isNull()) {
        Serial.printf("[DIRECT] Interact error: %s\n", doc["error"]["message"].as<const char*>());
        return false;
    }

    outId = doc["id"] | "";
    outId.trim();
    outText = "";

    for (JsonObject step : doc["steps"].as<JsonArray>()) {
        const char* stype = step["type"] | "";
        if (strcmp(stype, "thought") == 0) continue;
        if (strcmp(stype, "model_output") == 0) {
            for (JsonObject item : step["content"].as<JsonArray>()) {
                const char* ctype = item["type"] | "";
                if (ctype[0] == '\0' || strcmp(ctype, "text") == 0) {
                    outText += item["text"] | "";
                }
            }
        }
    }

    if (outText.length() == 0 && doc["outputs"].is<JsonArray>()) {
        for (JsonObject item : doc["outputs"].as<JsonArray>()) {
            const char* otype = item["type"] | "";
            if (otype[0] == '\0' || strcmp(otype, "text") == 0) {
                outText += item["text"] | "";
            }
        }
    }

    outText.trim();
    return true;
}

static bool directInteractAudioGemini(size_t sampleCount, String& reply, String& outNewId, bool& outIsSilence) {
    reply = "";
    outNewId = "";
    outIsSilence = false;

    if (g_geminiInteractionId.length() > 0) {
        if ((millis() - g_geminiLastTurnMs) >= DIRECT_SESSION_IDLE_MS) {
            directResetSession("idle timeout 10m");
        } else if (g_geminiTurnCount >= DIRECT_SESSION_MAX_TURNS) {
            directResetSession("max turns 12 reached");
        }
    }

    const uint32_t pcmBytes = sampleCount * sizeof(int16_t);
    writeWavHeader16kMono(g_directWavPsram, pcmBytes);
    memcpy(g_directWavPsram + DIRECT_WAV_HEADER_BYTES, g_recordPsramBuf, pcmBytes);

    String prefix;
    prefix.reserve(1024);
    prefix += "{\"model\":\"";
    prefix += g_cfg.llmModel;
    prefix += "\"";
    if (g_geminiInteractionId.length() > 0 && isSafeSecret(g_geminiInteractionId, 256)) {
        prefix += ",\"previous_interaction_id\":\"";
        prefix += g_geminiInteractionId;
        prefix += "\"";
    }
    prefix += ",\"system_instruction\":\"";
    prefix += DIRECT_CHAT_SYSTEM_PROMPT;
    prefix += "\",\"generation_config\":{\"thinking_level\":\"minimal\",\"temperature\":0.7,\"max_output_tokens\":200}";
    prefix += ",\"input\":[{\"type\":\"text\",\"text\":\"Listen to this audio. If it is only noise, silence, or background voices not talking to you, reply ONLY [SILENCE]. Otherwise reply naturally in conversation.\"},{\"type\":\"audio\",\"mime_type\":\"audio/wav\",\"data\":\"";

    static const char kSuffix[] = "\"}]}";
    const size_t prefixLen = prefix.length();
    if (prefixLen + sizeof(kSuffix) >= DIRECT_REQ_CAP) {
        Serial.println("[DIRECT] Request prefix overflow");
        return false;
    }

    memcpy(g_directReqPsram, prefix.c_str(), prefixLen);
    size_t b64Len = 0;
    int rc = mbedtls_base64_encode(g_directReqPsram + prefixLen,
                                   DIRECT_REQ_CAP - prefixLen - sizeof(kSuffix),
                                   &b64Len, g_directWavPsram, DIRECT_WAV_HEADER_BYTES + pcmBytes);
    if (rc != 0) {
        Serial.printf("[DIRECT] Interact base64 encode failed (%d)\n", rc);
        return false;
    }
    size_t pos = prefixLen + b64Len;
    memcpy(g_directReqPsram + pos, kSuffix, sizeof(kSuffix) - 1);
    pos += sizeof(kSuffix) - 1;

    PsramCaptureSink sink(g_directRespPsram, DIRECT_RESP_CAP);
    int status = directPost(DIRECT_INTERACTIONS_URL, GOOGLE_API_ROOT_CA_PEM, "x-goog-api-key", g_cfg.geminiKey,
                            g_directReqPsram, pos, sink);
    if ((status == 400 || status == 404) && g_geminiInteractionId.length() > 0) {
        Serial.printf("[DIRECT] Stale interaction id rejected (HTTP %d) -> resetting\n", status);
        directResetSession("HTTP 400/404 stale id");
        return false;
    }
    if (status != 200) {
        Serial.printf("[DIRECT] Interact HTTP %d: %.200s\n", status, sink.c_str());
        return false;
    }
    if (!extractInteractionResponse(sink, outNewId, reply)) {
        return false;
    }

    String upper = reply;
    upper.toUpperCase();
    if (reply.length() == 0 || upper.indexOf("[SILENCE]") >= 0) {
        outIsSilence = true;
        outNewId = "";
        return true;
    }
    return true;
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

static RobotExpression extractExpressionTag(String& reply, const char*& outTagLabel) {
    RobotExpression expr = EXPR_NEUTRAL;
    outTagLabel = "";

    struct TagMap {
        const char* tag;
        size_t len;
        RobotExpression expr;
        const char* label;
    };
    static const TagMap kTags[] = {
        {"[WAVE]",      6, EXPR_WAVE,      "[WAVE] "},
        {"[HAPPY]",     7, EXPR_HAPPY,     "[HAPPY] "},
        {"[SURPRISED]", 11, EXPR_SURPRISED, "[SURPRISED] "},
        {"[SAD]",       5, EXPR_SAD,       "[SAD] "},
    };

    for (const auto& item : kTags) {
        while (true) {
            String upper = reply;
            upper.toUpperCase();
            int idx = upper.indexOf(item.tag);
            if (idx < 0) break;
            if (expr == EXPR_NEUTRAL) {
                expr = item.expr;
                outTagLabel = item.label;
            }
            reply = reply.substring(0, idx) + " " + reply.substring(idx + item.len);
            reply.trim();
        }
    }

    // Strip any other short bracketed stage directions (e.g. [WINK], [LAUGH]) so TTS never speaks them
    while (true) {
        int openIdx = reply.indexOf('[');
        if (openIdx < 0) break;
        int closeIdx = reply.indexOf(']', openIdx + 1);
        if (closeIdx < 0 || (closeIdx - openIdx) > 16) break;
        reply = reply.substring(0, openIdx) + " " + reply.substring(closeIdx + 1);
        reply.trim();
    }

    return expr;
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
    uint32_t t1 = t0;
    uint32_t t2 = t0;
    String reply;
    String newInteractionId;
    const bool useGeminiInteractions = (g_cfg.llm != "chit");

    if (useGeminiInteractions) {
        bool isSilence = false;
        if (!directInteractAudioGemini(sampleCount, reply, newInteractionId, isSilence)) {
            directShowError("LLM");
            flushMicAfterPlayback();
            return;
        }
        t2 = millis();
        if (isSilence) {
            Serial.printf("[DIRECT] Ignored noise/background ([SILENCE]) in %lu ms -> idle\n",
                          static_cast<unsigned long>(t2 - t0));
            g_robotState = STATE_IDLE;
            flushMicAfterPlayback();
            return;
        }
    } else {
        String transcript;
        if (!directTranscribe(sampleCount, transcript)) { directShowError("STT"); flushMicAfterPlayback(); return; }
        t1 = millis();
        if (transcript.length() == 0) {
            Serial.println("[DIRECT] Empty transcript (silence/noise) -> idle");
            g_robotState = STATE_IDLE;
            flushMicAfterPlayback();
            return;
        }
        Serial.printf("[DIRECT] You said: %s\n", transcript.c_str());

        if (!directReplyChit(transcript, reply) || reply.length() == 0) {
            directShowError("LLM");
            flushMicAfterPlayback();
            return;
        }
        t2 = millis();
    }

    const char* tagLabel = "";
    const RobotExpression expr = extractExpressionTag(reply, tagLabel);
    if (reply.length() == 0) { directShowError("LLM"); flushMicAfterPlayback(); return; }
    Serial.printf("[DIRECT] Robot says: %s%s\n", tagLabel, reply.c_str());

    const uint32_t tTts0 = millis();
    size_t pcmBytes = 0;
    uint32_t sampleRate = TTS_DEFAULT_RATE;
    if (!directSynthesize(reply, pcmBytes, sampleRate)) { directShowError("TTS"); flushMicAfterPlayback(); return; }
    const uint32_t t3 = millis();

    // Commit stateful chain only after audio is ready to play (noise/errors never pollute history)
    if (useGeminiInteractions && newInteractionId.length() > 0) {
        g_geminiInteractionId = newInteractionId;
        g_geminiTurnCount++;
        g_geminiLastTurnMs = millis();
    }

    ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, sampleRate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));
    g_activeExpression = expr;
    if (expr == EXPR_WAVE) {
        triggerServoWaveIfEnabled();
    }
    RobotVoiceI2SSink speaker(g_prebufPsram, PREBUFFER_BYTES);
    speaker.reset();
    speaker.write(g_directPcmPsram, pcmBytes);
    speaker.flushRemaining();

    if (useGeminiInteractions) {
        Serial.printf("[DIRECT] Stateful Turn %lu/%lu | Timings ms: ChatAudio=%lu TTS=%lu (first audio after %lu) | %u PCM bytes @ %lu Hz | Free Heap: %u\n",
                      static_cast<unsigned long>(g_geminiTurnCount),
                      static_cast<unsigned long>(DIRECT_SESSION_MAX_TURNS),
                      static_cast<unsigned long>(t2 - t0),
                      static_cast<unsigned long>(t3 - tTts0),
                      static_cast<unsigned long>(t3 - t0),
                      static_cast<unsigned>(pcmBytes), static_cast<unsigned long>(sampleRate), ESP.getFreeHeap());
    } else {
        Serial.printf("[DIRECT] Timings ms: STT=%lu LLM=%lu TTS=%lu (first audio after %lu) | %u PCM bytes @ %lu Hz | Free Heap: %u\n",
                      static_cast<unsigned long>(t1 - t0), static_cast<unsigned long>(t2 - t1),
                      static_cast<unsigned long>(t3 - tTts0), static_cast<unsigned long>(t3 - t0),
                      static_cast<unsigned>(pcmBytes), static_cast<unsigned long>(sampleRate), ESP.getFreeHeap());
    }
    g_activeExpression = EXPR_NEUTRAL;
    g_robotState = STATE_IDLE;
    flushMicAfterPlayback();
}
