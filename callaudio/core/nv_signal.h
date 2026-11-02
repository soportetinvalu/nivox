/* SPDX-License-Identifier: MIT
 * Shared signal-preservation oracle: a muted denoiser must never pass.
 * A modulated tone need not be bit-exact, but must retain energy and its carrier.
 */
#ifndef NV_SIGNAL_H
#define NV_SIGNAL_H
#include <math.h>
#include <stddef.h>
static inline int nv_signal_preserved(const float *x, size_t n, double rate,
                                     double hz, double reference_rms,
                                     double *gain_db, double *coherence) {
    double sq = 0, s = 0, c = 0;
    for (size_t i = 0; i < n; i++) {
        double p = 6.283185307179586 * hz * i / rate;
        if (!isfinite(x[i])) return 0;
        sq += (double)x[i] * x[i];
        s += x[i] * sin(p); c += x[i] * cos(p);
    }
    *gain_db = 20 * log10((n ? sqrt(sq / n) : 0) / reference_rms + 1e-20);
    *coherence = sq > 0 && n ? 2 * (s * s + c * c) / (n * sq) : 0;
    return n >= (size_t)rate && *gain_db > -18 && *gain_db < 3 && *coherence > 0.35;
}
#endif
