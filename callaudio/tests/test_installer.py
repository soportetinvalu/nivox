#!/usr/bin/env python3
"""Real transaction shell, substituted OS/HAL commands: NOT macOS acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile
import time
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'callaudio/installer/manage-driver.sh'
BASH = 'bash'
RECORDS = []


def hashes(bundle):
    return {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(bundle.rglob('*')) if p.is_file()} if bundle.exists() else None


class InstallerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'build').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='installer-test-', dir=ROOT / 'build')
        self.base = Path(self.tmp.name)
        self.library = self.base / 'Library'
        self.hal = self.library / 'Audio/Plug-Ins/HAL'
        self.hal.mkdir(parents=True)
        self.dst = self.hal / 'NivoxAudio.driver'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.env = dict(PATH=str(self.bin) + ':/usr/bin:/bin', LC_ALL='C', STATE=str(self.base))
        commands = {
            'uname': 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi',
            'id': 'echo 0',
            'stat': 'echo 0:700',
            'xattr': 'if [ -e "$2/quarantine" ]; then echo com.apple.quarantine; fi',
            'ditto': 'cp -R "$1" "$2"',
            'chown': ':',
            'sleep': ':',
            'killall': '''n=0
[ ! -e "$STATE/restarts" ] || n=$(cat "$STATE/restarts")
n=$((n+1)); printf '%s' "$n" > "$STATE/restarts"
if [ "${FAIL_RESTART:-0}" = all ] || [ "$n" = "${FAIL_RESTART:-0}" ]; then exit 7; fi''',
            'codesign': '''case "${@: -1}" in */nv-driver-state) exit "${FAIL_HELPER_VERIFY:-0}";; esac
n=0
[ ! -e "$STATE/count" ] || n=$(cat "$STATE/count")
n=$((n+1)); printf '%s' "$n" > "$STATE/count"
[ "$n" != "${FAIL_VERIFY:-0}" ]''',
        }
        text = SCRIPT.read_text().replace('/Library', str(self.library))
        # Only the disposable sandbox gains a PATH override.
        text = text.replace('export PATH=/usr/bin:/bin:/usr/sbin:/sbin',
                            'export PATH=' + str(self.bin) + ':/usr/bin:/bin')
        for cmd, body in commands.items():
            target = self.bin / cmd
            target.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
            target.chmod(0o755)
            text = re.sub(r'/(?:usr/bin|usr/sbin|bin)/' + cmd + r'\b', str(target), text)
        self.script = self.base / 'manager.sh'
        self.script.write_text(text)
        self.src = self.base / 'source.driver'
        self.src.mkdir()
        (self.src / 'marker').write_text('candidate')
        (self.src / 'Contents/MacOS').mkdir(parents=True)
        (self.src / 'Contents/MacOS/NivoxAudio').write_bytes(b'\x00sandbox-version-B\xff')
        self.helper = self.base / 'nv-driver-state'
        self.helper.write_text('''#!/bin/bash
set -eu
printf '%s\\n' "$1" >> "$STATE/probes"
n=$(cat "$STATE/restarts")
if [ "${FAIL_LOAD:-0}" = all ] || [ "$n" = "${FAIL_LOAD:-0}" ]; then
    echo 'injected HAL query/state failure' >&2; exit 2
fi
if [ "$1" = present ]; then [ -d "$STATE/Library/Audio/Plug-Ins/HAL/NivoxAudio.driver" ];
else [ ! -e "$STATE/Library/Audio/Plug-Ins/HAL/NivoxAudio.driver" ]; fi
''')
        self.helper.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, p, args, elapsed_seconds=None):
        RECORDS.append(dict(test=self.id(), args=list(map(str, args)), rc=p.returncode,
                            elapsed_seconds=elapsed_seconds,
                            budgets=(self.base / 'budgets').read_text().splitlines() if (self.base / 'budgets').exists() else [],
                            stdout=p.stdout, stderr=p.stderr, installed=hashes(self.dst),
                            lock=(self.hal / '.NivoxAudio.lock').exists(),
                            restarts=(self.base / 'restarts').read_text() if (self.base / 'restarts').exists() else '0',
                            probes=(self.base / 'probes').read_text().splitlines() if (self.base / 'probes').exists() else [],
                            retained=sorted(str(p.relative_to(self.library)) for p in
                                            (self.library / 'Audio/.NivoxAudio-history').glob('transaction.*/*')),
                            previous={str(b): hashes(b) for b in self.previous()},
                            failed={str(b): hashes(b) for b in self.failed()}))
        return p

    def run_manager(self, *args, fail_verify: int | str = 0,
                    fail_restart: int | str = 0, fail_load: int | str = 0):
        for name in ['count', 'restarts']:
            (self.base / name).unlink(missing_ok=True)
        p = subprocess.run([BASH, str(self.script), *map(str, args)],
                           env=dict(self.env, FAIL_VERIFY=str(fail_verify),
                                    FAIL_RESTART=str(fail_restart), FAIL_LOAD=str(fail_load)),
                           text=True, capture_output=True, timeout=15)
        return self.record(p, args)

    def previous(self):
        return list((self.library / 'Audio/.NivoxAudio-history').glob('transaction.*/previous.driver'))

    def failed(self):
        return list((self.library / 'Audio/.NivoxAudio-history').glob('transaction.*/failed.driver'))

    def assert_failed(self, p):
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('Completed ', p.stdout)
        self.assertFalse((self.hal / '.NivoxAudio.lock').exists())

    def installed(self):
        self.dst.mkdir()
        (self.dst / 'marker').write_text('original')
        (self.dst / 'Contents/MacOS').mkdir(parents=True)
        (self.dst / 'Contents/MacOS/NivoxAudio').write_bytes(b'\x00sandbox-version-A\xfe')

    def test_update_and_restore_refused_before_any_system_mutation(self):
        self.installed()
        before = hashes(self.library)
        # The old daemon could satisfy every stable UID while B never loads.
        self.helper.write_text('#!/bin/bash\ntouch "$STATE/probe-called"\nexit 0\n')
        for mode in ['install', 'restore']:
            with self.subTest(mode=mode):
                p = self.run_manager(mode, self.src)
                self.assert_failed(p)
                self.assertIn('loaded driver version cannot be verified', p.stderr)
                self.assertEqual(hashes(self.library), before)
                self.assertFalse((self.library / 'Audio/.NivoxAudio-history').exists())
                for name in ['restarts', 'count', 'probe-called']:
                    self.assertFalse((self.base / name).exists(), name)

    def test_initial_install_succeeds_without_identity_claim(self):
        p = self.run_manager('install', self.src)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(hashes(self.dst), hashes(self.src))
        self.assertIn('HAL presence verified', p.stdout)
        self.assertFalse(self.previous())

    def test_managed_restore_to_occupied_path_is_also_rejected_without_mutation(self):
        self.installed()
        backup = self.library / 'Audio/.NivoxAudio-history/transaction.fixture/previous.driver'
        shutil.copytree(self.src, backup)
        before = hashes(self.library)
        entries = sorted(str(p.relative_to(self.library)) for p in self.library.rglob('*'))
        p = self.run_manager('restore', backup)
        self.assert_failed(p)
        self.assertIn('loaded driver version cannot be verified', p.stderr)
        self.assertEqual(hashes(self.library), before)
        self.assertEqual(sorted(str(p.relative_to(self.library)) for p in self.library.rglob('*')), entries)
        self.assertFalse((self.base / 'restarts').exists())

    def test_signal_immediately_after_displacement_still_recovers_original(self):
        self.installed()
        original = hashes(self.dst)
        move = self.bin / 'mv'
        move.write_text('''#!/bin/bash
/bin/mv "$@" || exit $?
case "$2" in */previous.driver) kill -TERM "$PPID";; esac
''')
        move.chmod(0o755)
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertEqual(p.returncode, 130)
        self.assertEqual(hashes(self.dst), original)
        self.assertIn('state restored', p.stderr)

    def test_uninstall_then_restore_preserves_original_backup(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('uninstall')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(self.dst.exists())
        backup = self.previous()[0]
        self.assertEqual(hashes(backup), original)
        p = self.run_manager('restore', backup)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual(hashes(backup), original)

    def test_restart_failure_rolls_back_and_never_completes(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('uninstall', fail_restart=1)
        self.assert_failed(p)
        self.assertEqual(p.returncode, 7)
        self.assertIn('CoreAudio restart failed (exit 7)', p.stderr)
        self.assertIn('Previous filesystem and HAL presence state restored', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])

    def test_load_failure_rolls_back_and_retains_failure_log(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('uninstall', fail_load=1)
        self.assert_failed(p)
        self.assertIn('HAL state verification timed out (expected absent)', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        txn = self.previous()[0].parent
        self.assertIn('injected HAL query/state failure', (txn / 'transaction-hal-state.log').read_text())
        self.assertEqual((txn / 'recovery-hal-state.log').read_text(), '')

    def test_initial_install_failure_returns_to_absent(self):
        for failure in ['restart', 'load']:
            with self.subTest(failure=failure):
                p = self.run_manager('install', self.src, **{'fail_' + failure: 1})
                self.assert_failed(p)
                self.assertFalse(self.dst.exists())
                self.assertFalse(self.previous())
                self.assertEqual((self.base / 'probes').read_text().splitlines()[-1], 'absent')
                self.assertIn(hashes(self.src), [hashes(b) for b in self.failed()])

    def test_recovery_failures_remain_explicit(self):
        self.installed()
        original = hashes(self.dst)
        for failure in ['restart', 'load']:
            with self.subTest(failure=failure):
                p = self.run_manager('uninstall', **{'fail_' + failure: 'all'})
                self.assert_failed(p)
                self.assertIn('RECOVERY UNVERIFIED', p.stderr)
                self.assertNotIn('state restored', p.stderr)
                self.assertEqual(hashes(self.dst), original)
                self.assertTrue(all(hashes(b) == original for b in self.previous()))

    def test_failed_restore_preserves_source_and_failed_candidate(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('uninstall')
        self.assertEqual(p.returncode, 0, p.stderr)
        backup = self.previous()[0]
        p = self.run_manager('restore', backup, fail_restart=1)
        self.assert_failed(p)
        self.assertFalse(self.dst.exists())
        self.assertEqual(hashes(backup), original)
        self.assertEqual([hashes(b) for b in self.failed()], [original])

    def test_missing_or_symbolic_helper_rejected_without_displacement(self):
        self.installed()
        original = hashes(self.dst)
        self.helper.unlink()
        for symbolic in [False, True]:
            if symbolic:
                self.helper.symlink_to('/bin/true')
            p = self.run_manager('uninstall')
            self.assert_failed(p)
            self.assertIn('Missing or unsafe HAL state probe', p.stderr)
            self.assertEqual(hashes(self.dst), original)
            self.assertFalse(self.previous())

    def test_quarantined_or_unsigned_helper_rejected(self):
        self.installed()
        self.env['FAIL_HELPER_VERIFY'] = '9'
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertFalse(self.previous())
        self.env['FAIL_HELPER_VERIFY'] = '0'
        (self.bin / 'xattr').write_text('#!/bin/bash\necho com.apple.quarantine\n')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('Refusing quarantined HAL state probe', p.stderr)
        self.assertFalse(self.previous())

    def test_failed_final_verification_rolls_back(self):
        p = self.run_manager('install', self.src, fail_verify=3)
        self.assert_failed(p)
        self.assertFalse(self.dst.exists())
        self.assertEqual([hashes(b) for b in self.failed()], [hashes(self.src)])

    def test_failed_recovery_copy_rescues_original_without_publishing_partial(self):
        self.installed()
        original = hashes(self.dst)
        (self.bin / 'ditto').write_text('''#!/bin/bash
case "$2" in */recovery.driver)
    mkdir "$2"; echo partial > "$2/marker"
    echo 'ditto: No space left on device' >&2; exit 1;; esac
cp -R "$1" "$2"
''')
        p = self.run_manager('uninstall', fail_restart=1)
        self.assert_failed(p)
        self.assertEqual(hashes(self.dst), original)
        self.assertFalse(self.previous())
        self.assertIn('original rescued by rename', p.stderr)
        self.assertIn('backup consumed', p.stderr)
        self.assertIn('state restored', p.stderr)
        partial = list((self.library / 'Audio/.NivoxAudio-history').glob('*/recovery.driver/marker'))
        self.assertEqual([p.read_text().strip() for p in partial], ['partial'])

    def test_failed_recovery_copy_publication_rescues_original(self):
        self.installed()
        original = hashes(self.dst)
        move = self.bin / 'mv'
        move.write_text('''#!/bin/bash
case "$1" in */recovery.driver) exit 8;; esac
exec /bin/mv "$@"
''')
        move.chmod(0o755)
        p = self.run_manager('uninstall', fail_restart=1)
        self.assert_failed(p)
        self.assertEqual(hashes(self.dst), original)
        self.assertFalse(self.previous())
        self.assertIn('original rescued by rename', p.stderr)

    def test_failed_rescue_keeps_original_and_marks_unverified(self):
        self.installed()
        original = hashes(self.dst)
        move = self.bin / 'mv'
        move.write_text('''#!/bin/bash
case "$1" in */recovery.driver|*/previous.driver) exit 8;; esac
exec /bin/mv "$@"
''')
        move.chmod(0o755)
        p = self.run_manager('uninstall', fail_restart=1)
        self.assert_failed(p)
        self.assertFalse(self.dst.exists())
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertIn('RECOVERY UNVERIFIED', p.stderr)
        self.assertNotIn('state restored', p.stderr)

    def timeout_then_state_helper(self, final_rc=None):
        # Two timeouts followed by the real sandbox filesystem-state check.
        body = self.helper.read_text()
        injection = '''
n=0; [ ! -e "$STATE/timeout-count" ] || n=$(cat "$STATE/timeout-count")
n=$((n+1)); echo "$n" > "$STATE/timeout-count"
if [ "$n" -le 2 ]; then echo 'injected transient timeout' >&2; exit 124; fi
'''
        if final_rc is not None:
            injection += f'exit {final_rc}\n'
        self.helper.write_text(body.replace('n=$(cat "$STATE/restarts")',
                                           injection + '\nn=$(cat "$STATE/restarts")', 1))

    def test_transient_probe_timeouts_require_eventual_state_for_install_remove_restore(self):
        self.timeout_then_state_helper()
        for mode in ['install', 'uninstall', 'restore']:
            with self.subTest(mode=mode):
                # Each subcase has independent bytes/backup prerequisites, so
                # a historical install failure cannot invalidate restore setup.
                shutil.rmtree(self.dst, ignore_errors=True)
                history = self.library / 'Audio/.NivoxAudio-history'
                shutil.rmtree(history, ignore_errors=True)
                (self.base / 'timeout-count').unlink(missing_ok=True)
                (self.base / 'probes').unlink(missing_ok=True)
                source = []
                if mode == 'install':
                    source = [self.src]
                elif mode == 'uninstall':
                    shutil.copytree(self.src, self.dst)
                else:
                    backup = history / 'transaction.fixture/previous.driver'
                    shutil.copytree(self.src, backup)
                    source = [backup]
                p = self.run_manager(mode, *source)
                self.assertEqual(p.returncode, 0, p.stderr)
                expected = 'absent' if mode == 'uninstall' else 'present'
                self.assertEqual((self.base / 'probes').read_text().splitlines(), [expected] * 3)
                self.assertEqual(hashes(self.dst), None if mode == 'uninstall' else hashes(self.src))
                self.assertFalse((self.hal / '.NivoxAudio.lock').exists())
                txn = sorted((self.library / 'Audio/.NivoxAudio-history').glob('transaction.*'),
                             key=lambda p: p.stat().st_mtime_ns)[-1]
                self.assertEqual((txn / 'transaction-hal-state.log').read_text().count('injected transient timeout'), 2)

    def test_timeout_followed_by_missing_hal_is_not_success(self):
        self.timeout_then_state_helper(final_rc=1)
        p = self.run_manager('install', self.src)
        self.assert_failed(p)
        self.assertFalse(self.dst.exists())
        self.assertIn('HAL state verification timed out (expected present)', p.stderr)
        self.assertIn(hashes(self.src), [hashes(b) for b in self.failed()])

    def shorten_phase_budget(self, seconds):
        text = self.script.read_text()
        self.assertIn('deadline=$((SECONDS+40))', text)
        self.script.write_text(text.replace('deadline=$((SECONDS+40))', f'deadline=$((SECONDS+{seconds}))'))

    def blocking_timeout_helper(self, recovery_also=False):
        # Real alarm/exit124 consumes the budget received from the actual shell.
        self.helper.write_text(f'''#!{sys.executable}
import os, signal, sys
from pathlib import Path
state = Path(os.environ['STATE'])
with (state / 'probes').open('a') as log: log.write(sys.argv[1] + '\\n')
with (state / 'budgets').open('a') as log: log.write(sys.argv[2] + '\\n')
if {recovery_also!r} or (state / 'restarts').read_text() == '1':
    signal.signal(signal.SIGALRM, lambda *_: sys.exit(124))
    signal.alarm(int(sys.argv[2]))
    while True: signal.pause()
''')

    def test_permanent_timeout_exhausts_phase_budget_then_restores_bytes(self):
        self.installed()
        original = hashes(self.dst)
        self.shorten_phase_budget(3)
        self.blocking_timeout_helper()
        start = time.monotonic()
        p = self.run_manager('uninstall')
        elapsed = time.monotonic() - start
        self.assert_failed(p)
        self.assertGreaterEqual(elapsed, 2)
        self.assertLess(elapsed, 5)
        self.assertEqual((self.base / 'probes').read_text().splitlines(), ['absent', 'present'])
        self.assertEqual((self.base / 'budgets').read_text().splitlines(), ['3', '3'])
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertIn('state restored', p.stderr)

    def test_permanent_recovery_timeout_has_separate_bounded_budget_and_stays_unverified(self):
        self.installed()
        original = hashes(self.dst)
        self.shorten_phase_budget(2)
        self.blocking_timeout_helper(recovery_also=True)
        start = time.monotonic()
        p = self.run_manager('uninstall')
        elapsed = time.monotonic() - start
        self.assert_failed(p)
        self.assertGreaterEqual(elapsed, 3)
        self.assertLess(elapsed, 6)
        self.assertEqual((self.base / 'probes').read_text().splitlines(), ['absent', 'present'])
        self.assertEqual((self.base / 'budgets').read_text().splitlines(), ['2', '2'])
        self.assertEqual(hashes(self.dst), original)
        self.assertIn('RECOVERY UNVERIFIED', p.stderr)
        self.assertNotIn('state restored', p.stderr)

    def test_full_40_second_budget_never_restarts_after_timeouts(self):
        self.installed()
        original = hashes(self.dst)
        self.blocking_timeout_helper()
        # Real polling intervals exercise a final probe with less than five
        # seconds left, not just repeated immediate rc124 fixtures.
        (self.bin / 'sleep').write_text('#!/bin/bash\nexec /bin/sleep "$@"\n')
        start = time.monotonic()
        p = subprocess.run([BASH, str(self.script), 'uninstall'], env=self.env,
                           text=True, capture_output=True, timeout=44)
        elapsed = time.monotonic() - start
        self.record(p, ['uninstall', 'full-40-second-budget'], elapsed_seconds=elapsed)
        self.assert_failed(p)
        self.assertGreaterEqual(elapsed, 39)
        self.assertLess(elapsed, 42)
        budgets = list(map(int, (self.base / 'budgets').read_text().splitlines()))
        self.assertTrue(all(1 <= b <= 5 for b in budgets))
        self.assertEqual(budgets[0], 5)
        self.assertTrue(any(b < 5 for b in budgets[:-1]), budgets)
        self.assertEqual(hashes(self.dst), original)
        self.assertIn('state restored', p.stderr)

    def test_success_after_deadline_is_rejected(self):
        self.installed()
        self.shorten_phase_budget(1)
        self.helper.write_text('''#!/bin/bash
printf '%s\\n' "$1" >> "$STATE/probes"
if [ "$(cat "$STATE/restarts")" = 1 ]; then /bin/sleep 1.1; fi
exit 0
''')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('HAL state verification timed out', p.stderr)
        self.assertIn('state restored', p.stderr)

    def test_unexpected_probe_error_is_not_retried_or_accepted(self):
        self.installed()
        self.helper.write_text('''#!/bin/bash
printf '%s\\n' "$1" >> "$STATE/probes"
if [ "$(cat "$STATE/restarts")" = 1 ]; then exit 126; fi
exit 0
''')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertEqual(p.returncode, 126)
        self.assertIn('HAL probe failed (exit 126)', p.stderr)
        self.assertEqual((self.base / 'probes').read_text().splitlines(), ['absent', 'present'])
        self.assertIn('state restored', p.stderr)

    def test_elapsed_poll_budget_is_not_just_an_attempt_count(self):
        # Shorten only the sandbox budget, never add an environment override.
        text = self.script.read_text()
        self.assertIn('deadline=$((SECONDS+40))', text)
        self.script.write_text(text.replace('deadline=$((SECONDS+40))', 'deadline=$((SECONDS+1))'))
        self.installed()
        self.helper.write_text('''#!/bin/bash
printf '%s\\n' "$1" >> "$STATE/probes"
if [ "$(cat "$STATE/restarts")" = 1 ]; then /bin/sleep 0.25; exit 1; fi
exit 0
''')
        start = time.monotonic()
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('HAL state verification timed out', p.stderr)
        self.assertLess(time.monotonic() - start, 4)
        calls = (self.base / 'probes').read_text().splitlines()
        self.assertLess(calls.count('absent'), 10)
        self.assertEqual(calls[-1], 'present')

    def wait_marker(self, marker, process):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (self.base / marker).exists():
                return
            if process.poll() is not None:
                self.fail('manager exited before ' + marker)
            time.sleep(0.01)
        self.fail('manager did not reach ' + marker)

    def test_repeated_signals_during_recovery_copy_release_lock(self):
        # Parent-only and foreground-process-group delivery reproduce both audits.
        for sig in [signal.SIGHUP, signal.SIGINT, signal.SIGTERM]:
            for group in [False, True]:
                with self.subTest(signal=sig.name, group=group):
                    if not self.dst.exists():
                        self.installed()
                    original = hashes(self.dst)
                    (self.base / 'copy-started').unlink(missing_ok=True)
                    (self.bin / 'ditto').write_text('''#!/bin/bash
case "$2" in */recovery.driver) touch "$STATE/copy-started"; /bin/sleep 0.25;; esac
cp -R "$1" "$2"
''')
                    (self.base / 'restarts').unlink(missing_ok=True)
                    p = subprocess.Popen([BASH, str(self.script), 'uninstall'],
                        env=dict(self.env, FAIL_RESTART='1'), text=True,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                    try:
                        self.wait_marker('copy-started', p)
                        for _ in range(2):
                            if group:
                                os.killpg(p.pid, sig)
                            else:
                                p.send_signal(sig)
                            time.sleep(0.03)
                        out, err = p.communicate(timeout=10)
                    finally:
                        if p.poll() is None:
                            os.killpg(p.pid, signal.SIGKILL)
                            p.communicate()
                    result = subprocess.CompletedProcess(p.args, p.returncode, out, err)
                    self.record(result, ['uninstall', sig.name, str(group)])
                    self.assert_failed(result)
                    self.assertEqual(hashes(self.dst), original)
                    self.assertIn('state restored', err)
                    self.assertIn('Transaction failed; preserved evidence', err)

    def test_second_ctrl_c_during_recovery_probe_does_not_abort_cleanup(self):
        self.installed()
        original = hashes(self.dst)
        # No shell/child-launch race between readiness and a sleeping command.
        # The first probe is interruptible before publishing readiness; recovery
        # inherits SIG_IGN and cannot finish until the test releases its FIFO.
        gate = self.base / 'probe-release'
        os.mkfifo(gate)
        self.helper.write_text(f'''#!{sys.executable}
import os
from pathlib import Path
import signal
state = Path(os.environ['STATE'])
n = (state / 'restarts').read_text()
attempt = state / ('timeout-' + n)
if not attempt.exists():
    attempt.touch()
    raise SystemExit(124)
if n == '1':
    signal.signal(signal.SIGINT, signal.SIG_DFL)
(state / ('probe-' + n)).touch()
with (state / 'probe-release').open('rb', buffering=0) as gate:
    gate.read(1)
''')
        fd = os.open(gate, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        p = subprocess.Popen([BASH, str(self.script), 'uninstall'], env=self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            self.wait_marker('probe-1', p)
            os.killpg(p.pid, signal.SIGINT)
            self.wait_marker('probe-2', p)
            os.killpg(p.pid, signal.SIGINT)
            os.write(fd, b'x')
            out, err = p.communicate(timeout=10)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
        result = subprocess.CompletedProcess(p.args, p.returncode, out, err)
        self.record(result, ['uninstall', 'double-Ctrl-C'])
        self.assert_failed(result)
        self.assertEqual(p.returncode, 130)
        self.assertEqual(hashes(self.dst), original)
        self.assertIn('state restored', err)

    def test_X1_signal_before_first_cleanup_command_still_recovers(self):
        # Widen the entry window only in the sandbox, BEFORE any protection.
        # A signal handler that merely exits skips the already-running EXIT trap.
        text = self.script.read_text()
        self.assertIn('finish() {\n', text)
        self.script.write_text(text.replace('finish() {\n', '''finish() {
    if [[ ! -e "$STATE/finish-entered" ]]; then
        touch "$STATE/finish-entered"; /bin/sleep 0.3
    fi
''', 1))
        self.installed()
        original = hashes(self.dst)
        for sig in [signal.SIGHUP, signal.SIGINT, signal.SIGTERM]:
            with self.subTest(signal=sig.name):
                # Reset only sandbox residue from a failed historical subcase;
                # its lock/bytes/counters were already recorded before assertions.
                lock = self.hal / '.NivoxAudio.lock'
                if lock.exists():
                    lock.rmdir()
                if not self.dst.exists():
                    self.installed()
                for name in ['finish-entered', 'restarts', 'probes']:
                    (self.base / name).unlink(missing_ok=True)
                p = subprocess.Popen([BASH, str(self.script), 'uninstall'],
                    env=dict(self.env, FAIL_RESTART='1'), text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    self.wait_marker('finish-entered', p)
                    p.send_signal(sig)
                    out, err = p.communicate(timeout=10)
                finally:
                    if p.poll() is None:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.communicate()
                result = self.record(subprocess.CompletedProcess(p.args, p.returncode, out, err),
                                     ['uninstall', 'cleanup-entry', sig.name])
                self.assert_failed(result)
                self.assertEqual(p.returncode, 130)
                self.assertEqual(hashes(self.dst), original)
                self.assertEqual((self.base / 'restarts').read_text(), '2')
                self.assertEqual((self.base / 'probes').read_text().splitlines(), ['present'])
                self.assertIn('state restored', err)

    def test_X1_signal_burst_at_unmodified_cleanup_entry(self):
        self.installed()
        original = hashes(self.dst)
        restart = self.bin / 'killall'
        restart.write_text('''#!/bin/bash
n=0; [ ! -e "$STATE/restarts" ] || n=$(cat "$STATE/restarts")
n=$((n+1)); printf '%s' "$n" > "$STATE/restarts"
if [ "$n" = 1 ]; then
    ( for ((i=0; i<400; i++)); do kill -TERM "$PPID" 2>/dev/null || exit 0; done ) &
    exit 7
fi
''')
        # Reproduce the original unmodified-script counterexperiment, not only
        # the deterministic widened window. No scheduler can exhaust all races.
        for attempt in range(150):
            for name in ['restarts', 'probes']:
                (self.base / name).unlink(missing_ok=True)
            result = self.run_manager('uninstall')
            self.assert_failed(result)
            self.assertEqual(hashes(self.dst), original, attempt)
            self.assertEqual((self.base / 'restarts').read_text(), '2', attempt)
            self.assertEqual((self.base / 'probes').read_text().splitlines(), ['present'], attempt)
            self.assertIn('state restored', result.stderr)

    def check_broken_stderr_recovery(self, broken_pipe):
        self.installed()
        original = hashes(self.dst)
        move = self.bin / 'mv'
        move.write_text('''#!/bin/bash
if [ "${RESCUE:-0}" = 1 ]; then
    case "$1" in */recovery.driver) exit 8;; esac
fi
exec /bin/mv "$@"
''')
        move.chmod(0o755)
        # Both normal copy and rescue must restart + probe after a failed write.
        # Also preserve negative restart/probe/rescue outcomes (never success).
        for case in ['copy', 'rescue', 'restart-fails', 'probe-fails', 'rescue-fails']:
            with self.subTest(output='pipe' if broken_pipe else 'full', case=case):
                # Isolate historical failures too; a stale lock must not turn the
                # next rescue counterprobe into an unrelated preflight rejection.
                lock = self.hal / '.NivoxAudio.lock'
                if lock.exists():
                    lock.rmdir()
                for name in ['restarts', 'probes']:
                    (self.base / name).unlink(missing_ok=True)
                if not self.dst.exists():
                    self.installed()
                if case == 'rescue-fails':
                    move.write_text('''#!/bin/bash
case "$1" in */recovery.driver|*/previous.driver) exit 8;; esac
exec /bin/mv "$@"
''')
                env = dict(self.env, RESCUE='1' if case == 'rescue' else '0',
                           FAIL_RESTART='all' if case == 'restart-fails' else '1',
                           FAIL_LOAD='all' if case == 'probe-fails' else '0')
                if broken_pipe:
                    reader, writer = os.pipe()
                    os.close(reader)
                    output = os.fdopen(writer, 'w')
                else:
                    output = open('/dev/full', 'w')
                with output:
                    result = subprocess.run([BASH, str(self.script), 'uninstall'],
                        env=env, text=True, stdout=subprocess.PIPE, stderr=output, timeout=15)
                # stderr is intentionally unavailable; do not invent diagnostics.
                result.stderr = ''
                self.record(result, ['uninstall', 'broken-stderr', str(broken_pipe), case])
                self.assert_failed(result)
                self.assertGreater(result.returncode, 0)  # not death by SIGPIPE
                txn = sorted((self.library / 'Audio/.NivoxAudio-history').glob('transaction.*'),
                             key=lambda p: p.stat().st_mtime_ns)[-1]
                if case == 'rescue-fails':
                    self.assertFalse(self.dst.exists())
                    self.assertEqual(hashes(txn / 'previous.driver'), original)
                    self.assertEqual((self.base / 'restarts').read_text(), '1')
                    self.assertFalse((self.base / 'probes').exists())
                else:
                    self.assertEqual(hashes(self.dst), original)
                    self.assertEqual((self.base / 'restarts').read_text(), '2')
                    if case == 'restart-fails':
                        self.assertFalse((self.base / 'probes').exists())
                    else:
                        calls = (self.base / 'probes').read_text().splitlines()
                        self.assertTrue(calls)
                        self.assertEqual(set(calls), {'present'})
                        self.assertTrue((txn / 'recovery-hal-state.log').exists())
                        if case == 'probe-fails':
                            self.assertIn('injected HAL query/state failure',
                                          (txn / 'recovery-hal-state.log').read_text())
                    self.assertEqual(hashes(txn / 'recovery.driver'),
                                     original if case == 'rescue' else None)
                    self.assertEqual(hashes(txn / 'previous.driver'),
                                     None if case == 'rescue' else original)

    @unittest.skipUnless(Path('/dev/full').exists(), '/dev/full is a Linux fixture')
    def test_X2b_stderr_full_does_not_abort_rescue_restart_or_unlock(self):
        self.check_broken_stderr_recovery(broken_pipe=False)

    def test_X4b_stderr_epipe_does_not_abort_recovery_or_unlock(self):
        self.check_broken_stderr_recovery(broken_pipe=True)

    def test_platform_and_root_guards_remain_closed(self):
        self.installed()
        original = hashes(self.dst)
        (self.bin / 'uname').write_text('#!/bin/bash\necho Linux\n')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('Requires Apple Silicon macOS', p.stderr)
        (self.bin / 'uname').write_text('#!/bin/bash\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n')
        (self.bin / 'id').write_text('#!/bin/bash\necho 501\n')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('Run with sudo', p.stderr)
        self.assertEqual(hashes(self.dst), original)

    def test_source_and_installed_symlinks_are_rejected(self):
        link = self.base / 'source-link.driver'
        link.symlink_to(self.src, target_is_directory=True)
        p = self.run_manager('install', link)
        self.assert_failed(p)
        self.assertIn('Missing or unsafe source bundle', p.stderr)
        self.assertFalse(self.dst.exists())
        self.installed()
        original = hashes(self.dst)
        self.dst.rename(self.base / 'retained.driver')
        self.dst.symlink_to(self.base / 'retained.driver', target_is_directory=True)
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('Unsafe installed bundle', p.stderr)
        self.assertEqual(hashes(self.base / 'retained.driver'), original)

    def test_unmanaged_restore_source_is_rejected(self):
        p = self.run_manager('restore', self.src)
        self.assert_failed(p)
        self.assertIn('Not a managed backup', p.stderr)
        self.assertFalse(self.previous())

    def test_quarantine_rejected_without_removing_attributes(self):
        (self.src / 'quarantine').touch()
        p = self.run_manager('install', self.src)
        self.assert_failed(p)
        self.assertIn('Refusing quarantined', p.stderr)
        self.assertFalse(self.dst.exists())
        self.assertTrue((self.src / 'quarantine').exists())

    def test_signals_immediately_after_lock_creation_do_not_orphan_it(self):
        # Signal from inside mkdir, after the real creation but before it returns.
        # This deterministically covers the former pre-trap window, no sleeps.
        mkdir = self.bin / 'mkdir'
        mkdir.write_text('''#!/bin/bash
/bin/mkdir "$@" || exit $?
case "$1" in */.NivoxAudio.lock)
    kill -s "$LOCK_SIGNAL" "$PPID"
    printf '%s' "$LOCK_SIGNAL" > "$STATE/lock-signal-sent";; esac
''')
        mkdir.chmod(0o755)
        for sig in ['HUP', 'INT', 'TERM', 'PIPE']:
            with self.subTest(signal=sig):
                # Isolate historical counterprobe failures after recording them.
                lock = self.hal / '.NivoxAudio.lock'
                if lock.exists():
                    lock.rmdir()
                if not self.dst.exists():
                    self.installed()
                original = hashes(self.dst)
                self.env['LOCK_SIGNAL'] = sig
                p = self.run_manager('uninstall')
                self.assertEqual((self.base / 'lock-signal-sent').read_text(), sig)
                self.assertFalse((self.hal / '.NivoxAudio.lock').exists())
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn('Completed uninstall', p.stdout)
                self.assertFalse(self.dst.exists())
                self.assertEqual(hashes(self.previous()[-1]), original)
                self.assertEqual((self.base / 'restarts').read_text(), '1')
                self.assertEqual((self.base / 'probes').read_text().splitlines()[-1], 'absent')

    def test_lock_rejects_parallel_transaction(self):
        self.installed()
        original = hashes(self.dst)
        (self.hal / '.NivoxAudio.lock').mkdir()
        p = self.run_manager('uninstall')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Another transaction or stale lock', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertTrue((self.hal / '.NivoxAudio.lock').exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--bash', default=BASH, help='Shell running the manager (e.g. Bash 3.2)')
    parser.add_argument('--manager-source', type=Path, default=SCRIPT,
                        help='Test-only source override for historical counterprobes')
    args, remaining = parser.parse_known_args()
    SCRIPT = args.manager_source.resolve()
    BASH = str(Path(shutil.which(args.bash) or args.bash).resolve())
    bash_version = subprocess.run([BASH, '--version'], check=True, capture_output=True,
                                  text=True).stdout.splitlines()[0]
    suite = unittest.main(argv=[sys.argv[0], *remaining], verbosity=2, exit=False)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(dict(
            scope='Sandbox; substituted OS/HAL commands, NOT native acceptance',
            bash=BASH, bash_version=bash_version,
            manager_sha256=hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            tests=suite.result.testsRun, failures=len(suite.result.failures),
            errors=len(suite.result.errors), records=RECORDS), indent=2) + '\n')
    sys.exit(not suite.result.wasSuccessful())
