/* SPDX-License-Identifier: MIT */
#ifndef CHRISTIAN_NOISE_H
#define CHRISTIAN_NOISE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct cn_engine cn_engine;
enum { CN_OK=0, CN_INVALID=-1, CN_SAMPLE_RATE=48000, CN_FRAME=480, CN_LATENCY=960 };
/* SpeexDSP 1.2.1 adapter. Create/reset/destroy outside the audio callback.
   One owner per instance. No weights, AGC, dereverb, echo cancellation or VAD.
   Samples: normalized finite float32 mono at 48 kHz. No resampler is hidden here.
   strength: 0 = delayed dry, 1 = full suppression (not a noise reduction dB knob).
   NULL means invalid configuration or allocation failure. */
cn_engine *cn_create(unsigned sample_rate, unsigned channels, float strength);
void cn_destroy(cn_engine *engine);
int cn_reset(cn_engine *engine);
int cn_set_strength(cn_engine *engine, float strength);
/* Exactly count samples in/out, arbitrary block length. Exact in-place allowed;
   otherwise buffers must not overlap. Validation errors do not advance state.
   No malloc, locks, file or network I/O after creation; warm-up done in create.
   Continuous stream has 960 samples nominal delay (20 ms), excluding host I/O.
   To drain an offline recording, feed 960 zeros, then crop 960 leading samples.
   Call cn_reset between unrelated recordings. Zero-count with NULL buffers OK.
   Do not call setters/reset/process concurrently on the same instance. */
int cn_process(cn_engine *engine, const float *input, float *output, size_t count);
float cn_last_vad(const cn_engine *engine); /* compatibility only: 0, NOT a VAD */
#ifdef __cplusplus
}
#endif
#endif
