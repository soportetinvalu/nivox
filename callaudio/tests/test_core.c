/* SPDX-License-Identifier: MIT
 * Linux tests for the Nivox call-audio core: shared clock, loopback ring
 * (incl. concurrent writer/readers), SPSC FIFO and the real-time bridge
 * (real SpeexDSP denoise + resampler) under simulated clock drift.
 * Prints one JSON object on the last line. Non-zero exit on any failure.
 */
#define _POSIX_C_SOURCE 200809L
#include "nv_core.h"
#include "nv_signal.h"
#include "nv_watch.h"
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef __APPLE__
#include <mach/mach_time.h>
#endif
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#ifdef WRAP_ALLOC
static atomic_int tracking, allocations;
void *__real_malloc(size_t); void *__real_calloc(size_t, size_t);
void *__real_realloc(void *, size_t); void __real_free(void *);
void *__wrap_malloc(size_t n) { if (atomic_load(&tracking)) atomic_fetch_add(&allocations, 1); return __real_malloc(n); }
void *__wrap_calloc(size_t n, size_t z) { if (atomic_load(&tracking)) atomic_fetch_add(&allocations, 1); return __real_calloc(n, z); }
void *__wrap_realloc(void *p, size_t n) { if (atomic_load(&tracking)) atomic_fetch_add(&allocations, 1); return __real_realloc(p, n); }
void __wrap_free(void *p) { if (atomic_load(&tracking)) atomic_fetch_add(&allocations, 1); __real_free(p); }
#define TRACK(on) atomic_store(&tracking, (on))
#else
#define TRACK(on) ((void)0)
#endif

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
    fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static double ms_now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e3 + t.tv_nsec / 1e6; }

/* ---------------- clock ---------------- */
static void test_clock(void) {
    nv_clock c;
    CHECK(nv_clock_init(&c, 1000, 24e6, 48000, 16384) == NV_OK, "init");
    CHECK(nv_clock_init(&c, 0, 0, 48000, 16384) == NV_EINVAL, "reject zero ticks");
    nv_clock_init(&c, 1000, 24e6, 48000, 16384);
    double st, prev_st = -1; uint64_t ht, prev_ht = 0;
    nv_clock_zero_timestamp(&c, 10, &st, &ht);
    CHECK(st == 0 && ht == 1000, "before anchor -> period 0");
    const double tpp = 24e6 / 48000 * 16384;
    uint64_t now = 1000; unsigned seed = 7;
    for (int i = 0; i < 200000; i++) {
        seed = seed * 1103515245u + 12345u;
        now += seed % 400000;
        nv_clock_zero_timestamp(&c, now, &st, &ht);
        CHECK(ht <= now && (double)(now - ht) < tpp + 1, "boundary not after now (%llu %llu)", (unsigned long long)ht, (unsigned long long)now);
        CHECK(fmod(st, 16384) == 0 && st >= prev_st && ht >= prev_ht, "monotonic period multiples");
        double expect_host = 1000 + st / 16384 * tpp;
        CHECK(fabs((double)ht - expect_host) <= 1.0, "host/sample consistency");
        prev_st = st; prev_ht = ht;
        if (failures > 5) return;
    }
    /* 24 h later: still exact multiples, host/sample relation preserved */
    nv_clock_zero_timestamp(&c, 1000 + (uint64_t)(24e6 * 86400.0), &st, &ht);
    CHECK(fabs(st - floor(86400.0 * 48000 / 16384) * 16384) < 0.5, "24h sample time");
}

/* ---------------- ring ---------------- */
static float fval(int64_t s) { return (float)((s % 4093) + 1) / 4096.f; }

