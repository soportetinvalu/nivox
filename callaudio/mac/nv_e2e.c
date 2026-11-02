/* SPDX-License-Identifier: MIT
 * nv-e2e: end-to-end test of the installed Nivox HAL plug-in + engine on a
 * real macOS CoreAudio stack (CI runner or user Mac). Uses only the four
 * Nivox virtual devices, so it needs no physical microphone or speakers.
 *   1. device topology (visible/hidden, formats, shared clock domain)
 *   2. raw loopback Mic Feed -> Microphone   (tone integrity + latency)
 *   3. raw loopback Speaker -> Speaker Return (stereo)
 *   4. engine path Speaker Return -> bridge(dry) -> Mic Feed, observed at Microphone
 *   5. same path with denoise on white noise: attenuation in dB
 * Prints a JSON report; exit 1 on failure. Silence on every capture usually
 * means microphone (TCC) permission was denied to the calling process.
 */
#include "nv_path.h"
#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static int failures;
static char notes[8192];
static void note(const char *fmt, const char *a, double v) {
    size_t n = strlen(notes);
    snprintf(notes + n, sizeof notes - n, "%s" "%s %s=%.3f", n ? "; " : "", fmt, a, v);
}
#define EXPECT(cond, what, val) do { if (!(cond)) { failures++; note("FAIL", what, (double)(val)); } } while (0)

enum { GEN_SINE, GEN_NOISE, GEN_IMPULSE };
typedef struct {
    int mode; double rate; double phase; float amp; uint32_t seed;
    uint64_t frames, impulse_after; _Atomic uint64_t impulse_ns; int impulse_done;
} gen_t;

static void gen_cb(void *ctx, float *d, uint32_t n, uint32_t ch, const AudioTimeStamp *ts) {
    gen_t *g = ctx;
    for (uint32_t i = 0; i < n; i++) {
        float x = 0;
        if (g->mode == GEN_SINE) { x = g->amp * (float)sin(g->phase); g->phase += 2 * M_PI * 1000.0 / g->rate; if (g->phase > 2 * M_PI) g->phase -= 2 * M_PI; }
        else if (g->mode == GEN_NOISE) { g->seed = g->seed * 1664525u + 1013904223u; x = g->amp * (((float)(g->seed >> 8) / 8388608.f) - 1.f); }
        else if (g->mode == GEN_IMPULSE && !g->impulse_done && g->frames + i >= g->impulse_after) {
            x = g->amp; g->impulse_done = 1;
            uint64_t base = (ts && (ts->mFlags & kAudioTimeStampHostTimeValid)) ? nv_host_to_ns(ts->mHostTime) : 0;
            atomic_store(&g->impulse_ns, base + (uint64_t)(i * 1e9 / g->rate));
        }
        for (uint32_t c = 0; c < ch; c++) d[(size_t)i * ch + c] = x;
    }
    g->frames += n;
}

typedef struct {
    float *buf; size_t cap, len; double rate;
    uint64_t *cb_ns; size_t *cb_idx; size_t ncb, cbcap;
    uint64_t channel_mismatch;
} rec_t;

static void rec_cb(void *ctx, float *d, uint32_t n, uint32_t ch, const AudioTimeStamp *ts) {
    rec_t *r = ctx;
    if (r->ncb < r->cbcap) {
        r->cb_ns[r->ncb] = (ts && (ts->mFlags & kAudioTimeStampHostTimeValid)) ? nv_host_to_ns(ts->mHostTime) : 0;
        r->cb_idx[r->ncb++] = r->len;
    }
    for (uint32_t i = 0; i < n && r->len < r->cap; i++) {
        r->buf[r->len++] = d[(size_t)i * ch];
        for (uint32_t c = 1; c < ch; c++) if (d[(size_t)i * ch + c] != d[(size_t)i * ch]) r->channel_mismatch++;
    }
}

