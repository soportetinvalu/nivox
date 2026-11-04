/* SPDX-License-Identifier: MIT */
#ifndef NOISE_PROBE_H
#define NOISE_PROBE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct np_session np_session;
enum { NP_OK=0, NP_LIMIT=1, NP_INVALID=-1, NP_OVERFLOW=-2, NP_CLOSED=-3,
       NP_DSP=-4, NP_RATE=48000, NP_DELAY=960, NP_MAX_FRAMES=720000 };
/* One SPSC producer (tap), one serialized worker (pump/finish).
 * Create/close/destroy outside callback. Never reset/reuse a session.
 * One caller-selected input channel, finite normalized float32, exactly 48 kHz.
 * Queue 16384 frames; fail closed on overrun, never silently overwrite.
 * SpeexDSP 1.2.1: full processed, noise suppression -15 dB, no AGC/VAD.
 * Nominal DSP + adapter delay: 960 samples; finish flushes and crops it.
 * Recorder only: no live output or virtual device. */
np_session *np_create(unsigned rate, unsigned channels, size_t max_frames);
void np_close(np_session *s); /* control thread; wait for in-flight producer */
void np_destroy(np_session *s); /* after callback and worker quiesce */
int np_push(np_session *s, const float *input, size_t count);
/* Same producer; contiguous valid sample times required, discontinuity is sticky. */
int np_push_timed(np_session *s, const float *input, size_t count, int64_t sample_time);
/* Core Audio IOProc producer: channel `offset` of `stride`-interleaved Float32 frames
 * (stride 1 = planar). Chunks through internal scratch; sample_time = device time of
 * the first frame. Bad layout/stride/offset is a sticky NP_INVALID. */
int np_push_strided_timed(np_session *s, const float *base, size_t frames,
                          size_t stride, size_t offset, int64_t sample_time);
size_t np_blocks(const np_session *s); /* producer calls seen, even empty ones */
int np_pump(np_session *s); /* worker; no I/O or allocation */
int np_finish(np_session *s); /* after close: drain, flush, align */
void np_abort(np_session *s); /* tap format changed: sticky invalid capture */
int np_error(const np_session *s);
size_t np_received(const np_session *s);
size_t np_count(const np_session *s); /* 0 until successful finish */
const float *np_raw(const np_session *s); /* immutable after finish */
const float *np_clean(const np_session *s);
/* Offline only, distinct input/output arrays, at most NP_MAX_FRAMES.
 * Explicit copy gain 0..24 dB, mild wet mix 0..0.25; no automatic gain.
 * Same scalar for both A/B copies, capped by their shared -1 dBFS peak.
 * Negative raw/wet correlation falls back to dry for the entire take.
 * Original capture remains untouched. Statistics are signal, not speech metrics. */
typedef struct np_ab_levels {
    double applied_gain_db, raw_rms_dbfs, filtered_rms_dbfs, peak_dbfs;
    size_t full_scale_samples;
    int gain_limited, bypassed;
} np_ab_levels;
int np_prepare_ab(const float *raw, const float *wet, size_t count,
                  double gain_db, double mix, float *a, float *b,
                  np_ab_levels *levels);
#ifdef __cplusplus
}
#endif
#endif
