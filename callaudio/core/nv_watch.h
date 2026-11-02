/* SPDX-License-Identifier: MIT */
#ifndef NV_WATCH_H
#define NV_WATCH_H
#include <stdint.h>
typedef struct {
    uint32_t id, alive, channels, buffer;
    double rate;
    uint64_t format_hash;
} nv_endpoint_state;
static inline int nv_endpoint_changed(const nv_endpoint_state *a, const nv_endpoint_state *b) {
    return !b->id || !b->alive || a->id != b->id || a->rate != b->rate ||
           a->channels != b->channels || a->buffer != b->buffer || a->format_hash != b->format_hash;
}
#endif
