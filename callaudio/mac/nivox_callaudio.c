/* SPDX-License-Identifier: MIT
 * nivox-callaudio: headless call-audio engine.
 *   mic path:     physical microphone -> denoise -> "Nivox Mic Feed" -> apps read "Nivox Microphone"
 *   speaker path: apps play "Nivox Speaker" -> "Nivox Speaker Return" -> denoise -> physical output
 * Requires the Nivox HAL plug-in installed. Audio never leaves the process;
 * no network, no files written (stats go to stderr).
 */
#include "nv_path.h"
#include <signal.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t stop_flag;
static void on_signal(int s) { (void)s; stop_flag = 1; }

static void usage(void) {
    fprintf(stderr,
        "usage: nivox-callaudio [--list] [--mic-uid UID] [--out-uid UID] [--no-mic] [--no-speaker]\n"
        "                       [--mic-strength 0..1] [--speaker-strength 0..1] [--buffer FRAMES]\n"
        "                       [--seconds N]\n"
        "Defaults: system default input/output (Nivox devices are never chosen as physical ends).\n");
}

/* Default device unless it is a Nivox device (would loop back into itself). */
static AudioObjectID pick(const char *uid, int input) {
    if (uid) return nv_device_for_uid(uid);
    AudioObjectID d = nv_default_device(input);
    return (d != kAudioObjectUnknown && !nv_device_is_nivox(d)) ? d : kAudioObjectUnknown;
}

static _Atomic uint64_t topology_generation;
static OSStatus topology_changed(AudioObjectID object, UInt32 count,
                                 const AudioObjectPropertyAddress *addresses, void *context) {
    (void)object; (void)count; (void)addresses; (void)context;
    atomic_fetch_add_explicit(&topology_generation, 1, memory_order_relaxed);
    return noErr;
}

typedef struct {
    int enabled; nv_path path; int open;
    nv_endpoint_state input, output;
    uint64_t generation;
} slot;

static int needs_restart(const slot *s, AudioObjectID in, AudioObjectID out) {
    nv_endpoint_state i = nv_device_state(in, 1), o = nv_device_state(out, 0);
    return s->generation != atomic_load_explicit(&topology_generation, memory_order_relaxed) ||
           nv_endpoint_changed(&s->input, &i) || nv_endpoint_changed(&s->output, &o);
}

static int open_slot(slot *s, const char *name, AudioObjectID in, uint32_t in_ch, AudioObjectID out,
                     uint32_t out_ch, const nv_path_options *o) {
    char err[256];
    if (nv_path_open(&s->path, name, in, in_ch, out, out_ch, o, err, sizeof err) ||
        nv_path_start(&s->path, err, sizeof err)) {
        nv_path_close(&s->path);
        fprintf(stderr, "{\"event\":\"error\",\"detail\":\"%s\"}\n", err);
        return -1;
    }
    s->open = 1; s->input = nv_device_state(in, 1); s->output = nv_device_state(out, 0);
    s->generation = atomic_load_explicit(&topology_generation, memory_order_relaxed);
    fprintf(stderr, "{\"event\":\"started\",\"path\":\"%s\",\"target\":%u}\n", name, s->path.target_fill);
    return 0;
}

