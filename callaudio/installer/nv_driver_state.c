/* SPDX-License-Identifier: MIT
 * Read-only HAL readiness probe. No capture, defaults, or device mutation.
 * Exit 0: expected state; 1: state mismatch; 2: query/usage error.
 * Run as a fresh process per poll to avoid retaining pre-restart HAL objects.
 */
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>

static void deadline_exceeded(int signal_number) {
    (void)signal_number;
    /* Only async-signal-safe operations, including when HAL is blocked. */
    static const char message[] = "HAL probe exceeded deadline (at most five seconds)\n";
    (void)write(STDERR_FILENO, message, sizeof message - 1);
    _exit(124);
}

static AudioObjectPropertyAddress address(AudioObjectPropertySelector selector) {
    AudioObjectPropertyAddress a = {selector, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    return a;
}

static int lookup(const char *uid, AudioObjectID *device) {
    CFStringRef s = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    if (!s) return 2;
    *device = kAudioObjectUnknown;
    UInt32 size = sizeof *device;
    AudioObjectPropertyAddress a = address(kAudioHardwarePropertyTranslateUIDToDevice);
    OSStatus status = AudioObjectGetPropertyData(kAudioObjectSystemObject, &a,
                                                sizeof s, &s, &size, device);
    if (status != noErr || *device == kAudioObjectUnknown) {
        /* Hidden devices may require the older DeviceForUID translation. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        *device = kAudioObjectUnknown;
        AudioValueTranslation t = {&s, sizeof s, device, sizeof *device};
        size = sizeof t;
        a = address(kAudioHardwarePropertyDeviceForUID);
        status = AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &t);
#pragma clang diagnostic pop
    }
    CFRelease(s);
    if (status != noErr) {
        fprintf(stderr, "HAL lookup failed: %s (%d)\n", uid, (int)status);
        return 2; /* A service/query failure is NOT evidence of absence. */
    }
    return 0;
}

int main(int argc, char **argv) {
    unsigned budget = 5;
    if ((argc != 2 && argc != 3) || (strcmp(argv[1], "present") && strcmp(argv[1], "absent")) ||
        (argc == 3 && (strlen(argv[2]) != 1 || argv[2][0] < '1' || argv[2][0] > '5'))) {
        fprintf(stderr, "Usage: nv-driver-state present|absent [deadline-seconds:1..5]\n");
        return 2;
    }
    if (argc == 3) budget = (unsigned)(argv[2][0] - '0');
    if (signal(SIGALRM, deadline_exceeded) == SIG_ERR) return 2;
    alarm(budget); /* Before any HAL call; never longer than the phase remainder. */
    int present = !strcmp(argv[1], "present");
    AudioObjectPropertyAddress a = address(kAudioHardwarePropertyDevices);
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) != noErr) {
        fprintf(stderr, "HAL service query failed\n");
        return 2;
    }
    const char *uids[] = {"nivox.microphone.v1", "nivox.mic-feed.v1",
                          "nivox.speaker.v1", "nivox.speaker-return.v1"};
    int mismatch = 0;
    for (unsigned i = 0; i < sizeof uids / sizeof *uids; ++i) {
        AudioObjectID device;
        if (lookup(uids[i], &device)) return 2;
        if (!present) {
            if (device != kAudioObjectUnknown) mismatch = 1;
        } else if (device == kAudioObjectUnknown) {
            mismatch = 1;
        } else {
            UInt32 alive = 0;
            size = sizeof alive;
            a = address(kAudioDevicePropertyDeviceIsAlive);
            if (AudioObjectGetPropertyData(device, &a, 0, NULL, &size, &alive) != noErr) return 2;
            if (!alive) mismatch = 1;
        }
    }
    if (mismatch) fprintf(stderr, "HAL state not ready: expected %s\n", argv[1]);
    return mismatch;
}