static void test_ring_basic(void) {
    CHECK(!nv_ring_create(1000, 1) && !nv_ring_create(4096, 0) && !nv_ring_create(4096, 9), "reject bad params");
    nv_ring *r = nv_ring_create(4096, 2);
    float in[2 * 8192], out[2 * 8192];
    CHECK(nv_ring_read(r, 0, out, 10) == 0 && out[0] == 0, "empty reads silence");
    for (int64_t s = 0; s < 8192; s++) in[2 * s] = fval(s), in[2 * s + 1] = -fval(s);
    nv_ring_write(r, 0, in, 8192);
    uint32_t v = nv_ring_read(r, 8192 - 4096, out, 4096);
    CHECK(v == 4096 && memcmp(out, in + 2 * 4096, sizeof(float) * 2 * 4096) == 0, "read newest ring length exact");
    v = nv_ring_read(r, 100, out, 10);
    CHECK(v == 0 && out[0] == 0 && out[19] == 0, "older than ring -> silence");
    v = nv_ring_read(r, 8190, out, 10);
    CHECK(v == 2 && out[0] == fval(8190) && out[4] == 0, "future frames silence (v=%u)", v);
    /* small gap: stale aliases (from t-4096) must not reappear */
    nv_ring_write(r, 8192 + 1000, in, 16);
    v = nv_ring_read(r, 8192, out, 1000);
    int stale = 0; for (int i = 0; i < 2000; i++) stale |= out[i] != 0;
    CHECK(v == 1000 && !stale, "gap zero-filled, no stale replay");
    v = nv_ring_read(r, 9192, out, 16);
    CHECK(v == 16 && memcmp(out, in, sizeof(float) * 32) == 0, "data after gap");
    /* large gap > ring */
    nv_ring_write(r, 100000, in, 8);
    v = nv_ring_read(r, 100000 - 4000, out, 4000);
    stale = 0; for (int i = 0; i < 8000; i++) stale |= out[i] != 0;
    CHECK(!stale, "large gap silence");
    /* too-old write is dropped, recent content kept */
    nv_ring_write(r, 100008 - 5000, in + 2 * 100, 100);
    v = nv_ring_read(r, 100000, out, 8);
    CHECK(v == 8 && memcmp(out, in, sizeof(float) * 16) == 0, "too-old write dropped");
    /* non-finite input sanitized */
    float bad[2] = {NAN, INFINITY};
    nv_ring_write(r, 100008, bad, 1);
    nv_ring_read(r, 100008, out, 1);
    CHECK(out[0] == 0 && out[1] == 0, "non-finite sanitized");
    nv_ring_invalidate(r);
    CHECK(nv_ring_read(r, 100000, out, 8) == 0 && out[0] == 0, "invalidate -> silence");
    nv_ring_write(r, 200000, in, 8);
    CHECK(nv_ring_read(r, 200000, out, 8) == 8 && out[0] == in[0], "write after invalidate");
    nv_ring_destroy(r);
}

static nv_ring *shared_ring;
static atomic_llong ring_end;
static atomic_int ring_stop;
static atomic_llong torn, checked_valid, muted;
static void *ring_writer(void *arg) {
    (void)arg; float buf[512];
    int64_t t = 0; unsigned seed = 3;
    while (t < 3000000) {
        seed = seed * 1103515245u + 12345u;
        uint32_t n = 64 + seed % 448;
        if (seed % 97 == 0) t += seed % 6000; /* occasional gap (writer pause) */
        for (uint32_t i = 0; i < n; i++) buf[i] = fval(t + i);
        nv_ring_write(shared_ring, t, buf, n);
        t += n;
        atomic_store(&ring_end, t);
    }
    atomic_store(&ring_stop, 1);
    return NULL;
}
static void *ring_reader(void *arg) {
    unsigned seed = (unsigned)(uintptr_t)arg; float buf[512];
    while (!atomic_load(&ring_stop)) {
        seed = seed * 1103515245u + 12345u;
        int64_t e = atomic_load(&ring_end);
        int64_t t = e - (int64_t)(seed % 9000) + 300;
        uint32_t n = 32 + seed % 480;
        nv_ring_read(shared_ring, t, buf, n);
        for (uint32_t i = 0; i < n; i++) {
            if (buf[i] == 0) { atomic_fetch_add(&muted, 1); continue; }
            if (buf[i] != fval(t + i)) atomic_fetch_add(&torn, 1);
            else atomic_fetch_add(&checked_valid, 1);
        }
    }
    return NULL;
}
static void test_ring_threads(void) {
    shared_ring = nv_ring_create(4096, 1);
    pthread_t w, r1, r2;
    pthread_create(&r1, NULL, ring_reader, (void *)11);
    pthread_create(&r2, NULL, ring_reader, (void *)29);
    pthread_create(&w, NULL, ring_writer, NULL);
    pthread_join(w, NULL); pthread_join(r1, NULL); pthread_join(r2, NULL);
    CHECK(atomic_load(&torn) == 0, "torn/stale frames observed: %lld", (long long)atomic_load(&torn));
    CHECK(atomic_load(&checked_valid) > 10000, "too few verified frames: %lld", (long long)atomic_load(&checked_valid));
    nv_ring_destroy(shared_ring);
}