static int rec_init(rec_t *r, double seconds, double rate) {
    memset(r, 0, sizeof *r);
    r->rate = rate; r->cap = (size_t)(seconds * rate) + 8192; r->cbcap = 100000;
    r->buf = calloc(r->cap, sizeof(float)); r->cb_ns = calloc(r->cbcap, 8); r->cb_idx = calloc(r->cbcap, sizeof(size_t));
    return r->buf && r->cb_ns && r->cb_idx ? 0 : -1;
}
static void rec_free(rec_t *r) { free(r->buf); free(r->cb_ns); free(r->cb_idx); }
static uint64_t rec_time_of(const rec_t *r, size_t idx) {
    for (size_t k = r->ncb; k-- > 0;)
        if (r->cb_idx[k] <= idx) return r->cb_ns[k] + (uint64_t)((idx - r->cb_idx[k]) * 1e9 / r->rate);
    return 0;
}

typedef struct { double rms, freq, onset_s; uint64_t glitches; size_t n; } analysis;
static analysis analyze(const rec_t *r, float glitch_thr) {
    analysis a = {0};
    size_t on = 0;
    while (on < r->len && fabsf(r->buf[on]) < 1e-4f) on++;
    a.onset_s = on / r->rate;
    size_t s = on + (size_t)(0.5 * r->rate), e = r->len > (size_t)(0.1 * r->rate) ? r->len - (size_t)(0.1 * r->rate) : 0;
    if (on >= r->len || s + 1000 > e) return a;
    double sq = 0; size_t zc = 0; size_t first = 0, last = 0;
    for (size_t i = s; i < e; i++) {
        sq += (double)r->buf[i] * r->buf[i];
        if (i > s && r->buf[i - 1] < 0 && r->buf[i] >= 0) { if (!zc) first = i; last = i; zc++; }
        if (i >= s + 2 && fabsf(r->buf[i] - 2 * r->buf[i - 1] + r->buf[i - 2]) > glitch_thr) a.glitches++;
    }
    a.n = e - s;
    a.rms = sqrt(sq / (double)a.n);
    a.freq = zc > 1 ? (zc - 1) * r->rate / (double)(last - first) : 0;
    return a;
}

/* Play gen into `play`, record `cap` for `seconds`. Optional engine path runs meanwhile. */
static int run(AudioObjectID play, uint32_t play_ch, gen_t *g, AudioObjectID cap, uint32_t cap_ch,
               rec_t *r, double seconds) {
    OSStatus st;
    nv_io *in = nv_io_open(cap, 1, cap_ch, rec_cb, r, &st);
    if (!in) { note("open capture", "status", st); failures++; return -1; }
    nv_io *out = nv_io_open(play, 0, play_ch, gen_cb, g, &st);
    if (!out) { note("open playback", "status", st); failures++; nv_io_close(in); return -1; }
    g->rate = nv_io_rate(out);
    if ((st = nv_io_start(in)) != noErr || (st = nv_io_start(out)) != noErr) { note("start", "status", st); failures++; }
    usleep((useconds_t)(seconds * 1e6));
    nv_io_close(out);
    nv_io_close(in);
    return 0;
}

static void set_volume(AudioObjectID dev, int input) {
    Float32 one = 1.0f;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyVolumeScalar,
                                    input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput,
                                    kAudioObjectPropertyElementMain};
    AudioObjectSetPropertyData(dev, &a, 0, NULL, sizeof one, &one);
}

static int listed(AudioObjectID dev) {
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    UInt32 size = 0; int found = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) != noErr) return 0;
    AudioObjectID *ids = malloc(size);
    if (ids && AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, ids) == noErr)
        for (UInt32 i = 0; i < size / sizeof *ids; i++) found |= ids[i] == dev;
    free(ids);
    return found;
}

