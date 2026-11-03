/* SPDX-License-Identifier: MIT
 * App-side real-time bridge: capture -> SpeexDSP denoise -> drift-compensated
 * playback. Uses SpeexDSP 1.2.1 (BSD) preprocess (via cn_engine) and resampler.
 */
#include "nv_core.h"
#include "christian_noise.h"
#include <speex/speex_resampler.h>
#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

/* ---------------- SPSC FIFO ---------------- */
struct nv_fifo {
    size_t cap, mask;
    _Atomic size_t head, tail; /* head: write count, tail: read count */
    float *data;
};

nv_fifo *nv_fifo_create(size_t cap) {
    if (cap < 64 || (cap & (cap - 1))) return NULL;
    nv_fifo *f = calloc(1, sizeof *f);
    if (!f) return NULL;
    f->data = calloc(cap, sizeof(float));
    if (!f->data) { free(f); return NULL; }
    f->cap = cap; f->mask = cap - 1;
    atomic_init(&f->head, 0); atomic_init(&f->tail, 0);
    return f;
}
void nv_fifo_destroy(nv_fifo *f) { if (f) { free(f->data); free(f); } }
size_t nv_fifo_capacity(const nv_fifo *f) { return f->cap; }
size_t nv_fifo_available(const nv_fifo *f) {
    size_t h = atomic_load_explicit(&((nv_fifo *)f)->head, memory_order_acquire);
    size_t t = atomic_load_explicit(&((nv_fifo *)f)->tail, memory_order_acquire);
    return h - t;
}
size_t nv_fifo_write(nv_fifo *f, const float *src, size_t n) {
    size_t h = atomic_load_explicit(&f->head, memory_order_relaxed);
    size_t t = atomic_load_explicit(&f->tail, memory_order_acquire);
    size_t room = f->cap - (h - t);
    if (n > room) n = room;
    for (size_t i = 0; i < n; i++) f->data[(h + i) & f->mask] = src[i];
    atomic_store_explicit(&f->head, h + n, memory_order_release);
    return n;
}
size_t nv_fifo_peek(nv_fifo *f, float *dst, size_t n) {
    size_t t = atomic_load_explicit(&f->tail, memory_order_relaxed);
    size_t h = atomic_load_explicit(&f->head, memory_order_acquire);
    if (n > h - t) n = h - t;
    for (size_t i = 0; i < n; i++) dst[i] = f->data[(t + i) & f->mask];
    return n;
}
void nv_fifo_consume(nv_fifo *f, size_t n) {
    size_t t = atomic_load_explicit(&f->tail, memory_order_relaxed);
    size_t h = atomic_load_explicit(&f->head, memory_order_acquire);
    if (n > h - t) n = h - t;
    atomic_store_explicit(&f->tail, t + n, memory_order_release);
}

/* ---------------- bridge ---------------- */
#define NV_RS_QUALITY 3   /* SPEEX_RESAMPLER_QUALITY_VOIP */
/* SpeexDSP 1.2.1 reduces num/den by their gcd and rescales the fractional
 * phase with 32-bit math (multiply_frac):
 *  - den >= 65536 overflows: set_rate_frac fails half-way, phase corrupt;
 *  - a ratio that reduces to a tiny den (e.g. exactly 1/1) truncates the
 *    phase -> audible one-sample jump when the ratio changes again.
 * So: prime denominator 65003 (den^2 < 2^32) and never num == den. Ratio step
 * ~15 ppm at 48 kHz; the PI loop dithers between steps for any average. */
#define NV_RATIO_DEN 65003u
struct nv_bridge {
    nv_bridge_config cfg;
    cn_engine *dsp;
    SpeexResamplerState *in_rs, *out_rs; /* in_rs NULL when in_rate == 48k */
    nv_fifo *fifo;
    /* producer scratch */
    float *mono, *rs_out, *den_out;
    size_t rs_cap;
    _Atomic float pending_strength;
    float strength;
    /* consumer state */
    float *peek, *pull_mono;
    size_t peek_cap;
    double fill_ema, integral, corr;
    spx_uint32_t num;
    int primed;
    /* stats */
    _Atomic uint64_t pushed, pulled, consumed, underruns, overflows, rejected;
    _Atomic int64_t fill;
    _Atomic uint64_t last_push_ns;
    _Atomic uint32_t last_push_frames;
    _Atomic double corr_ppm;
    _Atomic int primed_flag;
};

static size_t next_pow2(size_t x) { size_t p = 64; while (p < x) p <<= 1; return p; }

