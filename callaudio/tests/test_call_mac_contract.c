/* SPDX-License-Identifier: MIT
 * Production nv_call_mac.c with bounded, fake HAL/path functions. This tests
 * control decisions and listener cleanup on Linux, NOT Apple ABI, HAL or TCC.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "nv_core.h"
#include "nv_watch.h"
#define NV_PATH_H /* substitute only Apple's HAL/path boundary */
typedef uint32_t AudioObjectID;
typedef uint32_t UInt32;
typedef int32_t OSStatus;
typedef struct { UInt32 mSelector, mScope, mElement; } AudioObjectPropertyAddress;
enum { noErr = 0, kAudioObjectSystemObject = 100, kAudioObjectPropertyScopeGlobal = 0,
       kAudioObjectPropertyElementMain = 0, kAudioHardwarePropertyDevices = 1,
       kAudioHardwarePropertyServiceRestarted = 2, kAudioDevicePropertyTransportType = 3,
       kAudioDeviceTransportTypeVirtual = 10, kAudioDeviceTransportTypeAggregate = 11,
       kAudioDeviceTransportTypeAutoAggregate = 12, kAudioDeviceTransportTypeUnknown = 13 };
#define NV_UID_MICROPHONE "nivox.microphone.v1"
#define NV_UID_MIC_FEED "nivox.mic-feed.v1"
#define NV_UID_SPEAKER "nivox.speaker.v1"
#define NV_UID_SPEAKER_RETURN "nivox.speaker-return.v1"
typedef struct { nv_bridge *bridge; } nv_path;
typedef struct { float strength; uint32_t io_buffer; double min_target_ms; } nv_path_options;
static const char *uids[] = {"", "physical.mic", "physical.output", NV_UID_MICROPHONE,
                             NV_UID_MIC_FEED, NV_UID_SPEAKER, NV_UID_SPEAKER_RETURN};
static nv_endpoint_state endpoints[7];
static int fail_listener, listeners[3], fail_remove, live_paths, fail_start, change_on_open;
static UInt32 transports[7];
typedef OSStatus (*listener_fn)(AudioObjectID, UInt32, const AudioObjectPropertyAddress *, void *);
static listener_fn callback;
static OSStatus AudioObjectAddPropertyListener(AudioObjectID d, const AudioObjectPropertyAddress *a,
                                               listener_fn fn, void *ctx) {
    assert(d == kAudioObjectSystemObject && ctx == NULL);
    if ((int)a->mSelector == fail_listener) return -77;
    assert(!listeners[a->mSelector]); listeners[a->mSelector] = 1; callback = fn; return noErr;
}
static OSStatus AudioObjectRemovePropertyListener(AudioObjectID d, const AudioObjectPropertyAddress *a,
                                                  listener_fn fn, void *ctx) {
    assert(d == kAudioObjectSystemObject && fn == callback && ctx == NULL);
    if (fail_remove) return -78;
    assert(listeners[a->mSelector]); listeners[a->mSelector] = 0; return noErr;
}
static OSStatus AudioObjectGetPropertyDataSize(AudioObjectID d, const AudioObjectPropertyAddress *a,
                                              UInt32 q, const void *v, UInt32 *n) {
    (void)d; (void)a; (void)q; (void)v; *n = 6 * sizeof(AudioObjectID); return noErr;
}
static OSStatus AudioObjectGetPropertyData(AudioObjectID d, const AudioObjectPropertyAddress *a,
                                          UInt32 q, const void *v, UInt32 *n, void *data) {
    (void)q; (void)v;
    if (a->mSelector == kAudioHardwarePropertyDevices) {
        assert(*n == 6 * sizeof(AudioObjectID));
        for (AudioObjectID i = 1; i <= 6; i++) ((AudioObjectID *)data)[i-1] = i;
    } else { assert(a->mSelector == kAudioDevicePropertyTransportType); *(UInt32 *)data = transports[d]; }
    return noErr;
}
static AudioObjectID nv_device_for_uid(const char *uid) {
    for (AudioObjectID i = 1; i <= 6; i++) if (!strcmp(uids[i], uid)) return i;
    return 0;
}
static int nv_device_uid(AudioObjectID d, char *b, size_t n) { snprintf(b, n, "%s", uids[d]); return 0; }
static int nv_device_name(AudioObjectID d, char *b, size_t n) { return nv_device_uid(d, b, n); }
static uint32_t nv_device_channels(AudioObjectID d, int input) { (void)input; return endpoints[d].channels; }
static double nv_device_rate(AudioObjectID d) { return endpoints[d].rate; }
static int nv_device_alive(AudioObjectID d) { return endpoints[d].alive != 0; }
static uint32_t nv_device_hidden(AudioObjectID d) { return d == 4 || d == 6; }
static int nv_device_is_nivox(AudioObjectID d) { return d >= 3; }
static nv_endpoint_state nv_device_state(AudioObjectID d, int input) { (void)input; return endpoints[d]; }
static int nv_path_open(nv_path *p, const char *name, AudioObjectID in, uint32_t ic,
                        AudioObjectID out, uint32_t oc, const nv_path_options *o, char *err, size_t n) {
    (void)name; (void)in; (void)ic; (void)out; (void)oc; (void)err; (void)n;
    assert(o->io_buffer == 0 && o->strength == 1.f); /* UI must not change hardware buffers */
    p->bridge = (nv_bridge *)p; live_paths++;
    if (change_on_open) endpoints[in].buffer++;
    return 0;
}
static int nv_path_start(nv_path *p, char *err, size_t n) {
    (void)p; if (fail_start) { snprintf(err, n, "OSStatus -99"); return -1; } return 0;
}
static void nv_path_close(nv_path *p) { if (p->bridge) { live_paths--; p->bridge = NULL; } }
void nv_bridge_get_stats(const nv_bridge *b, nv_bridge_stats *s) {
    (void)b; memset(s, 0, sizeof *s); s->primed = 1; s->pushed = s->pulled = 480;
}
#ifndef NV_CALL_MAC_SOURCE
#define NV_CALL_MAC_SOURCE "../mac/nv_call_mac.c"
#endif
#include NV_CALL_MAC_SOURCE

