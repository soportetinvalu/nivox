/* SPDX-License-Identifier: MIT
 * Executes the production control state machine with an injected HAL backend.
 * Does not simulate Apple compilation, TCC, physical IO or acoustic quality.
 */
#include "nv_call.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    nv_call_device devices[3];
    nv_call_stats stats[2];
    int ready, list_error, fail_route, opens, closes, live, invalid_route;
} fixture;
static int list(void *ctx, nv_call_device **out, size_t *n) {
    fixture *f = ctx;
    if (f->list_error) return -1;
    *n = 3; *out = malloc(sizeof f->devices); assert(*out);
    memcpy(*out, f->devices, sizeof f->devices);
    return 0;
}
static int ready(void *ctx) { return ((fixture *)ctx)->ready; }
static void *open_route(void *ctx, int speaker, const char *uid, char *err, size_t n) {
    fixture *f = ctx;
    assert(!strcmp(uid, speaker ? "physical.output" : "physical.mic"));
    f->opens++;
    if (f->fail_route == speaker) { snprintf(err, n, "OSStatus -10875"); return NULL; }
    int *r = malloc(sizeof *r); assert(r); *r = speaker; f->live++; return r;
}
static void close_route(void *ctx, void *r) {
    fixture *f = ctx; assert(r); assert(f->live > 0);
    free(r); f->live--; f->closes++;
}
static void stats(void *ctx, void *route, nv_call_stats *s) { *s = ((fixture *)ctx)->stats[*(int *)route]; }
static int route_valid(void *ctx, void *route) { return ((fixture *)ctx)->invalid_route != *(int *)route; }
static nv_call *create(fixture *f) {
    memset(f, 0, sizeof *f); f->ready = 1; f->fail_route = -1; f->invalid_route = -1;
    f->devices[0] = (nv_call_device){"physical.mic", "USB mic", 1, 0, 48000, 1, 1};
    f->devices[1] = (nv_call_device){"physical.output", "Headphones", 0, 2, 44100, 1, 1};
    f->devices[2] = (nv_call_device){"nivox.microphone.v1", "Virtual", 1, 2, 48000, 0, 1};
    nv_call_backend b = {list, ready, open_route, close_route, stats, route_valid};
    nv_call *c = nv_call_create(&b, f); assert(c); return c;
}
static void start(nv_call *c) {
    assert(nv_call_start(c, "physical.mic", "physical.output", 0) == 0);
    assert(nv_call_state(c) == NV_CALL_STARTING);
}
static void progress(fixture *f) {
    for (int i = 0; i < 2; i++) {
        f->stats[i].primed = 1; f->stats[i].pushed += 480; f->stats[i].pulled += 480;
    }
}
static void error(nv_call *c, fixture *f, const char *detail) {
    assert(nv_call_state(c) == NV_CALL_ERROR); assert(f->live == 0);
    assert(strstr(nv_call_detail(c), detail));
}
static void destroy(nv_call *c, fixture *f) { nv_call_destroy(c); assert(f->live == 0); }
static void test_idle_and_catalog(void) {
    fixture f; nv_call *c = create(&f);
    assert(nv_call_state(c) == NV_CALL_STOPPED); assert(f.opens == 0);
    nv_call_poll(c, 100); assert(f.opens == 0);
    assert(nv_call_refresh(c) == 0); assert(nv_call_device_count(c) == 3);
    assert(!strcmp(nv_call_device_name(nv_call_device_at(c, 0)), "USB mic"));
    assert(!strcmp(nv_call_device_uid(nv_call_device_at(c, 1)), "physical.output"));
    assert(!nv_call_device_at(c, 3)); destroy(c, &f);
}
static void test_no_driver(void) {
    fixture f; nv_call *c = create(&f); f.ready = 0;
    assert(nv_call_start(c, "physical.mic", "physical.output", 0) == -1);
    error(c, &f, "Driver Nivox"); assert(f.opens == 0); destroy(c, &f);
}
static void test_bad_endpoints(void) {
    const char *uids[] = {"", "missing", "nivox.microphone.v1", "physical.output"};
    for (size_t i = 0; i < sizeof uids / sizeof *uids; i++) {
        fixture f; nv_call *c = create(&f);
        assert(nv_call_start(c, uids[i], "physical.output", 0) == -1);
        error(c, &f, "Selecciona"); assert(!f.opens); destroy(c, &f);
    }
    fixture f; nv_call *c = create(&f); f.devices[0].physical = 0;
    assert(nv_call_start(c, "physical.mic", "physical.output", 0) == -1);
    error(c, &f, "Selecciona"); destroy(c, &f);
    c = create(&f);
    assert(nv_call_start(c, "physical.mic", "physical.mic", 0) == -1);
    error(c, &f, "Selecciona"); destroy(c, &f);
}
static void test_open_rollback(void) {
    for (int route = 0; route < 2; route++) {
        fixture f; nv_call *c = create(&f); f.fail_route = route;
        assert(nv_call_start(c, "physical.mic", "physical.output", 0) == -1);
        error(c, &f, "OSStatus -10875"); assert(f.closes == route);
        f.fail_route = -1; start(c); nv_call_stop(c); assert(!f.live); destroy(c, &f);
    }
}
static void test_activity_required_on_both_paths(void) {
    fixture f; nv_call *c = create(&f); start(c);
    nv_call_poll(c, 0.5); assert(nv_call_state(c) == NV_CALL_STARTING);
    progress(&f); f.stats[1].primed = 0;
    nv_call_poll(c, 1); assert(nv_call_state(c) == NV_CALL_STARTING);
    f.stats[1].primed = 1; progress(&f);
    nv_call_poll(c, 1.5); assert(nv_call_state(c) == NV_CALL_RUNNING);
    assert(nv_call_start(c, "physical.mic", "physical.output", 2) == -1); assert(f.opens == 2);
    nv_call_stop(c); assert(nv_call_state(c) == NV_CALL_STOPPED); assert(f.closes == 2);
    nv_call_stop(c); assert(f.closes == 2); nv_call_poll(c, 100); assert(f.opens == 2);
    destroy(c, &f);
}
static void test_stall_startup_and_running(void) {
    for (int mode = 0; mode < 3; mode++) {
        fixture f; nv_call *c = create(&f); start(c);
        if (mode) { progress(&f); nv_call_poll(c, 0.5); assert(nv_call_state(c) == NV_CALL_RUNNING); }
        if (mode == 1) f.stats[0].pushed += 480; /* playback stalled */
        if (mode == 2) f.stats[1].pulled += 480; /* capture stalled */
        nv_call_poll(c, 5.6); error(c, &f, "5 s"); destroy(c, &f);
    }
}
static void test_route_changes(void) {
    for (int mode = 0; mode < 5; mode++) {
        fixture f; nv_call *c = create(&f); start(c);
        if (mode == 0) f.devices[0].alive = 0;
        if (mode == 1) f.devices[1].alive = 0;
        if (mode == 2) f.devices[1].rate = 48000;
        if (mode == 3) f.devices[0].inputs = 2;
        if (mode == 4) f.ready = 0;
        nv_call_poll(c, 1); error(c, &f, mode == 4 ? "driver" : "formato");
        nv_call_poll(c, 10); assert(f.opens == 2); /* no auto restart */
        destroy(c, &f);
    }
}
static void test_route_snapshot_failure(void) {
    for (int route = 0; route < 2; route++) {
        fixture f; nv_call *c = create(&f); start(c); progress(&f); nv_call_poll(c, .5);
        assert(nv_call_state(c) == NV_CALL_RUNNING);
        f.invalid_route = route; progress(&f); nv_call_poll(c, 1);
        assert(nv_call_state(c) == NV_CALL_ERROR);
        error(c, &f, "CoreAudio reiniciado"); assert(f.closes == 2);
        f.invalid_route = -1; nv_call_poll(c, 2); assert(f.opens == 2);
        start(c); assert(f.opens == 4); destroy(c, &f);
    }
    nv_call_backend b = {list, ready, open_route, close_route, stats, NULL};
    assert(nv_call_create(&b, NULL) == NULL); /* missing guard fails closed */
}
static void test_catalog_failure(void) {
    fixture f; nv_call *c = create(&f); start(c); f.list_error = 1;
    nv_call_poll(c, 1); error(c, &f, "CoreAudio"); assert(!nv_call_driver_ready(c)); destroy(c, &f);
}
static void test_rejected_samples_and_gaps(void) {
    fixture f; nv_call *c = create(&f); start(c); progress(&f);
    f.stats[0].underruns = 2; f.stats[1].overflows = 3;
    nv_call_poll(c, 0.5); assert(nv_call_state(c) == NV_CALL_RUNNING); assert(nv_call_underruns(c) == 5);
    f.stats[1].rejected = 1; nv_call_poll(c, 1); error(c, &f, "rechazó"); destroy(c, &f);
}
static void test_destroy_active(void) { fixture f; nv_call *c = create(&f); start(c); destroy(c, &f); assert(f.closes == 2); }
static void ui_event(nv_call *c, int event, int permission_pending) {
    if (nv_call_ui_should_stop(event, permission_pending)) nv_call_stop(c);
}
static void test_ui_hide_keeps_call(void) {
    for (int running = 0; running < 2; running++) {
        fixture f; nv_call *c = create(&f); start(c);
        if (running) { progress(&f); nv_call_poll(c, 0.5); }
        int state = nv_call_state(c);
        for (int i = 0; i < 100; i++) {
            ui_event(c, NV_CALL_UI_TAB_CHANGED, 0);
            ui_event(c, NV_CALL_UI_WINDOW_CLOSED, 0);
            assert(nv_call_state(c) == state); assert(f.live == 2); assert(f.closes == 0);
        }
        nv_call_stop(c); assert(f.closes == 2); destroy(c, &f);
    }
}
static void test_ui_pending_permission_is_cancelled(void) {
    assert(nv_call_ui_should_stop(NV_CALL_UI_TAB_CHANGED, 1));
    assert(nv_call_ui_should_stop(NV_CALL_UI_WINDOW_CLOSED, 1));
    fixture f; nv_call *c = create(&f);
    ui_event(c, NV_CALL_UI_WINDOW_CLOSED, 1);
    nv_call_poll(c, 100); assert(f.opens == 0); destroy(c, &f);
    /* Swift's generation token is a separate source-contract check, not TCC IO. */
}
static void test_ui_sleep_stops_without_restart(void) {
    for (int pending = 0; pending < 2; pending++) {
        fixture f; nv_call *c = create(&f); start(c);
        ui_event(c, NV_CALL_UI_SLEEP, pending);
        assert(nv_call_state(c) == NV_CALL_STOPPED); assert(f.live == 0); assert(f.closes == 2);
        ui_event(c, NV_CALL_UI_TAB_CHANGED, 0); /* reopening isn't a start */
        nv_call_poll(c, 100); assert(f.opens == 2); destroy(c, &f);
    }
}
static void test_ui_hidden_health_checks(void) {
    fixture f; nv_call *c = create(&f); start(c); progress(&f); nv_call_poll(c, 0.5);
    ui_event(c, NV_CALL_UI_WINDOW_CLOSED, 0);
    nv_call_poll(c, 6); error(c, &f, "5 s"); assert(f.closes == 2); destroy(c, &f);
}
int main(void) {
    unsigned tests = 0;
#define TEST(name) do { name(); tests++; } while (0)
    TEST(test_idle_and_catalog); TEST(test_no_driver); TEST(test_bad_endpoints);
    TEST(test_open_rollback); TEST(test_activity_required_on_both_paths);
    TEST(test_stall_startup_and_running); TEST(test_route_changes); TEST(test_catalog_failure);
    TEST(test_rejected_samples_and_gaps); TEST(test_destroy_active);
    TEST(test_ui_hide_keeps_call); TEST(test_ui_pending_permission_is_cancelled);
    TEST(test_route_snapshot_failure);
    TEST(test_ui_sleep_stops_without_restart); TEST(test_ui_hidden_health_checks);
    printf("{\"ok\":true,\"tests\":%u,\"backend\":\"injected; not CoreAudio\"}\n", tests);
    return 0;
}
