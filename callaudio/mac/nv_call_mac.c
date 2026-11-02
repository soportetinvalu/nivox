/* SPDX-License-Identifier: MIT
 * App backend: the same AUHAL + drift-compensated DSP paths as the CLI.
 * Never called from an audio callback. Does not change system defaults/rates.
 */
#include "nv_call.h"
#include "nv_path.h"
#include <stdlib.h>
#include <stdatomic.h>
#include <pthread.h>

/* Process-lifetime callback context: a late HAL notification cannot touch a
 * freed route. Registration/refcount changes are non-RT and mutex serialized. */
static _Atomic uint64_t topology_generation;
static pthread_mutex_t watch_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned watch_users;
static int watching[2];
static const AudioObjectPropertyAddress watches[] = {
    {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain},
    {kAudioHardwarePropertyServiceRestarted, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain},
};
static OSStatus topology_changed(AudioObjectID object, UInt32 count,
                                 const AudioObjectPropertyAddress *addresses, void *context) {
    (void)object; (void)count; (void)addresses; (void)context;
    atomic_fetch_add_explicit(&topology_generation, 1, memory_order_relaxed);
    return noErr;
}
static void remove_watches(void) {
    for (int i = 0; i < 2; i++)
        if (watching[i] && AudioObjectRemovePropertyListener(kAudioObjectSystemObject, &watches[i], topology_changed, NULL) == noErr)
            watching[i] = 0;
}
static int acquire_watches(char *err, size_t n) {
    pthread_mutex_lock(&watch_lock);
    for (int i = 0; i < 2; i++) if (!watching[i]) {
        OSStatus st = AudioObjectAddPropertyListener(kAudioObjectSystemObject, &watches[i], topology_changed, NULL);
        if (st != noErr) {
            if (!watch_users) remove_watches();
            pthread_mutex_unlock(&watch_lock);
            snprintf(err, n, "CoreAudio device/restart listener registration failed: OSStatus %d", (int)st);
            return -1;
        }
        watching[i] = 1;
    }
    watch_users++;
    pthread_mutex_unlock(&watch_lock);
    return 0;
}
static void release_watches(void) {
    pthread_mutex_lock(&watch_lock);
    if (--watch_users == 0) remove_watches();
    pthread_mutex_unlock(&watch_lock);
}
typedef struct {
    nv_path path;
    char physical_uid[256];
    int speaker;
    nv_endpoint_state input, output;
    uint64_t generation;
} call_route;
static int route_valid(void *ctx, void *route) {
    (void)ctx;
    call_route *r = route;
    AudioObjectID physical = nv_device_for_uid(r->physical_uid);
    AudioObjectID in = r->speaker ? nv_device_for_uid(NV_UID_SPEAKER_RETURN) : physical;
    AudioObjectID out = r->speaker ? physical : nv_device_for_uid(NV_UID_MIC_FEED);
    nv_endpoint_state input = nv_device_state(in, 1), output = nv_device_state(out, 0);
    return r->generation == atomic_load_explicit(&topology_generation, memory_order_relaxed) &&
           !nv_endpoint_changed(&r->input, &input) && !nv_endpoint_changed(&r->output, &output);
}

