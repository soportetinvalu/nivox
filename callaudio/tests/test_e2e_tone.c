/* SPDX-License-Identifier: MIT
 * Portable oracle tests; synthetic transport, production generator + analyzer.
 * NOT CoreAudio/HAL or native Mac acceptance.
 */
#include "nv_e2e_tone.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
/* Only timestamp plumbing is synthetic. generator.inc is extracted unchanged
 * from nv_e2e.c by the runner, so these tests exercise the emitted marker too. */
typedef struct { double mSampleTime; uint64_t mHostTime; unsigned mFlags; } AudioTimeStamp;
enum { kAudioTimeStampSampleTimeValid = 1, kAudioTimeStampHostTimeValid = 2 };
static uint64_t nv_host_to_ns(uint64_t x) { return x; }
#include "generator.inc"

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
static unsigned positives, negatives;
static int accepted(nv_e2e_tone_analysis a) {
    return a.window_valid && a.n > 0 && a.glitches == 0 && fabs(a.freq - 1000) < 1 &&
        fabs(20 * log10(a.rms / (.25 / sqrt(2)) + 1e-12)) < 1;
}
static void reject(const char *name, float *x, size_t len, uint64_t generated, int glitch) {
    nv_e2e_tone_analysis a = nv_e2e_analyze_tone(x, len, 48000, generated, .03f);
    if (accepted(a) || (glitch && !a.glitches)) {
        fprintf(stderr, "FAIL negative %s: accepted=%d glitches=%llu valid=%d\n",
                name, accepted(a), (unsigned long long)a.glitches, a.window_valid);
        exit(1);
    }
    negatives++;
}
int main(void) {
    const size_t generated = 169472, onset = 2496, end = onset + generated;
    const size_t tail[] = {0, 1, 2268, 3892, 4800, 5932, 7744, 8280, 48000};
    size_t len = end + 48000;
    float *x = calloc(len, sizeof *x), *clean = calloc(len, sizeof *clean);
    float *stereo = calloc(generated * 2, sizeof *stereo);
    CHECK(x && clean && stereo);
    gen_t g = {0}; g.mode = GEN_SINE; g.amp = .25f; g.rate = 48000; g.marked_tone = 1;
    AudioTimeStamp ts = {0, 1, kAudioTimeStampSampleTimeValid | kAudioTimeStampHostTimeValid};
    /* Deliberately split the marker across two real generator callbacks. */
    gen_cb(&g, stereo, 3, 2, &ts);
    ts.mSampleTime = 3;
    gen_cb(&g, stereo + 6, (uint32_t)generated - 3, 2, &ts);
    CHECK(g.frames == generated && g.timestamp_jumps == 0);
    for (size_t i = 0; i < generated; i++) {
        CHECK(stereo[2*i] == stereo[2*i+1]);
        clean[onset+i] = stereo[2*i];
    }
    CHECK(!memcmp(clean + onset, nv_e2e_tone_marker, sizeof nv_e2e_tone_marker));
    CHECK(nv_e2e_mark_tone(8, .123f) == .123f);
    memcpy(x, clean, len * sizeof *x);
    for (size_t k = 0; k < sizeof tail / sizeof *tail; k++) {
        nv_e2e_tone_analysis a = nv_e2e_analyze_tone(x, end + tail[k], 48000, g.frames, .03f);
        CHECK(accepted(a));
        CHECK(a.marker_count == 1 && a.marker_index == onset);
        CHECK(a.start == onset + 24000 && a.end == end && a.n == generated - 24000);
        positives++;
    }
    /* Leading latency is not part of the generated segment. */
    nv_e2e_tone_analysis a = nv_e2e_analyze_tone(clean + onset, generated, 48000, generated, .03f);
    CHECK(accepted(a) && a.marker_index == 0 && a.end == generated); positives++;
    const size_t cuts[][2] = {{onset+60005,1},{onset+60005,32},{onset+60005,480},
        {end-1005,480},{end-480,480},{end-4800,4800},{end-24000,24000},
        {end-1,1}};
    for (size_t k = 0; k < sizeof cuts / sizeof *cuts; k++) {
        memcpy(x, clean, len * sizeof *x);
        memset(x + cuts[k][0], 0, cuts[k][1] * sizeof *x);
        reject("real zero cut incl terminal", x, len, generated, 1);
    }
    memcpy(x, clean, len * sizeof *x);
    x[onset+60005] += .2f;
    reject("single spike", x, len, generated, 1);
    memcpy(x, clean, len * sizeof *x);
    reject("truncated recording", x, end-1, generated, 0);
    reject("generated overflow", x, len, UINT64_MAX, 0);
    reject("no generated frames", x, len, 0, 0);
    reject("too short generated signal", x, len, 24000+999, 0);
    x[onset] = 0;
    reject("corrupt start marker", x, len, generated, 0);
    memcpy(x, clean, len * sizeof *x);
    memset(x+onset, 0, sizeof nv_e2e_tone_marker);
    reject("missing start marker", x, len, generated, 0);
    memcpy(x, clean, len * sizeof *x);
    memcpy(x+end+100, nv_e2e_tone_marker, sizeof nv_e2e_tone_marker);
    reject("duplicate start marker", x, len, generated, 0);
    memset(x, 0, len * sizeof *x);
    reject("silence", x, len, generated, 0);
    memcpy(x+onset, nv_e2e_tone_marker, sizeof nv_e2e_tone_marker);
    reject("marker without tone", x, len, generated, 0);
    CHECK(!nv_e2e_analyze_tone(x, len, 44100, generated, .03f).window_valid);
    CHECK(!nv_e2e_analyze_tone(NULL, 0, 48000, generated, .03f).window_valid);
    negatives += 2;
    /* An unmarked engine tone remains the exact original sine (no marker). */
    memset(&g, 0, sizeof g); g.mode = GEN_SINE; g.amp = .25f; g.rate = 48000;
    gen_cb(&g, stereo, 32, 1, NULL);
    double phase = 0;
    for (size_t i=0; i<32; i++) {
        CHECK(stereo[i] == .25f * (float)sin(phase));
        phase += 2 * M_PI * 1000 / 48000;
        if (phase > 2 * M_PI) phase -= 2 * M_PI;
    }
    positives++;
    printf("{\"ok\":true,\"positives\":%u,\"negatives\":%u,\"production_generator\":true}\n", positives, negatives);
    free(stereo); free(clean); free(x);
    return 0;
}