/* ---------------- fifo ---------------- */
static void test_fifo(void) {
    CHECK(!nv_fifo_create(100), "reject non pow2");
    nv_fifo *f = nv_fifo_create(64);
    float a[100], b[100];
    for (int i = 0; i < 100; i++) a[i] = (float)i;
    CHECK(nv_fifo_write(f, a, 100) == 64 && nv_fifo_available(f) == 64, "bounded write");
    CHECK(nv_fifo_peek(f, b, 10) == 10 && b[9] == 9 && nv_fifo_available(f) == 64, "peek no consume");
    nv_fifo_consume(f, 60);
    CHECK(nv_fifo_write(f, a + 64, 30) == 30 && nv_fifo_peek(f, b, 100) == 34 && b[0] == 60 && b[33] == 93, "wraparound order");
    nv_fifo_consume(f, 1000);
    CHECK(nv_fifo_available(f) == 0, "consume clamps");
    nv_fifo_destroy(f);
}

/* ---------------- bridge simulation ---------------- */
typedef struct {
    const char *name;
    uint32_t in_rate, in_ch, bi, out_rate, out_ch, bo, target;
    double ppm_in, ppm_out, seconds;
    float strength;
    int noise; /* 0: 440 Hz sine; 1: white noise (denoise check) */
} sim_case;

typedef struct {
    double max_residual_db, effective_ppm, final_ppm, expected_ppm, latency_ms, fill_min, fill_max, reduction_db;
    unsigned long long underruns_after_prime, overflows, allocations, glitches;
} sim_result;

static double lcg_noise(unsigned *s) { *s = *s * 1664525u + 1013904223u; return ((double)(*s >> 8) / 16777216.0) * 2 - 1; }

static double mb_of(const sim_case *k) { double a = (double)k->bi * NV_INTERNAL_RATE / k->in_rate, b2 = k->bo; return a > b2 ? a : b2; }