static int list(void *ctx, nv_call_device **out, size_t *count) {
    (void)ctx;
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal,
                                   kAudioObjectPropertyElementMain};
    UInt32 size = 0;
    *out = NULL; *count = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) != noErr) return -1;
    if (!size) return 0;
    AudioObjectID *ids = malloc(size);
    if (!ids) return -1;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, ids) != noErr) {
        free(ids); return -1;
    }
    nv_call_device *devices = calloc(size / sizeof *ids, sizeof *devices);
    if (!devices) { free(ids); return -1; }
    for (size_t i = 0; i < size / sizeof *ids; i++) {
        nv_call_device *d = &devices[(*count)++];
        if (nv_device_uid(ids[i], d->uid, sizeof d->uid) || nv_device_name(ids[i], d->name, sizeof d->name)) {
            (*count)--; continue;
        }
        d->inputs = nv_device_channels(ids[i], 1); d->outputs = nv_device_channels(ids[i], 0);
        d->rate = nv_device_rate(ids[i]); d->alive = nv_device_alive(ids[i]);
        UInt32 transport = 0, n = sizeof transport;
        AudioObjectPropertyAddress t = {kAudioDevicePropertyTransportType, kAudioObjectPropertyScopeGlobal,
                                       kAudioObjectPropertyElementMain};
        if (AudioObjectGetPropertyData(ids[i], &t, 0, NULL, &n, &transport) == noErr)
            d->physical = transport != kAudioDeviceTransportTypeVirtual &&
                          transport != kAudioDeviceTransportTypeAggregate &&
                          transport != kAudioDeviceTransportTypeAutoAggregate &&
                          transport != kAudioDeviceTransportTypeUnknown &&
                          !nv_device_hidden(ids[i]) && !nv_device_is_nivox(ids[i]);
    }
    free(ids); *out = devices;
    return 0;
}
static int driver_ready(void *ctx) {
    (void)ctx;
    const char *uids[] = {NV_UID_MICROPHONE, NV_UID_MIC_FEED, NV_UID_SPEAKER, NV_UID_SPEAKER_RETURN};
    for (size_t i = 0; i < sizeof uids / sizeof *uids; i++) {
        AudioObjectID d = nv_device_for_uid(uids[i]);
        if (!nv_device_alive(d) || nv_device_rate(d) != 48000) return 0;
    }
    return 1;
}
static void *open_route(void *ctx, int speaker, const char *uid, char *err, size_t n) {
    (void)ctx;
    call_route *r = calloc(1, sizeof *r);
    if (!r) { snprintf(err, n, "Out of memory"); return NULL; }
    if (acquire_watches(err, n)) { free(r); return NULL; }
    r->speaker = speaker;
    snprintf(r->physical_uid, sizeof r->physical_uid, "%s", uid);
    r->generation = atomic_load_explicit(&topology_generation, memory_order_relaxed);
    AudioObjectID physical = nv_device_for_uid(uid);
    AudioObjectID in = speaker ? nv_device_for_uid(NV_UID_SPEAKER_RETURN) : physical;
    AudioObjectID out = speaker ? physical : nv_device_for_uid(NV_UID_MIC_FEED);
    uint32_t channels = speaker && nv_device_channels(out, 0) >= 2 ? 2 : 1;
    /* Respect the hardware's existing IO buffer size, unlike the CLI's optional
     * best-effort buffer request. Strength 1 uses the existing -15 dB preset. */
    nv_path_options options = {1.f, 0, 10};
    r->input = nv_device_state(in, 1); r->output = nv_device_state(out, 0);
    if (nv_path_open(&r->path, speaker ? "speaker" : "mic", in, speaker ? 2 : 1, out, channels,
                     &options, err, n) || nv_path_start(&r->path, err, n)) {
        nv_path_close(&r->path); release_watches(); free(r); return NULL;
    }
    if (!route_valid(NULL, r)) {
        snprintf(err, n, "CoreAudio topology or endpoint changed while opening route");
        nv_path_close(&r->path); release_watches(); free(r); return NULL;
    }
    return r;
}
static void close_route(void *ctx, void *route) {
    (void)ctx;
    call_route *r = route;
    nv_path_close(&r->path); release_watches(); free(r);
}
static void stats(void *ctx, void *route, nv_call_stats *out) {
    (void)ctx;
    nv_bridge_stats s = {0};
    nv_bridge_get_stats(((call_route *)route)->path.bridge, &s);
    *out = (nv_call_stats){s.pushed, s.pulled, s.underruns, s.overflows, s.rejected_samples, s.primed};
}
nv_call *nv_call_create_mac(void) {
    const nv_call_backend b = {list, driver_ready, open_route, close_route, stats, route_valid};
    return nv_call_create(&b, NULL);
}