int main(int argc, char **argv) {
    nv_host_time_init();
    const char *mic_uid = NULL, *out_uid = NULL;
    int use_mic = 1, use_speaker = 1;
    float mic_strength = 1.f, spk_strength = 1.f;
    double seconds = 0;
    uint32_t buffer = 480;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--list")) { nv_list_devices_json(stdout); return 0; }
        else if (!strcmp(a, "--mic-uid") && v) { mic_uid = v; i++; }
        else if (!strcmp(a, "--out-uid") && v) { out_uid = v; i++; }
        else if (!strcmp(a, "--no-mic")) use_mic = 0;
        else if (!strcmp(a, "--no-speaker")) use_speaker = 0;
        else if (!strcmp(a, "--mic-strength") && v) { mic_strength = strtof(v, NULL); i++; }
        else if (!strcmp(a, "--speaker-strength") && v) { spk_strength = strtof(v, NULL); i++; }
        else if (!strcmp(a, "--buffer") && v) { buffer = (uint32_t)strtoul(v, NULL, 10); i++; }
        else if (!strcmp(a, "--seconds") && v) { seconds = strtod(v, NULL); i++; }
        else { usage(); return 2; }
    }
    if (!(mic_strength >= 0 && mic_strength <= 1) || !(spk_strength >= 0 && spk_strength <= 1) ||
        buffer < 32 || buffer > 4096 || (!use_mic && !use_speaker)) { usage(); return 2; }
    AudioObjectID feed = nv_device_for_uid(NV_UID_MIC_FEED), ret = nv_device_for_uid(NV_UID_SPEAKER_RETURN);
    if (feed == kAudioObjectUnknown || ret == kAudioObjectUnknown) {
        fprintf(stderr, "{\"event\":\"error\",\"detail\":\"Nivox HAL plug-in not loaded (hidden devices missing)\"}\n");
        return 3;
    }
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    AudioObjectPropertyAddress watches[] = {
        {kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain},
        {kAudioHardwarePropertyServiceRestarted, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain},
    };
    for (size_t i = 0; i < sizeof watches / sizeof watches[0]; i++) {
        OSStatus st = AudioObjectAddPropertyListener(kAudioObjectSystemObject, &watches[i], topology_changed, NULL);
        if (st != noErr) {
            fprintf(stderr, "{\"event\":\"error\",\"detail\":\"device/restart listener registration failed\",\"status\":%d}\n", (int)st);
            return 4;
        }
    }
    nv_path_options mo = {mic_strength, buffer, 10}, so = {spk_strength, buffer, 10};
    slot mic, spk;
    memset(&mic, 0, sizeof mic); memset(&spk, 0, sizeof spk);
    mic.enabled = use_mic; spk.enabled = use_speaker;
    int rc = 0;
    double elapsed = 0;
    while (!stop_flag && (seconds <= 0 || elapsed < seconds)) {
        /* Resolve hidden IDs every poll: coreaudiod may replace them after restart. */
        feed = nv_device_for_uid(NV_UID_MIC_FEED); ret = nv_device_for_uid(NV_UID_SPEAKER_RETURN);
        if (mic.enabled) {
            AudioObjectID in = pick(mic_uid, 1);
            if (mic.open && needs_restart(&mic, in, feed)) {
                nv_path_close(&mic.path); mic.open = 0;
                fprintf(stderr, "{\"event\":\"restart\",\"path\":\"mic\"}\n");
            }
            if (!mic.open && in != kAudioObjectUnknown && feed != kAudioObjectUnknown)
                open_slot(&mic, "mic", in, 1, feed, 1, &mo);
        }
        if (spk.enabled) {
            AudioObjectID out = pick(out_uid, 0);
            if (spk.open && needs_restart(&spk, ret, out)) {
                nv_path_close(&spk.path); spk.open = 0;
                fprintf(stderr, "{\"event\":\"restart\",\"path\":\"speaker\"}\n");
            }
            if (!spk.open && out != kAudioObjectUnknown && ret != kAudioObjectUnknown) {
                uint32_t ch = nv_device_channels(out, 0);
                open_slot(&spk, "speaker", ret, 2, out, ch >= 2 ? 2 : 1, &so);
            }
        }
        usleep(500000);
        elapsed += 0.5;
        if ((int)(elapsed * 2) % 4 == 0) { /* every 2 s */
            fputs("{\"event\":\"stats\",\"paths\":[", stderr);
            int first = 1;
            if (mic.open) { nv_path_stats_json(&mic.path, stderr); first = 0; }
            if (spk.open) { if (!first) fputc(',', stderr); nv_path_stats_json(&spk.path, stderr); }
            fputs("]}\n", stderr);
        }
    }
    for (size_t i = 0; i < sizeof watches / sizeof watches[0]; i++)
        AudioObjectRemovePropertyListener(kAudioObjectSystemObject, &watches[i], topology_changed, NULL);
    if (mic.open) nv_path_close(&mic.path);
    if (spk.open) nv_path_close(&spk.path);
    fprintf(stderr, "{\"event\":\"stopped\"}\n");
    return rc;
}