static int run_sim(const sim_case *k, sim_result *res) {
    memset(res, 0, sizeof *res);
    uint32_t mb = k->bi > k->bo ? k->bi : k->bo;
    nv_bridge_config cfg = {k->in_rate, k->in_ch, k->out_rate, k->out_ch, mb, k->target, k->strength, 2000};
    nv_bridge *b = nv_bridge_create(&cfg);
    if (!b) { CHECK(0, "%s: create failed", k->name); return -1; }
    const double fin = k->in_rate * (1 + k->ppm_in * 1e-6), fout = k->out_rate * (1 + k->ppm_out * 1e-6);
    const double f0 = 440.0;
    size_t total_out = (size_t)(k->seconds * fout) + k->bo;
    float *rec = calloc(total_out, sizeof(float));
    float *inb = calloc((size_t)k->bi * k->in_ch, sizeof(float));
    float *outb = calloc((size_t)k->bo * k->out_ch, sizeof(float));
    uint64_t in_k = 0, out_k = 0; unsigned seed = 1;
    double in_sq = 0, in_n = 0;
    double t_in = 0, t_out = 0;
    nv_bridge_stats st, snap; int primed_seen = 0, snapped = 0; uint64_t under_at_prime = 0;
    const double settle = k->seconds > 90 ? 60 : k->seconds / 2;
    res->fill_min = 1e9; res->fill_max = -1;
    while (out_k + k->bo <= total_out) {
        if (t_in <= t_out) {
            for (uint32_t i = 0; i < k->bi; i++) {
                double tr = (double)(in_k + i) / fin, x;
                if (k->noise) x = 0.05 * lcg_noise(&seed);
                else x = 0.5 * sin(2 * M_PI * f0 * tr);
                if (tr > k->seconds - 10) { in_sq += x * x; in_n++; }
                for (uint32_t c = 0; c < k->in_ch; c++) inb[i * k->in_ch + c] = (float)x;
            }
            TRACK(1); int rc = nv_bridge_push(b, inb, k->bi, (uint64_t)llround(t_in * 1e9) + 1); TRACK(0);
            CHECK(rc == NV_OK, "push rc %d", rc);
            in_k += k->bi; t_in = (double)in_k / fin;
        } else {
            TRACK(1); nv_bridge_pull(b, outb, k->bo, (uint64_t)llround(t_out * 1e9) + 1); TRACK(0);
            for (uint32_t i = 0; i < k->bo; i++) {
                rec[out_k + i] = outb[i * k->out_ch];
                for (uint32_t c = 1; c < k->out_ch; c++)
                    if (outb[i * k->out_ch + c] != outb[i * k->out_ch]) { CHECK(0, "channel mismatch"); break; }
            }
            out_k += k->bo; t_out = (double)out_k / fout;
            nv_bridge_get_stats(b, &st);
            if (st.primed && !primed_seen) { primed_seen = 1; under_at_prime = st.underruns; }
            if (t_out > settle) {
                if (!snapped) { snap = st; snapped = 1; }
                if (st.fill < res->fill_min) res->fill_min = (double)st.fill;
                if (st.fill > res->fill_max) res->fill_max = (double)st.fill;
            }
        }
    }
    nv_bridge_get_stats(b, &st);
    res->underruns_after_prime = st.underruns - under_at_prime;
    res->overflows = st.overflows;
    res->final_ppm = st.correction_ppm;
    /* effective consumption ratio over the settled half, from frame counters */
    if (snapped && st.pulled > snap.pulled) {
        double nominal = (double)(st.pulled - snap.pulled) * NV_INTERNAL_RATE / k->out_rate;
        res->effective_ppm = ((double)(st.consumed - snap.consumed) / nominal - 1) * 1e6;
    }
    /* consumption 48k(1+po)(1+c) == production 48k(1+pi) */
    res->expected_ppm = ((1 + k->ppm_in * 1e-6) / (1 + k->ppm_out * 1e-6) - 1) * 1e6;
#ifdef WRAP_ALLOC
    res->allocations = (unsigned long long)atomic_load(&allocations);
#endif
    const size_t W = 1200; /* 25 ms windows: tolerant to slow ratio steering */
    if (!k->noise) {
        /* Per-window LS fit of the known real-time sinusoid: residual detects
         * glitches, drops, repeats or resampler artifacts. */
        double worst = -300, lat_sum = 0; int lat_n = 0;
        size_t start = (size_t)(fout * settle);
        /* glitch detector: a dropped/repeated/zeroed sample makes the second
         * difference jump far above the 440 Hz bound (0.5*(2*pi*440/fs)^2). */
        for (size_t i = start + 2; i < out_k; i++)
            if (fabs((double)rec[i] - 2.0 * rec[i - 1] + rec[i - 2]) > 0.01) res->glitches++;
        for (size_t w0 = start; w0 + W <= out_k; w0 += W) {
            double ss = 0, sc = 0, cc = 0, ys = 0, yc = 0, yy = 0;
            for (size_t i = w0; i < w0 + W; i++) {
                double tr = (double)i / fout, s = sin(2 * M_PI * f0 * tr), c = cos(2 * M_PI * f0 * tr), y = rec[i];
                ss += s * s; sc += s * c; cc += c * c; ys += y * s; yc += y * c; yy += y * y;
            }
            double det = ss * cc - sc * sc, A = (ys * cc - yc * sc) / det, B = (yc * ss - ys * sc) / det;
            double resid = 0;
            for (size_t i = w0; i < w0 + W; i++) {
                double tr = (double)i / fout, e = rec[i] - (A * sin(2 * M_PI * f0 * tr) + B * cos(2 * M_PI * f0 * tr));
                resid += e * e;
            }
            double db = 10 * log10((resid + 1e-20) / (yy + 1e-20));
            if (db > worst) worst = db;
            /* y = R sin(w t - phi) => phi = atan2(-B, A); latency = phi/w (mod period) */
            double phi = atan2(-B, A); if (phi < 0) phi += 2 * M_PI;
            lat_sum += phi / (2 * M_PI * f0); lat_n++;
        }
        res->max_residual_db = worst;
        res->latency_ms = lat_n ? 1000 * lat_sum / lat_n : -1; /* modulo 1/440 s */
    } else {
        double out_sq = 0, n = 0;
        for (size_t i = out_k - (size_t)(10 * fout); i < out_k; i++) out_sq += (double)rec[i] * rec[i], n++;
        res->reduction_db = 10 * log10((in_sq / in_n) / (out_sq / n + 1e-20));
    }
    free(rec); free(inb); free(outb);
    nv_bridge_destroy(b);
    return 0;
}

