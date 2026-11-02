/* SPDX-License-Identifier: MIT */
#ifndef NV_CALL_H
#define NV_CALL_H
#include <stddef.h>
#include <stdint.h>
#include "nv_call_ui.h"

/* Control-plane only: serialize ALL calls on one non-RT queue. No auto-start,
 * default-device changes, recording, or silent substitution of physical UIDs. */
typedef struct nv_call nv_call;
enum { NV_CALL_STOPPED, NV_CALL_STARTING, NV_CALL_RUNNING, NV_CALL_ERROR };
typedef struct {
    char uid[256], name[256];
    uint32_t inputs, outputs;
    double rate;
    int physical, alive;
} nv_call_device;
typedef struct {
    uint64_t pushed, pulled, underruns, overflows, rejected;
    int primed;
} nv_call_stats;

/* Backend seam: macOS uses real HAL/nv_path. Tests inject deterministic I/O;
 * those tests do not claim to exercise CoreAudio or microphone permissions. */
typedef struct {
    int (*list)(void *, nv_call_device **, size_t *); /* malloc-owned array */
    int (*driver_ready)(void *);
    void *(*open)(void *, int speaker, const char *physical_uid, char *, size_t);
    void (*close)(void *, void *);
    void (*stats)(void *, void *, nv_call_stats *);
    int (*route_valid)(void *, void *); /* required: topology/endpoint snapshot still current */
} nv_call_backend;
nv_call *nv_call_create(const nv_call_backend *, void *context);
nv_call *nv_call_create_mac(void);
void nv_call_destroy(nv_call *);
int nv_call_refresh(nv_call *);
size_t nv_call_device_count(const nv_call *);
const nv_call_device *nv_call_device_at(const nv_call *, size_t);
/* Both routes are required. Success means STARTING, not proven audio delivery.
 * now is monotonic seconds. poll confirms callbacks and detects stalled IO. */
int nv_call_start(nv_call *, const char *mic_uid, const char *out_uid, double now);
void nv_call_stop(nv_call *);
void nv_call_poll(nv_call *, double now);
int nv_call_state(const nv_call *);
int nv_call_driver_ready(const nv_call *);
const char *nv_call_detail(const nv_call *);
uint64_t nv_call_underruns(const nv_call *);
const char *nv_call_device_uid(const nv_call_device *);
const char *nv_call_device_name(const nv_call_device *);
#endif