static spx_uint32_t ratio_num(uint32_t out_rate, double corr) {
    spx_uint32_t n = (spx_uint32_t)llround((double)NV_RATIO_DEN * NV_INTERNAL_RATE / out_rate * (1.0 + corr));
    if (n % NV_RATIO_DEN == 0) n += corr >= 0 ? 1 : -1; /* keep gcd(n, den) == 1 */
    return n;
}

void nv_bridge_destroy(nv_bridge *b) {
    if (!b) return;
    cn_destroy(b->dsp);
    if (b->in_rs) speex_resampler_destroy(b->in_rs);
    if (b->out_rs) speex_resampler_destroy(b->out_rs);
    nv_fifo_destroy(b->fifo);
    free(b->mono); free(b->rs_out); free(b->den_out); free(b->peek); free(b->pull_mono);
    free(b);
}

nv_bridge *nv_bridge_create(const nv_bridge_config *c) {
    if (!c || c->in_rate < 8000 || c->in_rate > 192000 || c->out_rate < 8000 || c->out_rate > 192000
        || c->in_channels < 1 || c->in_channels > 8 || c->out_channels < 1 || c->out_channels > 8
        || c->max_block < 16 || c->max_block > 8192 || c->target_fill < 64 || c->target_fill > 48000
        || !(c->strength >= 0 && c->strength <= 1) || !(c->max_ppm >= 0 && c->max_ppm <= 20000))
        return NULL;
    nv_bridge *b = calloc(1, sizeof *b);
    if (!b) return NULL;
    b->cfg = *c;
    int err = 0;
    b->dsp = cn_create(CN_SAMPLE_RATE, 1, c->strength);
    b->strength = c->strength;
    atomic_init(&b->pending_strength, c->strength);
    if (c->in_rate != NV_INTERNAL_RATE) {
        b->in_rs = speex_resampler_init(1, c->in_rate, NV_INTERNAL_RATE, NV_RS_QUALITY, &err);
        if (b->in_rs) speex_resampler_skip_zeros(b->in_rs);
    }
    b->rs_cap = (size_t)ceil((double)c->max_block * NV_INTERNAL_RATE / c->in_rate) + 64;
    /* Output resampler: integer fraction so a few-ppm correction is representable. */
    /* Shared-clock endpoints need an exact, fixed nominal ratio. Their
     * presentation-time offset is latency, not relative clock drift. */
    b->num = c->max_ppm > 0 ? ratio_num(c->out_rate, 0) : NV_INTERNAL_RATE;
    spx_uint32_t initial_den = c->max_ppm > 0 ? NV_RATIO_DEN : c->out_rate;
    b->out_rs = speex_resampler_init_frac(1, b->num, initial_den,
                                          NV_INTERNAL_RATE, c->out_rate, NV_RS_QUALITY, &err);
    b->peek_cap = (size_t)ceil((double)c->max_block * NV_INTERNAL_RATE / c->out_rate * (1 + c->max_ppm * 1e-6)) + 256;
    size_t fifo_cap = next_pow2(4 * ((size_t)c->target_fill + b->rs_cap + b->peek_cap));
    b->fifo = nv_fifo_create(fifo_cap);
    b->mono = calloc(c->max_block, sizeof(float));
    b->rs_out = calloc(b->rs_cap, sizeof(float));
    b->den_out = calloc(b->rs_cap, sizeof(float));
    b->peek = calloc(b->peek_cap, sizeof(float));
    b->pull_mono = calloc(c->max_block, sizeof(float));
    if (!b->dsp || (c->in_rate != NV_INTERNAL_RATE && !b->in_rs) || !b->out_rs || !b->fifo
        || !b->mono || !b->rs_out || !b->den_out || !b->peek || !b->pull_mono) {
        nv_bridge_destroy(b);
        return NULL;
    }
    /* Pre-size SpeexDSP internal tables for every ratio we may request, so
     * later corrections on the RT thread never reallocate. */
    const spx_uint32_t den = NV_RATIO_DEN;
    if (c->max_ppm > 0 && (speex_resampler_set_rate_frac(b->out_rs, ratio_num(c->out_rate, c->max_ppm * 1e-6), den, NV_INTERNAL_RATE, c->out_rate)
        || speex_resampler_set_rate_frac(b->out_rs, ratio_num(c->out_rate, -c->max_ppm * 1e-6), den, NV_INTERNAL_RATE, c->out_rate)
        || speex_resampler_set_rate_frac(b->out_rs, b->num, den, NV_INTERNAL_RATE, c->out_rate))) {
        nv_bridge_destroy(b);
        return NULL;
    }
    speex_resampler_reset_mem(b->out_rs);
    speex_resampler_skip_zeros(b->out_rs);
    atomic_init(&b->pushed, 0); atomic_init(&b->pulled, 0); atomic_init(&b->consumed, 0); atomic_init(&b->underruns, 0);
    atomic_init(&b->overflows, 0); atomic_init(&b->rejected, 0); atomic_init(&b->fill, 0);
    atomic_init(&b->corr_ppm, 0.0); atomic_init(&b->primed_flag, 0);
    atomic_init(&b->last_push_ns, 0);
    atomic_init(&b->last_push_frames, 0);
    return b;
}