/* Nonstationary signal with denoise fully active; muting must fail this test. */
static void test_denoise_signal(int mute_output) {
    nv_bridge_config cfg = {48000, 1, 48000, 1, 480, 1440, 1.f, 2000};
    nv_bridge *b = nv_bridge_create(&cfg);
    const size_t cap = 48000 * 4;
    float *recording = calloc(cap, sizeof *recording);
    float in[480], out[480]; unsigned seed = 19; size_t recorded = 0;
    CHECK(b && recording, "denoise signal allocation");
    if (!b || !recording) { nv_bridge_destroy(b); free(recording); return; }
    for (unsigned block = 0; block < 1000; block++) {
        for (unsigned i = 0; i < 480; i++) {
            double t = (block * 480.0 + i) / 48000;
            double signal = block < 200 ? 0 : 0.25 * (0.6 + 0.4 * sin(2 * M_PI * 1.3 * t)) * sin(2 * M_PI * 1000 * t);
            in[i] = (float)(signal + (block < 200 ? 0.05 : 0.001) * lcg_noise(&seed));
        }
        TRACK(1);
        nv_bridge_push(b, in, 480, (uint64_t)block * 10000000 + 1);
        nv_bridge_pull(b, out, 480, (uint64_t)block * 10000000 + 5000001);
        TRACK(0);
        if (mute_output) memset(out, 0, sizeof out); /* explicit test mutation */
        if (block >= 600) { memcpy(recording + recorded, out, sizeof out); recorded += 480; }
    }
    double gain = 0, coherence = 0, reference = 0.25 * sqrt((0.6 * 0.6 + 0.4 * 0.4 / 2) / 2);
    int preserved = nv_signal_preserved(recording, recorded, 48000, 1000, reference, &gain, &coherence);
    CHECK(preserved, "denoise signal lost: gain %.2f dB coherence %.3f", gain, coherence);
    fprintf(stderr, "denoise-preservation gain_db=%.2f coherence=%.3f mute_mutation=%d\n", gain, coherence, mute_output);
    memset(recording, 0, cap * sizeof *recording);
    CHECK(!nv_signal_preserved(recording, cap, 48000, 1000, reference, &gain, &coherence), "oracle accepted complete silence");
    free(recording); nv_bridge_destroy(b);
}

static void test_endpoint_watch(void) {
    nv_endpoint_state a = {42, 1, 2, 480, 48000, 123}, b = a;
    CHECK(!nv_endpoint_changed(&a, &b), "unchanged endpoint must not restart");
    b.id++; CHECK(nv_endpoint_changed(&a, &b), "redetect replacement virtual ID"); b = a;
    b.rate = 44100; CHECK(nv_endpoint_changed(&a, &b), "redetect nominal rate"); b = a;
    b.channels = 1; CHECK(nv_endpoint_changed(&a, &b), "redetect channel count"); b = a;
    b.buffer = 512; CHECK(nv_endpoint_changed(&a, &b), "redetect buffer size"); b = a;
    b.format_hash++; CHECK(nv_endpoint_changed(&a, &b), "redetect physical stream format"); b = a;
    b.alive = 0; CHECK(nv_endpoint_changed(&a, &b), "redetect dead endpoint");
}

/* Impulse latency through the whole bridge (strength 0 = delayed dry). */
static double impulse_latency_ms(uint32_t target, uint32_t block) {
    nv_bridge_config cfg = {48000, 1, 48000, 1, block, target, 0.f, 2000};
    nv_bridge *b = nv_bridge_create(&cfg);
    float in[4096] = {0}, out[4096];
    long found = -1; long pushed = 0, pulled = 0, impulse_at = -1;
    for (int cycle = 0; cycle < 2000 && found < 0; cycle++) {
        memset(in, 0, sizeof(float) * block);
        if (cycle == 500) { in[0] = 0.9f; impulse_at = pushed; }
        nv_bridge_push(b, in, block, 0); pushed += block;
        nv_bridge_pull(b, out, block, 0);
        for (uint32_t i = 0; i < block; i++) if (impulse_at >= 0 && found < 0 && fabsf(out[i]) > 0.3f) found = pulled + i;
        pulled += block;
    }
    nv_bridge_destroy(b);
    return found < 0 ? -1 : 1000.0 * (double)(found - impulse_at) / 48000;
}

