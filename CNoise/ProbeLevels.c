/* SPDX-License-Identifier: MIT */
#include "NoiseProbe.h"
#include <math.h>
#include <string.h>

static double db(double energy) { return energy > 0 ? 10 * log10(energy) : -160; }
int np_prepare_ab(const float *raw, const float *wet, size_t count,
                  double gain_db, double mix, float *a, float *b, np_ab_levels *s) {
    if (!raw || !wet || !a || !b || !s || !count || count > NP_MAX_FRAMES ||
        !isfinite(gain_db) || gain_db < 0 || gain_db > 24 ||
        !isfinite(mix) || mix < 0 || mix > .25 ||
        a == raw || a == wet || b == raw || b == wet || a == b) return NP_INVALID;
    double energy = 0, dot = 0;
    size_t full_scale = 0;
    /* Validate the whole input before writing any output. */
    for (size_t i = 0; i < count; i++) {
        if (!isfinite(raw[i]) || !isfinite(wet[i]) || fabs(raw[i]) > 1 || fabs(wet[i]) > 1)
            return NP_INVALID;
        energy += (double)raw[i] * raw[i];
        dot += (double)raw[i] * wet[i];
        full_scale += fabs(raw[i]) >= 1;
    }
    memset(s, 0, sizeof(*s));
    s->bypassed = dot < 0 || energy == 0;
    if (s->bypassed) mix = 0;
    double peak = 0, filtered = 0;
    for (size_t i = 0; i < count; i++) {
        double v = (1 - mix) * raw[i] + mix * wet[i];
        filtered += v * v;
        peak = fmax(peak, fmax(fabs(raw[i]), fabs(v)));
    }
    /* Whole-take constant gain, never a time-varying AGC/limiter. */
    const double ceiling = pow(10, -1.0 / 20);
    double requested = pow(10, gain_db / 20);
    double gain = peak > 0 ? fmin(requested, ceiling / peak) : 1;
    s->gain_limited = gain < requested - 1e-12;
    s->applied_gain_db = 20 * log10(gain);
    s->raw_rms_dbfs = db(energy / count);
    s->filtered_rms_dbfs = db(filtered / count);
    s->peak_dbfs = db(peak * peak * gain * gain);
    s->full_scale_samples = full_scale;
    for (size_t i = 0; i < count; i++) {
        a[i] = (float)(raw[i] * gain);
        b[i] = (float)(((1 - mix) * raw[i] + mix * wet[i]) * gain);
    }
    return NP_OK;
}
