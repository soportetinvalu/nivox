#!/usr/bin/env python3
"""Compile the production probe against a synthetic HAL API, NOT the Apple SDK.

Tests only decision logic/error handling. Native build, signing, and actual HAL
presence still require an authorized Apple lab. No audio or host state is used.
"""
# SPDX-License-Identifier: MIT

from pathlib import Path
import argparse
import hashlib
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'callaudio/installer/nv_driver_state.c'
RECORDS = []
HEADER = r'''
#ifndef FAKE_HAL_H
#define FAKE_HAL_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
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
    if (!strcmp(mode(), "service-hang")) {
        const char *marker = getenv("HAL_HANG_MARKER");
        if (marker) {
            FILE *f = fopen(marker, "a");
            if (f) { fputs("ready\n", f); fclose(f); }
        }
        for (;;) pause();
    }
    return !strcmp(mode(), "service-error") ? -77 : noErr;
}
static OSStatus AudioObjectGetPropertyData(AudioObjectID d,
    const AudioObjectPropertyAddress *a, UInt32 n, const void *q, UInt32 *size, void *out) {
    (void)d; (void)n; (void)size;
    if (!strcmp(mode(), "lookup-hang")) for (;;) pause();
    if (!strcmp(mode(), "lookup-error")) return -88;
    if (a->selector == kAudioDevicePropertyDeviceIsAlive) {
        if (!strcmp(mode(), "alive-hang")) for (;;) pause();
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


def compile_probe(base, source):
    base.mkdir(parents=True, exist_ok=True)
    for directory in ['CoreAudio', 'CoreFoundation']:
        folder = base / directory
        folder.mkdir()
        (folder / (directory + '.h')).write_text(HEADER)
    binary = base / 'probe'
    cc = shutil.which('clang') or shutil.which('cc')
    if not cc:
        raise RuntimeError('C compiler required for probe logic tests')
    subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unknown-pragmas',
                    '-I' + str(base), str(source), '-o', str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


class DriverStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'build').mkdir(exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='probe-logic-', dir=ROOT / 'build')
        cls.base = Path(cls.tmp.name)
        cls.binary = compile_probe(cls.base, SOURCE)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe(self, expected, scenario):
        return self.run_probe(expected, scenario, 5).returncode

    def run_probe(self, expected, scenario, timeout, budget=None, inherited=None, via_bash=False):
        def inherit_alarm():
            assert inherited is not None
            if 'ignored' in inherited:
                signal.signal(signal.SIGALRM, signal.SIG_IGN)
            if 'blocked' in inherited:
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        start = time.monotonic()
        try:
            command = [str(self.binary), expected] + ([] if budget is None else [str(budget)])
            if via_bash:
                command = ['bash', '-c', 'exec "$@"', 'probe-launch', *command]
            p = subprocess.run(command, env={'SCENARIO': scenario},
                               capture_output=True, text=True, timeout=timeout,
                               preexec_fn=inherit_alarm if inherited else None)
        except subprocess.TimeoutExpired:
            RECORDS.append(dict(test=self.id(), expected=expected, scenario=scenario,
                                inherited=inherited, via_bash=via_bash,
                                outer_timeout=True, elapsed=time.monotonic() - start))
            raise
        RECORDS.append(dict(test=self.id(), expected=expected, scenario=scenario,
                            inherited=inherited, via_bash=via_bash,
                            rc=p.returncode, stdout=p.stdout, stderr=p.stderr,
                            elapsed=time.monotonic() - start))
        return p

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

    def test_remaining_budget_is_bounded_and_cannot_disable_alarm(self):
        for budget in ['', '0', '-1', '6', '40', '01', '1x', '0ms', '5001ms',
                       '10000ms', '-1ms', '01ms', '1.5ms', ' 1ms', '1msx']:
            with self.subTest(budget=budget):
                p = self.run_probe('absent', 'service-hang', 3, budget=budget)
                self.assertEqual(p.returncode, 2, p.stderr)
        for scenario in ['service-hang', 'lookup-hang', 'alive-hang']:
            with self.subTest(scenario=scenario):
                start = time.monotonic()
                p = self.run_probe('present', scenario, 3, budget=1)
                self.assertEqual(p.returncode, 124, p.stderr)
                self.assertGreaterEqual(time.monotonic() - start, 0.9)
                self.assertLess(time.monotonic() - start, 3)

    def test_monotonic_mode_never_enters_hal_and_tracks_elapsed_time(self):
        before = time.monotonic()
        first = self.run_probe('--monotonic-ms', 'service-hang', 1)
        time.sleep(0.15)
        last = self.run_probe('--monotonic-ms', 'service-hang', 1)
        self.assertEqual((first.returncode, last.returncode), (0, 0))
        self.assertRegex(first.stdout, r'^[0-9]+\n$')
        self.assertRegex(last.stdout, r'^[0-9]+\n$')
        self.assertGreaterEqual(int(last.stdout) - int(first.stdout), 140)
        self.assertLessEqual(int(last.stdout) - int(first.stdout), (time.monotonic() - before) * 1000 + 2)

    def test_millisecond_watchdog_with_inherited_blocked_alarm(self):
        for budget in ['1ms', '125ms', '999ms']:
            for via_bash in [False, True]:
                with self.subTest(budget=budget, via_bash=via_bash):
                    p = self.run_probe('present', 'service-hang', 2, budget=budget,
                                       inherited='blocked-ignored', via_bash=via_bash)
                    self.assertEqual(p.returncode, 124, p.stderr)
                    self.assertLess(RECORDS[-1]['elapsed'], int(budget[:-2]) / 1000 + 0.7)

    def test_blocked_hal_calls_have_real_process_deadline(self):
        # Execute the production C, including its default five-second alarm.
        for scenario in ['service-hang', 'lookup-hang', 'alive-hang']:
            with self.subTest(scenario=scenario):
                start = time.monotonic()
                p = self.run_probe('present', scenario, 8)
                elapsed = time.monotonic() - start
                self.assertEqual(p.returncode, 124, p.stderr)
                self.assertIn('HAL probe exceeded deadline', p.stderr)
                self.assertGreaterEqual(elapsed, 4)
                self.assertLess(elapsed, 8)

    def test_inherited_alarm_mask_and_disposition_cannot_disable_deadline(self):
        # Real exec signal inheritance, including a shell between parent and C.
        for inherited in ['blocked', 'ignored', 'blocked-ignored']:
            for via_bash in [False, True]:
                for scenario in ['service-hang', 'lookup-hang', 'alive-hang']:
                    with self.subTest(inherited=inherited, via_bash=via_bash, scenario=scenario):
                        p = self.run_probe('present', scenario, 2.5, budget=1,
                                           inherited=inherited, via_bash=via_bash)
                        self.assertEqual(p.returncode, 124, p.stderr)
                        self.assertIn('HAL probe exceeded deadline', p.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--probe-source', type=Path, default=SOURCE)
    args, remaining = parser.parse_known_args()
    SOURCE = args.probe_source.resolve()
    suite = unittest.main(argv=[sys.argv[0], *remaining], verbosity=2, exit=False)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(dict(
            scope='Production C with synthetic HAL API, NOT Apple SDK/native acceptance',
            probe_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            tests=suite.result.testsRun, failures=len(suite.result.failures),
            errors=len(suite.result.errors), records=RECORDS), indent=2) + '\n')
    sys.exit(not suite.result.wasSuccessful())
