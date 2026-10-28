#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <driver/i2s.h>
#include <time.h>
#include "pins.h"
#include "certs.h"
#include "robot_secrets.h"

// ============================================================================
// Gate 7: Raw 16 kHz PCM Upload & Chunk-Decoded Echo Playback (`POST /echo`)
// Build & Upload: pio run -e gate7_echo -t upload -t monitor
// Proves:
//   1. Recording 16 kHz mono PCM into PSRAM on GPIO 1 button hold
//   2. POSTing raw PCM bytes to Gateway `/echo` over verified TLS (or LAN HTTP)
//   3. Parsing `X-Sample-Rate` header and calling `i2s_set_clk()`
//   4. HTTP chunk decoding + 1-byte odd-chunk carryover guard + 400 ms PSRAM
//      pre-buffer before I2S speaker playback
// ============================================================================

static constexpr i2s_port_t MIC_I2S_PORT = I2S_NUM_0;
static constexpr i2s_port_t AMP_I2S_PORT = I2S_NUM_1;

static constexpr uint32_t   MIC_SAMPLE_RATE    = 16000;
static constexpr uint32_t   MAX_RECORD_SECONDS = 4;
static constexpr size_t     MAX_RECORD_SAMPLES = MIC_SAMPLE_RATE * MAX_RECORD_SECONDS;
static constexpr size_t     MIN_VALID_SAMPLES  = (MIC_SAMPLE_RATE * 400) / 1000; // 400 ms
static constexpr size_t     IO_CHUNK_SAMPLES   = 256;
static constexpr int        MIC_BIT_SHIFT      = 11;
static constexpr float      PLAYBACK_VOLUME    = 0.35f;
static constexpr size_t     PREBUFFER_BYTES    = 16000 * 2 * 400 / 1000; // 400 ms @ 16 kHz (12.8 KB)

static int16_t* g_recordPsramBuf = nullptr;
static uint8_t* g_prebufPsram    = nullptr;
static int32_t  g_prevX = 0;
static int32_t  g_prevY = 0;

// Custom Stream sink for HTTPClient::writeToStream()
// Handles:
//   - 1-byte carryover when an HTTP chunk has an odd byte length
//   - 400 ms PSRAM pre-buffering before starting I2S playback
//   - Stereo [L, L] duplication and software volume scaling
class PcmPrebufferedI2SSink : public Stream {
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

            for (size_t i = 0; i < batch; ++i) {
                int16_t scaled = static_cast<int16_t>(samples[idx + i] * PLAYBACK_VOLUME);
                stereoBatch[i * 2]     = scaled;
                stereoBatch[i * 2 + 1] = scaled;
            }

            size_t bytesWritten = 0;
            i2s_write(AMP_I2S_PORT, stereoBatch, batch * 2 * sizeof(int16_t), &bytesWritten, portMAX_DELAY);
            idx += batch;
        }
        _totalPlayedBytes += evenLen;
    }

public:
    PcmPrebufferedI2SSink(uint8_t* prebuf, size_t prebufCap)
        : _prebuf(prebuf), _prebufCap(prebufCap) {}

    void reset() {
        _prebufLen = 0;
        _startedPlayback = false;
        _hasCarry = false;
        _carryByte = 0;
        _totalPlayedBytes = 0;
    }

    size_t write(uint8_t b) override {
        return write(&b, 1);
    }

    size_t write(const uint8_t* buffer, size_t size) override {
        if (size == 0) return 0;
        const uint8_t* ptr = buffer;
        size_t remaining = size;

        // 1. Complete any 1-byte carryover from a previous odd-sized chunk
        if (_hasCarry && remaining > 0) {
            uint8_t pair[2] = {_carryByte, ptr[0]};
            _hasCarry = false;
            ptr++;
            remaining--;
            write(pair, 2);
        }

        // 2. Hold back 1 trailing byte if remaining length is odd
        if ((remaining % 2) != 0) {
            _carryByte = ptr[remaining - 1];
            _hasCarry = true;
            remaining--;
        }

        if (remaining == 0) return size;

        // 3. Fill 400 ms PSRAM pre-buffer before starting I2S DMA playback
        if (!_startedPlayback) {
            size_t space = _prebufCap - _prebufLen;
            size_t toCopy = (remaining < space) ? remaining : space;
            memcpy(_prebuf + _prebufLen, ptr, toCopy);
            _prebufLen += toCopy;
            ptr += toCopy;
            remaining -= toCopy;

            if (_prebufLen >= _prebufCap) {
                _startedPlayback = true;
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
            writeAlignedPcmToI2S(_prebuf, _prebufLen);
            _prebufLen = 0;
        }
        i2s_zero_dma_buffer(AMP_I2S_PORT);
    }

    size_t totalPlayedBytes() const { return _totalPlayedBytes; }

    int available() override { return 0; }
    int read() override { return -1; }
    int peek() override { return -1; }
    void flush() override {}
};

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
    ampCfg.sample_rate = 16000;
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

