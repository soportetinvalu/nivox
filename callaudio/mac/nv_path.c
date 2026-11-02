/* SPDX-License-Identifier: MIT */
#include "nv_path.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static uint32_t buffer_frames(AudioObjectID dev) {
    UInt32 v = 0, size = sizeof v;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyBufferFrameSize, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &v) == noErr ? v : 0;
}

static void on_input(void *ctx, float *data, uint32_t frames, uint32_t ch, const AudioTimeStamp *ts) {
    (void)ch;
    nv_path *p = ctx;
    uint64_t ns = (ts && (ts->mFlags & kAudioTimeStampHostTimeValid)) ? nv_host_to_ns(ts->mHostTime) : 0;
    nv_bridge_push(p->bridge, data, frames, ns);
}

static void on_output(void *ctx, float *data, uint32_t frames, uint32_t ch, const AudioTimeStamp *ts) {
    (void)ch;
    nv_path *p = ctx;
    uint64_t ns = (ts && (ts->mFlags & kAudioTimeStampHostTimeValid)) ? nv_host_to_ns(ts->mHostTime) : 0;
    nv_bridge_pull(p->bridge, data, frames, ns);
}

int nv_path_open(nv_path *p, const char *name, AudioObjectID in_dev, uint32_t in_ch,
                 AudioObjectID out_dev, uint32_t out_ch, const nv_path_options *o,
                 char *err, size_t errlen) {
    memset(p, 0, sizeof *p);
    snprintf(p->name, sizeof p->name, "%s", name);
    p->in_dev = in_dev; p->out_dev = out_dev;
    if (in_dev == kAudioObjectUnknown || out_dev == kAudioObjectUnknown) {
        snprintf(err, errlen, "%s: device not found", name);
        return -1;
    }
    if (in_dev == out_dev) {
        snprintf(err, errlen, "%s: input and output are the same device (feedback loop)", name);
        return -1;
    }
    if (o->io_buffer) {
        nv_device_set_buffer_frames(in_dev, o->io_buffer);   /* best effort; HAL may keep larger */
        nv_device_set_buffer_frames(out_dev, o->io_buffer);
    }
    p->in_buffer = buffer_frames(in_dev);
    p->out_buffer = buffer_frames(out_dev);
    double in_rate = nv_device_rate(in_dev), out_rate = nv_device_rate(out_dev);
    if (in_rate < 8000 || out_rate < 8000 || !p->in_buffer || !p->out_buffer) {
        snprintf(err, errlen, "%s: unusable device format (rates %.0f/%.0f, buffers %u/%u)", name,
                 in_rate, out_rate, p->in_buffer, p->out_buffer);
        return -1;
    }
    if (fabs(in_rate - round(in_rate)) > 1e-6 || fabs(out_rate - round(out_rate)) > 1e-6) {
        snprintf(err, errlen, "%s: fractional nominal rate not supported", name);
        return -1;
    }
    /* Target: both callback periods (at 48 kHz) + half a Speex frame of margin. */
    double in48 = p->in_buffer * NV_INTERNAL_RATE / in_rate, out48 = p->out_buffer * NV_INTERNAL_RATE / out_rate;
    double target = in48 + out48 + 240;
    if (target < o->min_target_ms * 48) target = o->min_target_ms * 48;
    p->target_fill = (uint32_t)ceil(target);
    OSStatus st = noErr;
    p->in = nv_io_open(in_dev, 1, in_ch, on_input, p, &st);
    if (!p->in) { snprintf(err, errlen, "%s: open input failed (OSStatus %d)", name, (int)st); nv_path_close(p); return -1; }
    p->out = nv_io_open(out_dev, 0, out_ch, on_output, p, &st);
    if (!p->out) { snprintf(err, errlen, "%s: open output failed (OSStatus %d)", name, (int)st); nv_path_close(p); return -1; }
    uint32_t mb = nv_io_max_frames(p->in) > nv_io_max_frames(p->out) ? nv_io_max_frames(p->in) : nv_io_max_frames(p->out);
    nv_bridge_config cfg = {(uint32_t)in_rate, in_ch, (uint32_t)out_rate, out_ch, mb, p->target_fill, o->strength, 2000};
    p->bridge = nv_bridge_create(&cfg);
    if (!p->bridge) { snprintf(err, errlen, "%s: bridge create failed", name); nv_path_close(p); return -1; }
    return 0;
}

int nv_path_start(nv_path *p, char *err, size_t errlen) {
    OSStatus st = nv_io_start(p->out);
    if (st == noErr) st = nv_io_start(p->in);
    if (st != noErr) {
        /* kAudioDevicePermissionsError / silence are typical TCC outcomes */
        snprintf(err, errlen, "%s: start failed (OSStatus %d)", p->name, (int)st);
        return -1;
    }
    return 0;
}

void nv_path_close(nv_path *p) {
    /* stop IO first: callbacks reference the bridge */
    nv_io_close(p->in); p->in = NULL;
    nv_io_close(p->out); p->out = NULL;
    nv_bridge_destroy(p->bridge); p->bridge = NULL;
}

void nv_path_stats_json(const nv_path *p, FILE *o) {
    nv_bridge_stats s = {0};
    if (p->bridge) nv_bridge_get_stats(p->bridge, &s);
    fprintf(o, "{\"path\":\"%s\",\"primed\":%d,\"pushed\":%llu,\"pulled\":%llu,\"underruns\":%llu,"
               "\"overflows\":%llu,\"fill\":%lld,\"target\":%u,\"correction_ppm\":%.1f,"
               "\"in_buffer\":%u,\"out_buffer\":%u,\"nominal_latency_ms\":%.1f}",
            p->name, s.primed, (unsigned long long)s.pushed, (unsigned long long)s.pulled,
            (unsigned long long)s.underruns, (unsigned long long)s.overflows, (long long)s.fill,
            p->target_fill, s.correction_ppm, p->in_buffer, p->out_buffer,
            p->bridge ? nv_bridge_nominal_latency_ms(p->bridge) : 0.0);
}