/* Two real threads, wall-clock paced, for TSan and scheduling realism. */
static nv_bridge *tb; static atomic_int tstop;
static void sleep_until(double ms) {
#ifdef __APPLE__ /* absolute Mach wait avoids relative nanosleep coalescing */
    double d = ms - ms_now();
    if (d <= 0) return;
    mach_timebase_info_data_t tb;
    mach_timebase_info(&tb);
    uint64_t deadline = mach_absolute_time() + (uint64_t)(d * 1e6 * tb.denom / tb.numer);
    mach_wait_until(deadline);
#else
    struct timespec ts; ts.tv_sec = (time_t)(ms / 1e3); ts.tv_nsec = (long)(fmod(ms, 1e3) * 1e6);
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
#endif
}
static void *tprod(void *a) {
    (void)a; float buf[480]; double t0 = ms_now(), period = 480.0 / (48000 * (1 + 300e-6)) * 1e3; long k = 0; unsigned s = 5;
    while (!atomic_load(&tstop)) {
        for (int i = 0; i < 480; i++) buf[i] = (float)(0.1 * lcg_noise(&s));
        nv_bridge_push(tb, buf, 480, (uint64_t)(ms_now() * 1e6)); k++;
        sleep_until(t0 + k * period);
    }
    return NULL;
}
static void *tcons(void *a) {
    (void)a; float buf[512 * 2]; double t0 = ms_now(), period = 512.0 / 48000 * 1e3; long k = 0;
    while (!atomic_load(&tstop)) {
        nv_bridge_pull(tb, buf, 512, (uint64_t)(ms_now() * 1e6)); k++;
        sleep_until(t0 + k * period);
    }
    return NULL;
}
static void test_bridge_threads(unsigned long long *underruns, unsigned long long *overflows) {
    nv_bridge_config cfg = {48000, 1, 48000, 2, 512, 2400, 1.f, 2000};
    tb = nv_bridge_create(&cfg);
    pthread_t p, c;
    pthread_create(&p, NULL, tprod, NULL); pthread_create(&c, NULL, tcons, NULL);
    for (int i = 0; i < 6; i++) { nv_bridge_set_strength(tb, i % 2 ? 0.5f : 1.f); sleep_until(ms_now() + 500); }
    atomic_store(&tstop, 1);
    pthread_join(p, NULL); pthread_join(c, NULL);
    nv_bridge_stats st; nv_bridge_get_stats(tb, &st);
    *underruns = st.underruns; *overflows = st.overflows;
    nv_bridge_destroy(tb);
}