static void connectWiFiAndSyncNTP() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("[Gate 7] Connecting to Wi-Fi (%s)", WIFI_SSID);
    while (WiFi.status() != WL_CONNECTED) {
        delay(300);
        Serial.print(".");
    }
    Serial.printf("\n[OK] Wi-Fi Connected: %s\n", WiFi.localIP().toString().c_str());

    configTime(0, 0, "pool.ntp.org", "time.cloudflare.com");
    while (time(nullptr) < 1700000000) {
        delay(250);
    }
    Serial.println("[OK] NTP Clock Synced.");
}

static size_t recordFromMic() {
    int32_t rawBuf[IO_CHUNK_SAMPLES];
    size_t recorded = 0;
    g_prevX = 0;
    g_prevY = 0;
    i2s_zero_dma_buffer(MIC_I2S_PORT);

    Serial.println("[Gate 7] Recording... Hold GPIO 1 and speak.");
    while (digitalRead(PIN_PTT_BTN) == LOW && recorded < MAX_RECORD_SAMPLES) {
        size_t bytesRead = 0;
        if (i2s_read(MIC_I2S_PORT, rawBuf, sizeof(rawBuf), &bytesRead, pdMS_TO_TICKS(100)) != ESP_OK) {
            continue;
        }
        size_t count = bytesRead / sizeof(int32_t);
        for (size_t i = 0; i < count && recorded < MAX_RECORD_SAMPLES; ++i) {
            int32_t x = rawBuf[i] >> MIC_BIT_SHIFT;
            int32_t y = (x - g_prevX) + ((g_prevY * 255) >> 8);
            g_prevX = x;
            g_prevY = y;
            if (y > 32767)  y = 32767;
            if (y < -32768) y = -32768;
            g_recordPsramBuf[recorded++] = static_cast<int16_t>(y);
        }
    }
    return recorded;
}

static void sendToEchoGateway(size_t sampleCount) {
    const size_t payloadBytes = sampleCount * sizeof(int16_t);
    String url = String(GATEWAY_BASE_URL) + "/echo";
    Serial.printf("[Gate 7] POST %s (%u bytes)...\n", url.c_str(), static_cast<unsigned>(payloadBytes));

    HTTPClient http;
    WiFiClient plainClient;
    WiFiClientSecure tlsClient;

    if (url.startsWith("https://")) {
        tlsClient.setCACert(GATEWAY_ROOT_CA_PEM);
        tlsClient.setTimeout(20);
        http.begin(tlsClient, url);
    } else {
        http.begin(plainClient, url);
    }

    const char* headerKeys[] = {"X-Sample-Rate"};
    http.collectHeaders(headerKeys, 1);
    http.addHeader("Authorization", String("Bearer ") + GATEWAY_BEARER_TOKEN);
    http.addHeader("Content-Type", "audio/pcm;rate=16000");

    int status = http.POST(reinterpret_cast<uint8_t*>(g_recordPsramBuf), payloadBytes);
    if (status == 200) {
        uint32_t sampleRate = 16000;
        if (http.hasHeader("X-Sample-Rate")) {
            sampleRate = static_cast<uint32_t>(http.header("X-Sample-Rate").toInt());
            if (sampleRate < 8000 || sampleRate > 48000) sampleRate = 16000;
        }
        ESP_ERROR_CHECK(i2s_set_clk(AMP_I2S_PORT, sampleRate,
                                    I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_STEREO));

        PcmPrebufferedI2SSink sink(g_prebufPsram, PREBUFFER_BYTES);
        sink.reset();
        http.writeToStream(&sink);
        sink.flushRemaining();

        Serial.printf("[PASS] Gate 7 Echo played %u bytes @ %u Hz | Free Heap: %u\n",
                      static_cast<unsigned>(sink.totalPlayedBytes()),
                      static_cast<unsigned>(sampleRate),
                      ESP.getFreeHeap());
    } else {
        i2s_zero_dma_buffer(AMP_I2S_PORT);
        Serial.printf("[FAIL] Gateway /echo returned HTTP %d\n", status);
    }
    http.end();
}

void setup() {
    Serial.begin(115200);
    uint32_t startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) {
        delay(10);
    }

    pinMode(PIN_PTT_BTN, INPUT_PULLUP);
    g_recordPsramBuf = static_cast<int16_t*>(ps_malloc(MAX_RECORD_SAMPLES * sizeof(int16_t)));
    g_prebufPsram    = static_cast<uint8_t*>(ps_malloc(PREBUFFER_BYTES));
    if (!g_recordPsramBuf || !g_prebufPsram) {
        Serial.println("[FAIL] PSRAM allocation failed!");
        while (true) { delay(1000); }
    }

    initAudioI2S();
    connectWiFiAndSyncNTP();
    Serial.println("[READY] Hold GPIO 1 button to record and echo through Gateway.");
}

void loop() {
    if (digitalRead(PIN_PTT_BTN) == LOW) {
        delay(30);
        if (digitalRead(PIN_PTT_BTN) == LOW) {
            size_t samples = recordFromMic();
            while (digitalRead(PIN_PTT_BTN) == LOW) { delay(10); }
            delay(30);

            if (samples >= MIN_VALID_SAMPLES) {
                sendToEchoGateway(samples);
            } else {
                Serial.println("[SKIP] Button press < 400 ms ignored.");
            }
        }
    }
    delay(10);
}