int nv_bridge_set_strength(nv_bridge *b, float s) {
    if (!b || !(s >= 0 && s <= 1)) return NV_EINVAL;
    atomic_store_explicit(&b->pending_strength, s, memory_order_relaxed);
    return NV_OK;
}

static int push_block(nv_bridge *b, const float *src, uint32_t n) {
    const uint32_t ch = b->cfg.in_channels;
    uint64_t rejected = 0;
    for (uint32_t i = 0; i < n; i++) {
        float acc = 0;
        for (uint32_t c = 0; c < ch; c++) {
            float x = src[(size_t)i * ch + c];
            if (!isfinite(x)) { x = 0; rejected++; }
            acc += x;
        }
        acc /= (float)ch;
        b->mono[i] = fminf(1.f, fmaxf(-1.f, acc));
    }
    if (rejected) atomic_fetch_add_explicit(&b->rejected, rejected, memory_order_relaxed);
    const float *x = b->mono;
    size_t m = n;
    if (b->in_rs) {
        spx_uint32_t in_len = n, out_len = (spx_uint32_t)b->rs_cap;
        speex_resampler_process_float(b->in_rs, 0, b->mono, &in_len, b->rs_out, &out_len);
        for (spx_uint32_t i = 0; i < out_len; i++) b->rs_out[i] = fminf(1.f, fmaxf(-1.f, b->rs_out[i]));
        x = b->rs_out; m = out_len;
    }
    if (m && cn_process(b->dsp, x, b->den_out, m) != CN_OK) return NV_EDSP;
    size_t w = nv_fifo_write(b->fifo, b->den_out, m);
    if (w < m) atomic_fetch_add_explicit(&b->overflows, 1, memory_order_relaxed);
    return NV_OK;
}

int nv_bridge_push(nv_bridge *b, const float *src, uint32_t frames, uint64_t now_ns) {
    if (!b || (frames && !src)) return NV_EINVAL;
    float s = atomic_load_explicit(&b->pending_strength, memory_order_relaxed);
    if (s != b->strength) { cn_set_strength(b->dsp, s); b->strength = s; }
    uint32_t done = 0;
    while (done < frames) {
        uint32_t n = frames - done;
        if (n > b->cfg.max_block) n = b->cfg.max_block;
        int rc = push_block(b, src + (size_t)done * b->cfg.in_channels, n);
        if (rc) return rc;
        done += n;
    }
    atomic_fetch_add_explicit(&b->pushed, frames, memory_order_relaxed);
    atomic_store_explicit(&b->last_push_frames, frames, memory_order_relaxed);
    atomic_store_explicit(&b->last_push_ns, now_ns, memory_order_release);
    return NV_OK;
}

/* Fill-level PI controller. Plant: d(fill)/dt = 48000 * (drift - corr).
 * Gains: wn ~0.2 rad/s, damping ~1 (settles in ~20-30 s); the EMA
 * removes the block-size sawtooth before it reaches the ratio. */
#define NV_KP 8.0e-6
#define NV_KI 8.0e-7
#define NV_EMA_TAU 1.0

static void update_ratio(nv_bridge *b, double avail, uint32_t frames) {
    const double dt = (double)frames / b->cfg.out_rate;
    const double alpha = dt / (NV_EMA_TAU + dt);
    b->fill_ema += alpha * (avail - b->fill_ema);
    const double err = b->fill_ema - (double)b->cfg.target_fill;
    const double lim = b->cfg.max_ppm * 1e-6;
    double integral = b->integral + err * dt;
    double corr = NV_KP * err + NV_KI * integral;
    if (corr > lim) corr = lim;
    else if (corr < -lim) corr = -lim;
    else b->integral = integral; /* anti-windup: integrate only when unsaturated */
    b->corr = corr;
    spx_uint32_t num = ratio_num(b->cfg.out_rate, corr);
    if (num != b->num && speex_resampler_set_rate_frac(b->out_rs, num, NV_RATIO_DEN, NV_INTERNAL_RATE,
                                                       b->cfg.out_rate) == RESAMPLER_ERR_SUCCESS)
        b->num = num;
    atomic_store_explicit(&b->corr_ppm, corr * 1e6, memory_order_relaxed);
}