int main(void) {
    AudioObjectID mic = nv_device_for_uid(NV_UID_MICROPHONE), feed = nv_device_for_uid(NV_UID_MIC_FEED);
    AudioObjectID spk = nv_device_for_uid(NV_UID_SPEAKER), ret = nv_device_for_uid(NV_UID_SPEAKER_RETURN);
    printf("{\"devices\":");
    nv_list_devices_json(stdout);
    if (!mic || !feed || !spk || !ret) {
        printf(",\"ok\":false,\"error\":\"Nivox devices not found (plug-in not loaded)\"}\n");
        return 1;
    }
    /* 1. topology */
    EXPECT(listed(mic) && listed(spk), "visible devices listed", 0);
    EXPECT(!listed(feed) && !listed(ret), "hidden devices not listed", 0);
    EXPECT(nv_device_hidden(feed) && nv_device_hidden(ret) && !nv_device_hidden(mic) && !nv_device_hidden(spk), "hidden flags", 0);
    EXPECT(nv_device_channels(mic, 1) == 1 && nv_device_channels(feed, 0) == 1, "mic pair channels", nv_device_channels(mic, 1));
    EXPECT(nv_device_channels(spk, 0) == 2 && nv_device_channels(ret, 1) == 2, "speaker pair channels", nv_device_channels(spk, 0));
    EXPECT(nv_device_rate(mic) == 48000 && nv_device_rate(ret) == 48000, "rate 48k", nv_device_rate(mic));
    uint32_t cd = nv_device_clock_domain(mic);
    EXPECT(cd && cd == nv_device_clock_domain(feed) && cd == nv_device_clock_domain(spk) && cd == nv_device_clock_domain(ret), "clock domain", cd);
    set_volume(mic, 1); set_volume(spk, 0);

    printf(",\"tests\":{");
    rec_t r; gen_t g; analysis a;
    /* 2. raw loopback mic pair: tone */
    rec_init(&r, 4, 48000); memset(&g, 0, sizeof g); g.mode = GEN_SINE; g.amp = 0.25f;
    run(feed, 1, &g, mic, 1, &r, 3.5);
    a = analyze(&r, 0.03f);
    printf("\"mic_loopback_tone\":{\"rms\":%.4f,\"freq\":%.2f,\"glitches\":%llu,\"onset_s\":%.3f,\"samples\":%zu}",
           a.rms, a.freq, (unsigned long long)a.glitches, a.onset_s, r.len);
    EXPECT(a.n > 0, "mic loopback captured signal (silence => TCC/routing)", a.rms);
    EXPECT(fabs(20 * log10(a.rms / (0.25 / sqrt(2)) + 1e-12)) < 1.0, "mic loopback level dB", 20 * log10(a.rms / (0.25 / sqrt(2)) + 1e-12));
    EXPECT(fabs(a.freq - 1000) < 1.0, "mic loopback freq", a.freq);
    EXPECT(a.glitches == 0, "mic loopback glitches", a.glitches);
    rec_free(&r);

    /* latency through the driver alone (host-time based) */
    rec_init(&r, 3, 48000); memset(&g, 0, sizeof g); g.mode = GEN_IMPULSE; g.amp = 0.8f; g.impulse_after = 48000;
    run(feed, 1, &g, mic, 1, &r, 2.5);
    size_t idx = 0; while (idx < r.len && fabsf(r.buf[idx]) < 0.4f) idx++;
    double lat_ms = (idx < r.len && atomic_load(&g.impulse_ns)) ? ((double)rec_time_of(&r, idx) - (double)atomic_load(&g.impulse_ns)) / 1e6 : NAN;
    printf(",\"mic_loopback_latency_ms\":%.3f", lat_ms);
    EXPECT(isfinite(lat_ms) && fabs(lat_ms) < 50, "driver loopback latency ms", lat_ms);
    rec_free(&r);

    /* 3. raw loopback speaker pair (stereo) */
    rec_init(&r, 4, 48000); memset(&g, 0, sizeof g); g.mode = GEN_SINE; g.amp = 0.25f;
    run(spk, 2, &g, ret, 2, &r, 3.5);
    a = analyze(&r, 0.03f);
    printf(",\"speaker_loopback_tone\":{\"rms\":%.4f,\"freq\":%.2f,\"glitches\":%llu,\"channel_mismatch\":%llu}",
           a.rms, a.freq, (unsigned long long)a.glitches, (unsigned long long)r.channel_mismatch);
    EXPECT(a.n > 0 && fabs(a.freq - 1000) < 1.0 && a.glitches == 0, "speaker loopback tone", a.freq);
    EXPECT(r.channel_mismatch == 0, "speaker stereo channels identical", r.channel_mismatch);
    rec_free(&r);

    /* 4. engine path, dry: Speaker -> Return -> bridge -> Feed -> Microphone */
    char err[256];
    nv_path p; nv_path_options o = {0.f, 480, 10};
    if (nv_path_open(&p, "e2e", ret, 2, feed, 1, &o, err, sizeof err) || nv_path_start(&p, err, sizeof err)) {
        printf(",\"engine_error\":\"%s\"", err); failures++;
    } else {
        rec_init(&r, 5, 48000); memset(&g, 0, sizeof g); g.mode = GEN_SINE; g.amp = 0.25f;
        run(spk, 2, &g, mic, 1, &r, 4.5);
        a = analyze(&r, 0.03f);
        printf(",\"engine_dry_tone\":{\"rms\":%.4f,\"freq\":%.2f,\"glitches\":%llu,\"stats\":", a.rms, a.freq, (unsigned long long)a.glitches);
        nv_path_stats_json(&p, stdout);
        printf("}");
        EXPECT(a.n > 0 && fabs(a.freq - 1000) < 1.0, "engine dry freq", a.freq);
        EXPECT(a.glitches == 0, "engine dry glitches", a.glitches);
        EXPECT(fabs(20 * log10(a.rms / (0.25 / sqrt(2)) + 1e-12)) < 1.0, "engine dry level dB", 20 * log10(a.rms / (0.25 / sqrt(2)) + 1e-12));
        rec_free(&r);
        rec_init(&r, 3, 48000); memset(&g, 0, sizeof g); g.mode = GEN_IMPULSE; g.amp = 0.8f; g.impulse_after = 48000;
        run(spk, 2, &g, mic, 1, &r, 2.5);
        idx = 0; while (idx < r.len && fabsf(r.buf[idx]) < 0.4f) idx++;
        lat_ms = (idx < r.len && atomic_load(&g.impulse_ns)) ? ((double)rec_time_of(&r, idx) - (double)atomic_load(&g.impulse_ns)) / 1e6 : NAN;
        printf(",\"engine_latency_ms\":%.3f", lat_ms);
        EXPECT(isfinite(lat_ms) && lat_ms > 0 && lat_ms < 120, "engine latency ms", lat_ms);
        rec_free(&r);
        nv_path_close(&p);
    }

    /* 5. engine path with denoise on stationary white noise */
    o.strength = 1.f;
    if (nv_path_open(&p, "e2e-denoise", ret, 2, feed, 1, &o, err, sizeof err) || nv_path_start(&p, err, sizeof err)) {
        printf(",\"denoise_error\":\"%s\"", err); failures++;
    } else {
        rec_init(&r, 7, 48000); memset(&g, 0, sizeof g); g.mode = GEN_NOISE; g.amp = 0.05f; g.seed = 9;
        run(spk, 2, &g, mic, 1, &r, 6.5);
        double sq = 0; size_t n = 0;
        for (size_t i = r.len > 96000 ? r.len - 96000 : 0; i < r.len; i++) { sq += (double)r.buf[i] * r.buf[i]; n++; }
        double in_rms = 0.05 / sqrt(3.0), out_rms = n ? sqrt(sq / n) : 0;
        double red = 20 * log10(in_rms / (out_rms + 1e-12));
        printf(",\"engine_denoise\":{\"input_rms\":%.4f,\"output_rms\":%.5f,\"reduction_db\":%.1f}", in_rms, out_rms, red);
        EXPECT(n > 0 && red > 6, "denoise reduction dB", red);
        rec_free(&r);
        nv_path_close(&p);
    }
    printf("},\"failures\":%d,\"notes\":\"%s\",\"ok\":%s}\n", failures, notes, failures ? "false" : "true");
    return failures ? 1 : 0;
}
