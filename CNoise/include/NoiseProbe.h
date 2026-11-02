/* SPDX-License-Identifier: MIT */
#ifndef NOISE_PROBE_H
#define NOISE_PROBE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct np_session np_session;
enum { NP_OK=0, NP_LIMIT=1, NP_INVALID=-1, NP_OVERFLOW=-2, NP_CLOSED=-3,
       NP_DSP=-4, NP_RATE=48000, NP_DELAY=960, NP_MAX_FRAMES=720000 };
/* One SPSC producer (tap), one serialized worker (pump/finish).
 * Create/close/destroy outside callback. Never reset/reuse a session.
 * First input channel only, finite normalized float32, exactly 48 kHz.
 * Queue 16384 frames; fail closed on overrun, never silently overwrite.
 * SpeexDSP 1.2.1: full processed, noise suppression -15 dB, no AGC/VAD.
 * Nominal DSP + adapter delay: 960 samples; finish flushes and crops it.
 * Recorder only: no live output or virtual device. */
np_session *np_create(unsigned rate, unsigned channels, size_t max_frames);
void np_close(np_session *s); /* control thread; wait for in-flight producer */
void np_destroy(np_session *s); /* after callback and worker quiesce */
int np_push(np_session *s, const float *input, size_t count);
int np_pump(np_session *s); /* worker; no I/O or allocation */
int np_finish(np_session *s); /* after close: drain, flush, align */
void np_abort(np_session *s); /* tap format changed: sticky invalid capture */
int np_error(const np_session *s);
size_t np_received(const np_session *s);
size_t np_count(const np_session *s); /* 0 until successful finish */
const float *np_raw(const np_session *s); /* immutable after finish */
const float *np_clean(const np_session *s);
#ifdef __cplusplus
}
#endif
#endif
