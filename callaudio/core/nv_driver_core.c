/* SPDX-License-Identifier: MIT
 * Shared clock + time-indexed loopback ring for the Nivox HAL plug-in.
 * Pure C11, no Apple headers: unit- and sanitizer-tested on Linux.
 */
#include "nv_core.h"
#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

int nv_clock_init(nv_clock *c, uint64_t anchor, double ticks_per_second,
                  double rate, uint32_t period) {
    if (!c || !(ticks_per_second > 0) || !(rate >= 8000 && rate <= 384000) || period == 0)
        return NV_EINVAL;
    c->anchor_host = anchor;
    c->ticks_per_frame = ticks_per_second / rate;
    c->period_frames = period;
    return NV_OK;
}

void nv_clock_zero_timestamp(const nv_clock *c, uint64_t now, double *st, uint64_t *ht) {
    const double ticks_per_period = c->ticks_per_frame * (double)c->period_frames;
    uint64_t index = 0;
    if (now > c->anchor_host)
        index = (uint64_t)floor((double)(now - c->anchor_host) / ticks_per_period);
    /* floating rounding may place the boundary one tick after now: step back */
    uint64_t host = c->anchor_host + (uint64_t)llround((double)index * ticks_per_period);
    while (index > 0 && host > now) {
        index--;
        host = c->anchor_host + (uint64_t)llround((double)index * ticks_per_period);
    }
    *st = (double)index * (double)c->period_frames;
    *ht = host;
}

/* Seqlock-style ring. Writer: pending=end_new (relaxed); release fence; data
 * (relaxed atomics); end=end_new (release). Reader: e=end (acquire); data;
 * acquire fence; p=pending. Frames whose ring slot may have been touched by a
 * write in flight (s < p - frames) or not yet published (s >= e) are muted. */
#define NV_EMPTY INT64_MIN
struct nv_ring {
    uint32_t frames, mask, channels;
    _Atomic int64_t end, pending;
    _Atomic float *data;
};

nv_ring *nv_ring_create(uint32_t frames, uint32_t channels) {
    if (frames < 1024 || (frames & (frames - 1)) || channels < 1 || channels > 8) return NULL;
    nv_ring *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->data = calloc((size_t)frames * channels, sizeof *r->data);
    if (!r->data) { free(r); return NULL; }
    r->frames = frames; r->mask = frames - 1; r->channels = channels;
    for (size_t i = 0; i < (size_t)frames * channels; i++)
        atomic_init(&r->data[i], 0.f);
    atomic_init(&r->end, NV_EMPTY);
    atomic_init(&r->pending, NV_EMPTY);
    return r;
}

void nv_ring_destroy(nv_ring *r) {
    if (!r) return;
    free((void *)r->data);
    free(r);
}

uint32_t nv_ring_channels(const nv_ring *r) { return r ? r->channels : 0; }

static void put_frames(nv_ring *r, int64_t t, const float *src, int64_t n) {
    const uint32_t ch = r->channels;
    for (int64_t i = 0; i < n; i++) {
        _Atomic float *slot = &r->data[(size_t)((uint64_t)(t + i) & r->mask) * ch];
        for (uint32_t c = 0; c < ch; c++) {
            float x = src ? src[(size_t)i * ch + c] : 0.f;
            if (!isfinite(x)) x = 0.f;
            atomic_store_explicit(&slot[c], x, memory_order_relaxed);
        }
    }
}

void nv_ring_write(nv_ring *r, int64_t t, const float *src, uint32_t frames) {
    if (!r || !frames) return;
    const int64_t size = r->frames;
    int64_t e = atomic_load_explicit(&r->end, memory_order_relaxed);
    int64_t n = frames;
    if (e != NV_EMPTY && t + n <= e - size) return;      /* too old: drop */
    if (e != NV_EMPTY && t < e - size) {                  /* partially too old */
        int64_t skip = (e - size) - t;
        t += skip; n -= skip; src = src ? src + (size_t)skip * r->channels : NULL;
    }
    int64_t new_end = (e == NV_EMPTY || t + n > e) ? t + n : e;
    atomic_store_explicit(&r->pending, new_end, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
    if (e != NV_EMPTY && t > e) {                        /* gap: zero stale slots */
        int64_t gap = t - e, from = e;
        if (gap > size) { from = t - size; gap = size; }
        put_frames(r, from, NULL, gap);
    }
    put_frames(r, t, src, n);
    atomic_store_explicit(&r->end, new_end, memory_order_release);
}

uint32_t nv_ring_read(nv_ring *r, int64_t t, float *dst, uint32_t frames) {
    if (!r || !dst || !frames) return 0;
    const uint32_t ch = r->channels;
    const int64_t size = r->frames;
    int64_t e = atomic_load_explicit(&r->end, memory_order_acquire);
    if (e == NV_EMPTY) { memset(dst, 0, sizeof(float) * frames * ch); return 0; }
    for (uint32_t i = 0; i < frames; i++) {
        _Atomic float *slot = &r->data[(size_t)((uint64_t)(t + i) & r->mask) * ch];
        for (uint32_t c = 0; c < ch; c++)
            dst[(size_t)i * ch + c] = atomic_load_explicit(&slot[c], memory_order_relaxed);
    }
    atomic_thread_fence(memory_order_acquire);
    int64_t p = atomic_load_explicit(&r->pending, memory_order_relaxed);
    if (p == NV_EMPTY) { memset(dst, 0, sizeof(float) * frames * ch); return 0; }
    int64_t lo = p - size, hi = e;   /* valid window [lo, hi) */
    uint32_t valid = 0;
    for (uint32_t i = 0; i < frames; i++) {
        int64_t s = t + i;
        if (s < lo || s >= hi) memset(&dst[(size_t)i * ch], 0, sizeof(float) * ch);
        else valid++;
    }
    return valid;
}

void nv_ring_invalidate(nv_ring *r) {
    if (!r) return;
    /* Publish "empty" first so readers mute immediately, then clear slots. */
    atomic_store_explicit(&r->pending, NV_EMPTY, memory_order_relaxed);
    atomic_store_explicit(&r->end, NV_EMPTY, memory_order_release);
    put_frames(r, 0, NULL, r->frames);
}
