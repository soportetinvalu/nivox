/* SPDX-License-Identifier: MIT
 * Raw (unprocessed, same-rate) loopback measurement only, not bridge/denoise.
 * The start marker is emitted and captured in-band. The endpoint is its index
 * plus ALL frames emitted by the generator, never the last nonzero sample.
 * Missing/corrupt/duplicate markers or incomplete captures fail closed.
 */
#ifndef NV_E2E_TONE_H
#define NV_E2E_TONE_H
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

static const float nv_e2e_tone_marker[] = {
    .75f, -.75f, .5f, -.5f, .625f, -.625f, .875f, -.875f
};
#define NV_E2E_TONE_MARKER_FRAMES (sizeof nv_e2e_tone_marker / sizeof *nv_e2e_tone_marker)

/* Replace the initial sine samples, without changing phase or frame count. */
static inline float nv_e2e_mark_tone(uint64_t frame, float sine) {
    return frame < NV_E2E_TONE_MARKER_FRAMES ? nv_e2e_tone_marker[frame] : sine;
}

typedef struct {
    double rms, freq, onset_s;
    uint64_t glitches;
    size_t n, start, end, marker_index, marker_count;
    int window_valid;
} nv_e2e_tone_analysis;

static inline nv_e2e_tone_analysis nv_e2e_analyze_tone(
        const float *buf, size_t len, double rate, uint64_t generated, float glitch_thr) {
    nv_e2e_tone_analysis a = {0};
    if (!buf || !isfinite(rate) || rate != 48000 || len < NV_E2E_TONE_MARKER_FRAMES)
        return a;
    for (size_t i = 0; i <= len - NV_E2E_TONE_MARKER_FRAMES; i++) {
        if (!memcmp(buf + i, nv_e2e_tone_marker, sizeof nv_e2e_tone_marker)) {
            a.marker_index = i;
            a.marker_count++;
        }
    }
    if (a.marker_count != 1 || generated > len - a.marker_index)
        return a;
    a.onset_s = a.marker_index / rate;
    a.start = a.marker_index + (size_t)(0.5 * rate); /* unchanged settling time */
    a.end = a.marker_index + (size_t)generated; /* exclusive, no amplitude trim */
    if (a.end < a.start || a.end - a.start < 1000)
        return a;
    double sq = 0;
    size_t zc = 0, first = 0, last = 0;
    for (size_t i = a.start; i < a.end; i++) {
        sq += (double)buf[i] * buf[i];
        if (i > a.start && buf[i - 1] < 0 && buf[i] >= 0) {
            if (!zc) first = i;
            last = i;
            zc++;
        }
        if (i >= a.start + 2 && fabsf(buf[i] - 2 * buf[i - 1] + buf[i - 2]) > glitch_thr)
            a.glitches++;
    }
    a.n = a.end - a.start;
    a.rms = sqrt(sq / (double)a.n);
    a.freq = zc > 1 ? (zc - 1) * rate / (double)(last - first) : 0;
    a.window_valid = 1;
    return a;
}
#endif
