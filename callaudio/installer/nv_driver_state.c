/* SPDX-License-Identifier: MIT
 * Read-only HAL readiness probe. No capture, defaults, or device mutation.
 * Exit 0: expected state; 1: state mismatch; 2: query/usage error.
 * Run as a fresh process per poll to avoid retaining pre-restart HAL objects.
 */
#define _POSIX_C_SOURCE 200809L
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <time.h>
#include <inttypes.h>
#include <sys/time.h>

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

static unsigned budget_ms(const char *s) {
    size_t n = strlen(s);
    if (n == 1 && s[0] >= '1' && s[0] <= '5') return (unsigned)(s[0] - '0') * 1000;
    if (n < 3 || n > 6 || strcmp(s + n - 2, "ms") || s[0] < '1' || s[0] > '9') return 0;
    unsigned value = 0;
    for (size_t i = 0; i < n - 2; ++i) {
        if (s[i] < '0' || s[i] > '9') return 0;
        value = value * 10 + (unsigned)(s[i] - '0');
    }
    return value <= 5000 ? value : 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--monotonic-ms")) {
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec < 0) return 2;
        return printf("%" PRIu64 "\n", (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000) < 0 ? 2 : 0;
    }
    unsigned budget = 5000;
    if ((argc != 2 && argc != 3) || (strcmp(argv[1], "present") && strcmp(argv[1], "absent")) ||
        (argc == 3 && !(budget = budget_ms(argv[2])))) {
        fprintf(stderr, "Usage: nv-driver-state --monotonic-ms | present|absent [1..5 seconds or 1..5000ms]\n");
        return 2;
    }
    struct sigaction action = {0};
    sigset_t alarm_set;
    action.sa_handler = deadline_exceeded;
    if (sigemptyset(&action.sa_mask) || sigemptyset(&alarm_set) ||
        sigaddset(&alarm_set, SIGALRM) ||
        sigaction(SIGALRM, &action, NULL) ||
        sigprocmask(SIG_UNBLOCK, &alarm_set, NULL)) return 2;
    /* exec preserves a blocked signal mask: resetting SIG_IGN alone is not enough. */
    /* Millisecond remainder: no rounding down the useful polling window or up
     * the final HAL watchdog. A zero timer is never accepted (it disables it). */
    struct itimerval timer = {0};
    timer.it_value.tv_sec = budget / 1000;
    timer.it_value.tv_usec = (budget % 1000) * 1000;
    if (setitimer(ITIMER_REAL, &timer, NULL)) return 2;
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
