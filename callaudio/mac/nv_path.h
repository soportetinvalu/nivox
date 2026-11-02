/* SPDX-License-Identifier: MIT
 * One real-time audio path: capture device -> nv_bridge (denoise + drift
 * compensation) -> playback device. Used by the CLI engine and the E2E test.
 */
#ifndef NV_PATH_H
#define NV_PATH_H
#include "nv_core.h"
#include "nv_hal.h"

typedef struct nv_path {
    char name[32];
    AudioObjectID in_dev, out_dev;
    nv_io *in, *out;
    nv_bridge *bridge;
    uint32_t in_buffer, out_buffer, target_fill;
} nv_path;

typedef struct nv_path_options {
    float strength;      /* 0..1 */
    uint32_t io_buffer;  /* requested device buffer (frames), e.g. 480 */
    double min_target_ms;/* lower bound for FIFO target, e.g. 10 */
} nv_path_options;

/* Opens, but does not start. On error returns -1 and writes a message. */
int nv_path_open(nv_path *p, const char *name, AudioObjectID in_dev, uint32_t in_ch,
                 AudioObjectID out_dev, uint32_t out_ch, const nv_path_options *o,
                 char *err, size_t errlen);
int nv_path_start(nv_path *p, char *err, size_t errlen);
void nv_path_close(nv_path *p);
void nv_path_stats_json(const nv_path *p, FILE *out);

#endif
