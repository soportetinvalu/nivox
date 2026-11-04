/* SPDX-License-Identifier: MIT
 * Read-only HAL readiness probe. No capture, defaults, or device mutation.
 * Exit 0: expected state; 1: state mismatch; 2: query/usage error.
 * Run as a fresh process per poll to avoid retaining pre-restart HAL objects.
 */
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>

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
    if (argc != 2 || (strcmp(argv[1], "present") && strcmp(argv[1], "absent"))) {
        fprintf(stderr, "Usage: nv-driver-state present|absent\n");
        return 2;
    }
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
