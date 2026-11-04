#!/usr/bin/env python3
"""Compile the production probe against a synthetic HAL API, NOT the Apple SDK.

Tests only decision logic/error handling. Native build, signing, and actual HAL
presence still require an authorized Apple lab. No audio or host state is used.
"""
# SPDX-License-Identifier: MIT

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADER = r'''
#ifndef FAKE_HAL_H
#define FAKE_HAL_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t UInt32;
typedef uint32_t AudioObjectID;
typedef uint32_t AudioObjectPropertySelector;
typedef int32_t OSStatus;
typedef const char *CFStringRef;
typedef struct { UInt32 selector, scope, element; } AudioObjectPropertyAddress;
typedef struct { void *in; UInt32 in_size; void *out; UInt32 out_size; } AudioValueTranslation;
enum { kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain,
       kAudioHardwarePropertyTranslateUIDToDevice, kAudioHardwarePropertyDeviceForUID,
       kAudioHardwarePropertyDevices, kAudioDevicePropertyDeviceIsAlive };
enum { kAudioObjectUnknown = 0, kAudioObjectSystemObject = 1, noErr = 0, kCFStringEncodingUTF8 = 0 };
static const char *mode(void) { const char *s = getenv("SCENARIO"); return s ? s : "present"; }
static CFStringRef CFStringCreateWithCString(void *a, const char *s, int encoding) {
    (void)a; (void)encoding; return !strcmp(mode(), "allocation-error") ? NULL : s;
}
static void CFRelease(CFStringRef s) { (void)s; }
static OSStatus AudioObjectGetPropertyDataSize(AudioObjectID d,
    const AudioObjectPropertyAddress *a, UInt32 n, const void *q, UInt32 *size) {
    (void)d; (void)a; (void)n; (void)q; *size = sizeof(AudioObjectID);
    return !strcmp(mode(), "service-error") ? -77 : noErr;
}
static OSStatus AudioObjectGetPropertyData(AudioObjectID d,
    const AudioObjectPropertyAddress *a, UInt32 n, const void *q, UInt32 *size, void *out) {
    (void)d; (void)n; (void)size;
    if (!strcmp(mode(), "lookup-error")) return -88;
    if (a->selector == kAudioDevicePropertyDeviceIsAlive) {
        if (!strcmp(mode(), "alive-error")) return -99;
        *(UInt32 *)out = strcmp(mode(), "dead") != 0;
        return noErr;
    }
    const char *uid;
    AudioObjectID *device;
    if (a->selector == kAudioHardwarePropertyDeviceForUID) {
        AudioValueTranslation *t = out;
        uid = *(CFStringRef *)t->in;
        device = t->out;
    } else {
        uid = *(CFStringRef *)q;
        device = out;
        if (!strcmp(mode(), "fallback")) { *device = kAudioObjectUnknown; return noErr; }
    }
    *device = !strcmp(mode(), "absent") ? kAudioObjectUnknown : 42;
    if (!strcmp(mode(), "partial") && !strcmp(uid, "nivox.speaker-return.v1"))
        *device = kAudioObjectUnknown;
    return noErr;
}
#endif
'''


class DriverStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='probe-logic-', dir=ROOT / 'build')
        cls.base = Path(cls.tmp.name)
        for directory in ['CoreAudio', 'CoreFoundation']:
            folder = cls.base / directory
            folder.mkdir()
            (folder / (directory + '.h')).write_text(HEADER)
        cls.binary = cls.base / 'probe'
        cc = shutil.which('clang') or shutil.which('cc')
        if not cc:
            raise RuntimeError('C compiler required for probe logic tests')
        subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unknown-pragmas',
                        '-I' + str(cls.base), str(ROOT / 'callaudio/installer/nv_driver_state.c'),
                        '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe(self, expected, scenario):
        return subprocess.run([str(self.binary), expected], env={'SCENARIO': scenario},
                              capture_output=True, text=True, timeout=5).returncode

    def test_present_requires_all_four_live_uids(self):
        self.assertEqual(self.probe('present', 'present'), 0)
        for scenario in ['absent', 'partial', 'dead']:
            with self.subTest(scenario=scenario):
                self.assertEqual(self.probe('present', scenario), 1)

    def test_absent_rejects_even_one_retained_uid(self):
        self.assertEqual(self.probe('absent', 'absent'), 0)
        for scenario in ['present', 'partial', 'dead']:
            with self.subTest(scenario=scenario):
                self.assertEqual(self.probe('absent', scenario), 1)

    def test_query_failures_are_never_absence_or_readiness(self):
        for expected in ['present', 'absent']:
            for scenario in ['service-error', 'lookup-error', 'allocation-error']:
                with self.subTest(expected=expected, scenario=scenario):
                    self.assertEqual(self.probe(expected, scenario), 2)
        self.assertEqual(self.probe('present', 'alive-error'), 2)

    def test_hidden_device_fallback(self):
        self.assertEqual(self.probe('present', 'fallback'), 0)
        self.assertEqual(self.probe('absent', 'fallback'), 1)

    def test_usage_error_is_not_success(self):
        self.assertEqual(self.probe('anything', 'present'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