static void reset(void) {
    assert(!watch_users && !live_paths && !watching[0] && !watching[1]);
    assert(!listeners[1] && !listeners[2]);
    fail_listener = fail_remove = fail_start = change_on_open = 0;
    for (uint32_t i = 1; i <= 6; i++) {
        endpoints[i] = (nv_endpoint_state){i, 1, 2, 480, 48000, 42};
        transports[i] = i < 3 ? 99 : kAudioDeviceTransportTypeVirtual;
    }
}
static void test_listener_failure_closed(void) {
    for (int selector = 1; selector <= 2; selector++) {
        reset(); fail_listener = selector; char err[256];
        assert(open_route(NULL, 0, "physical.mic", err, sizeof err) == NULL);
        assert(strstr(err, "OSStatus -77")); assert(!watch_users && !live_paths);
        assert(!listeners[1] && !listeners[2]);
    }
}
static void test_endpoint_snapshot_changes(void) {
    /* Each property, on physical and hidden endpoints, must invalidate a route. */
    for (int speaker = 0; speaker < 2; speaker++) for (int side = 0; side < 2; side++)
    for (int field = 0; field < 6; field++) {
        reset(); char err[256];
        void *r = open_route(NULL, speaker, speaker ? "physical.output" : "physical.mic", err, sizeof err);
        assert(r && route_valid(NULL, r));
        uint32_t d = speaker ? (side ? 2 : 6) : (side ? 4 : 1);
        if (field == 0) endpoints[d].id += 10;
        if (field == 1) endpoints[d].alive = 0;
        if (field == 2) endpoints[d].channels++;
        if (field == 3) endpoints[d].buffer++;
        if (field == 4) endpoints[d].rate = 44100;
        if (field == 5) endpoints[d].format_hash++;
        assert(!route_valid(NULL, r)); close_route(NULL, r);
    }
}
static void test_restart_same_ids(void) {
    reset(); char err[256];
    void *m = open_route(NULL, 0, "physical.mic", err, sizeof err);
    void *s = open_route(NULL, 1, "physical.output", err, sizeof err);
    assert(m && s && watch_users == 2 && route_valid(NULL, m) && route_valid(NULL, s));
    callback(kAudioObjectSystemObject, 1, &watches[1], NULL);
    assert(!route_valid(NULL, m) && !route_valid(NULL, s));
    close_route(NULL, m); assert(listeners[1] && listeners[2]);
    close_route(NULL, s); assert(!listeners[1] && !listeners[2]);
    callback(kAudioObjectSystemObject, 1, &watches[1], NULL); /* late callback: no freed context */
}
static void test_open_race_and_start_failure(void) {
    for (int mode = 0; mode < 2; mode++) {
        reset(); char err[256]; change_on_open = mode == 0; fail_start = mode == 1;
        assert(!open_route(NULL, 0, "physical.mic", err, sizeof err));
        assert(!watch_users && !live_paths && !listeners[1] && !listeners[2]);
        assert(strstr(err, mode ? "OSStatus -99" : "changed while opening"));
    }
}
static void test_remove_failure_retains_safe_listener(void) {
    reset(); char err[256]; void *r = open_route(NULL, 0, "physical.mic", err, sizeof err); assert(r);
    fail_remove = 1; close_route(NULL, r); assert(!watch_users && watching[0] && watching[1]);
    callback(kAudioObjectSystemObject, 1, &watches[0], NULL);
    fail_remove = 0; r = open_route(NULL, 0, "physical.mic", err, sizeof err); assert(r && route_valid(NULL, r));
    close_route(NULL, r); reset();
}
static void test_catalog_excludes_virtual(void) {
    reset(); nv_call_device *d; size_t n;
    assert(!list(NULL, &d, &n) && n == 6);
    assert(d[0].physical && d[1].physical);
    for (size_t i = 2; i < n; i++) assert(!d[i].physical);
    free(d); assert(driver_ready(NULL)); endpoints[4].rate = 44100; assert(!driver_ready(NULL));
}
int main(void) {
    test_listener_failure_closed(); test_endpoint_snapshot_changes(); test_restart_same_ids();
    test_open_race_and_start_failure(); test_remove_failure_retains_safe_listener(); test_catalog_excludes_virtual();
    reset(); puts("{\"ok\":true,\"tests\":6,\"backend\":\"production control glue; fake HAL/path, not Apple ABI\"}");
    return 0;
}
