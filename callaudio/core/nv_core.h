/* SPDX-License-Identifier: MIT
 * Nivox call audio: portable real-time core (no Apple headers, testable on Linux).
 *
 *  nv_clock  - shared zero-timestamp clock so several virtual devices of the
 *              same driver share one sample-time axis (one clock domain).
 *  nv_ring   - time-indexed loopback ring used INSIDE the HAL plug-in:
 *              hidden "feed" device writes at sample time t, the visible
 *              device reads the same t. Lock-free, single writer, N readers.
 *  nv_fifo   - SPSC float FIFO (app side, between two device IO threads).
 *  nv_bridge - app side: capture block -> mono -> fixed SRC to 48 kHz ->
 *              SpeexDSP denoise -> FIFO -> adaptive (drift-compensating)
 *              SRC -> playback block. Two RT threads: one pushes, one pulls.
 *
 * Real-time contract: after *_create, the functions marked RT do not allocate,
 * lock, log or perform I/O.
 */
#ifndef NV_CORE_H
#define NV_CORE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum { NV_OK = 0, NV_EINVAL = -1, NV_ENOMEM = -2, NV_EDSP = -3, NV_INTERNAL_RATE = 48000 };

/* ---------------- shared clock ---------------- */
typedef struct nv_clock {
    uint64_t anchor_host;     /* host ticks at sample time 0 */
    double ticks_per_frame;   /* host ticks per frame at nominal rate */
    uint32_t period_frames;   /* zero time stamp period (frames) */
} nv_clock;
int nv_clock_init(nv_clock *c, uint64_t anchor_host, double host_ticks_per_second,
                  double sample_rate, uint32_t period_frames);
/* RT. Latest period boundary not after now_host. Monotonic in now_host. */
void nv_clock_zero_timestamp(const nv_clock *c, uint64_t now_host,
                             double *out_sample_time, uint64_t *out_host_time);

/* ---------------- loopback ring (driver) ---------------- */
typedef struct nv_ring nv_ring;
/* frames must be a power of two >= 1024; channels 1..8. Not RT. */
nv_ring *nv_ring_create(uint32_t frames, uint32_t channels);
void nv_ring_destroy(nv_ring *r);
/* RT, single writer. Interleaved float. Gaps since the previous write are
 * zero-filled so stale audio is never replayed; writes older than one ring
 * length are dropped. */
void nv_ring_write(nv_ring *r, int64_t sample_time, const float *src, uint32_t frames);
/* RT, any number of readers. Frames not written yet, overwritten while
 * reading, or older than one ring length are returned as silence.
 * Returns the number of valid (non-silence-filled) frames. */
uint32_t nv_ring_read(nv_ring *r, int64_t sample_time, float *dst, uint32_t frames);
/* Not RT-critical: forget content (e.g. writer stopped). */
void nv_ring_invalidate(nv_ring *r);
uint32_t nv_ring_channels(const nv_ring *r);

/* ---------------- SPSC FIFO ---------------- */
typedef struct nv_fifo nv_fifo;
nv_fifo *nv_fifo_create(size_t capacity_pow2);
void nv_fifo_destroy(nv_fifo *f);
size_t nv_fifo_capacity(const nv_fifo *f);
size_t nv_fifo_available(const nv_fifo *f);   /* RT, either side */
size_t nv_fifo_write(nv_fifo *f, const float *src, size_t n); /* RT producer */
size_t nv_fifo_peek(nv_fifo *f, float *dst, size_t n);        /* RT consumer */
void nv_fifo_consume(nv_fifo *f, size_t n);                    /* RT consumer */

/* ---------------- bridge (app) ---------------- */
typedef struct nv_bridge nv_bridge;
typedef struct nv_bridge_config {
    uint32_t in_rate, in_channels;   /* capture device format */
    uint32_t out_rate, out_channels; /* playback device format */
    uint32_t max_block;              /* largest callback block (frames) */
    uint32_t target_fill;            /* FIFO target at 48 kHz, frames */
    float strength;                  /* 0 = delayed dry, 1 = full denoise */
    double max_ppm;                  /* drift correction limit, e.g. 2000 */
} nv_bridge_config;
typedef struct nv_bridge_stats {
    uint64_t pushed, pulled, consumed, underruns, overflows, rejected_samples;
    /* consumed: frames taken from the 48 kHz FIFO by the output resampler */
    int64_t fill;          /* last observed FIFO fill, frames @48k */
    double correction_ppm; /* current consumer ratio correction */
    int primed;
} nv_bridge_stats;
nv_bridge *nv_bridge_create(const nv_bridge_config *cfg); /* not RT */
void nv_bridge_destroy(nv_bridge *b);
/* RT producer thread: interleaved float frames at in_rate. now_ns: monotonic
 * time of this callback (e.g. AudioTimeStamp host time in ns); 0 = unknown.
 * Timestamps let the consumer remove the push/pull phase sawtooth from its
 * fill estimate (otherwise equal block sizes + drift cause slow pitch wobble). */
int nv_bridge_push(nv_bridge *b, const float *src, uint32_t frames, uint64_t now_ns);
/* RT consumer thread: always fills `frames` (silence while priming/underrun). */
int nv_bridge_pull(nv_bridge *b, float *dst, uint32_t frames, uint64_t now_ns);
/* Any thread: applied by the producer on its next push. */
int nv_bridge_set_strength(nv_bridge *b, float strength);
void nv_bridge_get_stats(const nv_bridge *b, nv_bridge_stats *out);
/* Nominal processing latency excluding device/OS buffers, in output frames. */
double nv_bridge_nominal_latency_ms(const nv_bridge *b);

#ifdef __cplusplus
}
#endif
#endif