static void write_out(nv_bridge *b, float *dst, const float *mono, uint32_t from, uint32_t to) {
    const uint32_t ch = b->cfg.out_channels;
    for (uint32_t i = from; i < to; i++)
        for (uint32_t c = 0; c < ch; c++) dst[(size_t)i * ch + c] = mono ? mono[i] : 0.f;
}

static int pull_block(nv_bridge *b, float *dst, uint32_t frames, uint64_t now_ns) {
    size_t avail = nv_fifo_available(b->fifo);
    /* frames the producer has captured since its last push, not yet in FIFO */
    double inflight = 0;
    uint64_t pushed_ns = atomic_load_explicit(&b->last_push_ns, memory_order_acquire);
    if (now_ns && pushed_ns && now_ns > pushed_ns) {
        /* at most one producer block can be in flight; a stalled producer must
         * not inflate the estimate (it would starve the FIFO) */
        double cap = (double)atomic_load_explicit(&b->last_push_frames, memory_order_relaxed)
                     * NV_INTERNAL_RATE / b->cfg.in_rate;
        inflight = (double)(now_ns - pushed_ns) * 1e-9 * NV_INTERNAL_RATE;
        if (inflight > cap) inflight = cap;
    }
    atomic_store_explicit(&b->fill, (int64_t)avail, memory_order_relaxed);
    if (!b->primed) {
        if (avail < b->cfg.target_fill) { write_out(b, dst, NULL, 0, frames); return NV_OK; }
        /* start exactly at target: discard any backlog accumulated while idle */
        nv_fifo_consume(b->fifo, avail - b->cfg.target_fill);
        avail = b->cfg.target_fill;
        b->fill_ema = (double)avail + inflight;
        b->primed = 1;
        atomic_store_explicit(&b->primed_flag, 1, memory_order_relaxed);
    }
    if (b->cfg.max_ppm > 0) update_ratio(b, (double)avail + inflight, frames);
    uint32_t done = 0;
    while (done < frames) {
        size_t got = nv_fifo_peek(b->fifo, b->peek, b->peek_cap);
        spx_uint32_t in_len = (spx_uint32_t)got, out_len = frames - done;
        speex_resampler_process_float(b->out_rs, 0, b->peek, &in_len, b->pull_mono + done, &out_len);
        nv_fifo_consume(b->fifo, in_len);
        atomic_fetch_add_explicit(&b->consumed, in_len, memory_order_relaxed);
        done += out_len;
        if (done < frames && in_len == 0 && out_len == 0) {
            /* underrun: mute the rest and re-prime at target */
            memset(b->pull_mono + done, 0, sizeof(float) * (frames - done));
            atomic_fetch_add_explicit(&b->underruns, 1, memory_order_relaxed);
            b->primed = 0;
            atomic_store_explicit(&b->primed_flag, 0, memory_order_relaxed);
            b->integral = 0;
            done = frames;
        }
    }
    write_out(b, dst, b->pull_mono, 0, frames);
    return NV_OK;
}

int nv_bridge_pull(nv_bridge *b, float *dst, uint32_t frames, uint64_t now_ns) {
    if (!b || (frames && !dst)) return NV_EINVAL;
    uint32_t done = 0;
    while (done < frames) {
        uint32_t n = frames - done;
        if (n > b->cfg.max_block) n = b->cfg.max_block;
        pull_block(b, dst + (size_t)done * b->cfg.out_channels, n, now_ns);
        done += n;
    }
    atomic_fetch_add_explicit(&b->pulled, frames, memory_order_relaxed);
    return NV_OK;
}

void nv_bridge_get_stats(const nv_bridge *cb, nv_bridge_stats *o) {
    nv_bridge *b = (nv_bridge *)cb;
    o->pushed = atomic_load(&b->pushed); o->pulled = atomic_load(&b->pulled); o->consumed = atomic_load(&b->consumed);
    o->underruns = atomic_load(&b->underruns); o->overflows = atomic_load(&b->overflows);
    o->rejected_samples = atomic_load(&b->rejected); o->fill = atomic_load(&b->fill);
    o->correction_ppm = atomic_load(&b->corr_ppm); o->primed = atomic_load(&b->primed_flag);
}

double nv_bridge_nominal_latency_ms(const nv_bridge *b) {
    double ms = 1000.0 * (CN_LATENCY + (double)b->cfg.target_fill) / NV_INTERNAL_RATE;
    ms += 1000.0 * speex_resampler_get_input_latency(b->out_rs) / NV_INTERNAL_RATE;
    if (b->in_rs) ms += 1000.0 * speex_resampler_get_output_latency(b->in_rs) / NV_INTERNAL_RATE;
    return ms;
}