int main(int argc, char **argv) {
    int denoise_only = argc > 1 && strcmp(argv[1], "--denoise-only") == 0;
    int mute_output = argc > 2 && strcmp(argv[2], "--mute-denoise-output") == 0;
    test_endpoint_watch(); test_denoise_signal(mute_output);
    if (denoise_only) {
        printf("{\"ok\":%s,\"failures\":%d,\"muted_output\":%s}\n", failures ? "false" : "true", failures, mute_output ? "true" : "false");
        return failures ? 1 : 0;
    }
    int quick = argc > 1 && strcmp(argv[1], "--quick") == 0; /* sanitizer builds */
    double t0 = ms_now();
    test_clock(); test_ring_basic(); test_fifo(); test_ring_threads();
    nv_bridge_config bad = {48000, 1, 48000, 1, 512, 10, 1.f, 2000};
    CHECK(!nv_bridge_create(&bad), "reject tiny target");
    double S = quick ? 40 : 600;
    sim_case cases[] = {
        {"48k->48k drift +300/-300ppm", 48000, 1, 480, 48000, 2, 512, 1440, 300, -300, S, 0.f, 0},
        {"44.1k mic->48k feed, -150ppm", 44100, 2, 441, 48000, 1, 512, 1440, -150, 0, S, 0.f, 0},
        {"48k->44.1k speakers, +500ppm", 48000, 2, 512, 44100, 2, 441, 1440, 500, 0, S, 0.f, 0},
        {"16k BT headset ->48k, +80ppm", 16000, 1, 160, 48000, 1, 480, 1440, 80, 0, S, 0.f, 0},
        {"48k->48k drift 1500ppm, 256 blocks", 48000, 1, 256, 48000, 1, 256, 960, 1500, 0, S, 0.f, 0},
        {"48k->48k equal 480 blocks, +40ppm (slow beat)", 48000, 1, 480, 48000, 1, 480, 1440, 40, 0, S, 0.f, 0},
        {"denoise white noise -26dBFS", 48000, 1, 480, 48000, 1, 480, 1440, 0, 0, quick ? 20 : 60, 1.f, 1},
    };
    const int ncases = sizeof cases / sizeof cases[0];
    char json[16384]; size_t pos = 0;
    pos += snprintf(json + pos, sizeof json - pos, "{\"cases\":[");
    for (int i = 0; i < ncases; i++) {
        sim_result r; run_sim(&cases[i], &r);
        const sim_case *k = &cases[i];
        CHECK(r.underruns_after_prime == 0, "%s: underruns %llu", k->name, r.underruns_after_prime);
        CHECK(r.overflows == 0, "%s: overflows %llu", k->name, r.overflows);
#ifdef WRAP_ALLOC
        CHECK(r.allocations == 0, "%s: allocations in RT path: %llu", k->name, r.allocations);
#endif
        if (!k->noise) {
            CHECK(r.max_residual_db < (quick ? -50 : -60), /* quick runs measure during PI settling */ "%s: residual %.1f dB", k->name, r.max_residual_db);
            CHECK(r.glitches == 0, "%s: %llu glitches", k->name, r.glitches);
            if (!quick) CHECK(fabs(r.effective_ppm - r.expected_ppm) < 5 + 1e6 * 2.0 * mb_of(k) / (k->seconds / 2 * NV_INTERNAL_RATE), "%s: effective ppm %.2f vs %.2f", k->name, r.effective_ppm, r.expected_ppm);
            CHECK(r.fill_max - r.fill_min < 3.0 * k->target, "%s: fill span %.0f..%.0f", k->name, r.fill_min, r.fill_max);
            CHECK(r.fill_min > 0, "%s: fill hit zero", k->name);
        } else {
            CHECK(r.reduction_db > 6 && r.reduction_db < 40, "%s: invalid noise reduction %.1f dB (mute is not denoising)", k->name, r.reduction_db);
        }
        pos += snprintf(json + pos, sizeof json - pos,
            "%s{\"name\":\"%s\",\"seconds\":%.0f,\"underruns_after_prime\":%llu,\"overflows\":%llu,"
            "\"rt_allocations\":%llu,\"glitches\":%llu,\"max_residual_db\":%.1f,\"effective_ppm\":%.2f,\"final_ppm\":%.2f,\"expected_ppm\":%.2f,"
            "\"fill_min\":%.0f,\"fill_max\":%.0f,\"target\":%u,\"noise_reduction_db\":%.1f}",
            i ? "," : "", k->name, k->seconds, r.underruns_after_prime, r.overflows, r.allocations, r.glitches,
            r.max_residual_db, r.effective_ppm, r.final_ppm, r.expected_ppm, r.fill_min, r.fill_max, k->target, r.reduction_db);
    }
    double lat = impulse_latency_ms(960, 480);
    nv_bridge_config lc = {48000, 1, 48000, 1, 480, 960, 0.f, 2000};
    nv_bridge *lb = nv_bridge_create(&lc);
    double nominal = nv_bridge_nominal_latency_ms(lb);
    nv_bridge_destroy(lb);
    CHECK(lat > 0 && lat < 60, "impulse latency %.2f ms", lat);
    unsigned long long tu = 0, to = 0;
    test_bridge_threads(&tu, &to);
    CHECK(to == 0, "threaded overflows %llu", to);
    if (!quick) CHECK(tu <= 1, "threaded underruns %llu", tu);
    pos += snprintf(json + pos, sizeof json - pos,
        "],\"impulse_latency_ms\":%.2f,\"nominal_latency_ms\":%.2f,\"ring_verified_frames\":%lld,"
        "\"ring_muted_frames\":%lld,\"ring_torn\":%lld,\"threaded_underruns\":%llu,\"threaded_overflows\":%llu,"
        "\"quick\":%s,\"wall_ms\":%.0f,\"failures\":%d,\"ok\":%s}",
        lat, nominal, (long long)atomic_load(&checked_valid), (long long)atomic_load(&muted),
        (long long)atomic_load(&torn), tu, to, quick ? "true" : "false", ms_now() - t0, failures,
        failures ? "false" : "true");
    puts(json);
    return failures ? 1 : 0;
}
