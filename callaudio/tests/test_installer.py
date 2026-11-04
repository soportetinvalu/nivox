#!/usr/bin/env python3
"""Exercise the real transaction shell in a sandbox, not a macOS/HAL test.

Only OS commands and the /Library root are substituted. Every mutation occurs
inside build/; production scripts have no environment-variable path overrides.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'callaudio/installer/manage-driver.sh'
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
        self.env = dict(PATH=str(self.bin) + ':/usr/bin:/bin', LC_ALL='C')
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
        self.env['STATE'] = str(self.base)
        text = SCRIPT.read_text().replace('/Library', str(self.library))
        # Only the sandbox copy gains a test PATH; never an override in production.
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

    def run_manager(self, *args, fail_verify=0, fail_restart: int | str = 0, fail_load: int | str = 0):
        # Count driver signature checks separately from the new helper checks.
        (self.base / 'count').unlink(missing_ok=True)
        (self.base / 'restarts').unlink(missing_ok=True)
        p = subprocess.run(['bash', str(self.script), *map(str, args)],
                           env=dict(self.env, FAIL_VERIFY=str(fail_verify),
                                    FAIL_RESTART=str(fail_restart), FAIL_LOAD=str(fail_load)),
                           text=True, capture_output=True, timeout=15)
        RECORDS.append(dict(test=self.id(), args=list(map(str, args)), rc=p.returncode,
                            stdout=p.stdout, stderr=p.stderr, installed=hashes(self.dst),
                            previous={str(b): hashes(b) for b in self.previous()},
                            failed={str(b): hashes(b) for b in self.failed()}))
        return p

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

    def test_replace_and_restore_retain_both_versions(self):
        self.installed()
        original, candidate = hashes(self.dst), hashes(self.src)
        self.assertNotEqual(original, candidate)
        p = self.run_manager('install', self.src)
        self.assertEqual(p.returncode, 0, p.stderr)
        backup = self.previous()[0]
        self.assertEqual((backup / 'marker').read_text(), 'original')
        self.assertEqual((self.dst / 'marker').read_text(), 'candidate')
        p = self.run_manager('restore', backup)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.dst / 'marker').read_text(), 'original')
        self.assertTrue(backup.exists())
        self.assertIn('candidate', [(p / 'marker').read_text() for p in self.previous()])
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual(hashes(backup), original)
        displaced = next(b for b in self.previous() if hashes(b) == candidate)
        p = self.run_manager('restore', displaced)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(hashes(self.dst), candidate)
        self.assertEqual(hashes(displaced), candidate)
        self.assertIn(original, [hashes(b) for b in self.previous()])

    def test_restart_failure_rolls_back_and_never_completes(self):
        self.installed()
        original, candidate = hashes(self.dst), hashes(self.src)
        p = self.run_manager('install', self.src, fail_restart=1)
        self.assert_failed(p)
        self.assertEqual(p.returncode, 7)
        self.assertIn('CoreAudio restart failed (exit 7)', p.stderr)
        self.assertIn('Previous filesystem and HAL presence state restored', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertEqual([hashes(b) for b in self.failed()], [candidate])

    def test_load_failure_rolls_back_and_retains_failure_log(self):
        self.installed()
        original, candidate = hashes(self.dst), hashes(self.src)
        p = self.run_manager('install', self.src, fail_load=1)
        self.assert_failed(p)
        self.assertIn('HAL state verification timed out (expected present)', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertEqual([hashes(b) for b in self.failed()], [candidate])
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

    def test_recovery_restart_failure_is_explicit_and_preserves_bundles(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('install', self.src, fail_restart='all')
        self.assert_failed(p)
        self.assertIn('RECOVERY UNVERIFIED', p.stderr)
        self.assertNotIn('state restored', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertEqual([hashes(b) for b in self.failed()], [hashes(self.src)])

    def test_recovery_load_failure_is_not_reported_as_restored(self):
        self.installed()
        p = self.run_manager('install', self.src, fail_load='all')
        self.assert_failed(p)
        self.assertIn('RECOVERY UNVERIFIED', p.stderr)
        self.assertNotIn('state restored', p.stderr)

    def test_failed_uninstall_restores_previous_and_keeps_backup(self):
        self.installed()
        original = hashes(self.dst)
        p = self.run_manager('uninstall', fail_load=1)
        self.assert_failed(p)
        self.assertIn('expected absent', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertEqual([hashes(b) for b in self.previous()], [original])

    def test_failed_restore_preserves_both_versions_and_source(self):
        self.installed()
        original, candidate = hashes(self.dst), hashes(self.src)
        p = self.run_manager('install', self.src)
        self.assertEqual(p.returncode, 0, p.stderr)
        backup = self.previous()[0]
        p = self.run_manager('restore', backup, fail_restart=1)
        self.assert_failed(p)
        self.assertEqual(hashes(self.dst), candidate)
        self.assertEqual(hashes(backup), original)
        self.assertIn(candidate, [hashes(b) for b in self.previous()])
        self.assertEqual([hashes(b) for b in self.failed()], [original])

    def test_missing_or_symbolic_helper_rejected_without_displacement(self):
        self.installed()
        original = hashes(self.dst)
        self.helper.unlink()
        for symbolic in [False, True]:
            if symbolic:
                self.helper.symlink_to('/bin/true')
            p = self.run_manager('install', self.src)
            self.assert_failed(p)
            self.assertIn('Missing or unsafe HAL state probe', p.stderr)
            self.assertEqual(hashes(self.dst), original)
            self.assertFalse(self.previous())

    def test_quarantined_or_unsigned_helper_rejected(self):
        self.installed()
        self.env['FAIL_HELPER_VERIFY'] = '9'
        p = self.run_manager('install', self.src)
        self.assert_failed(p)
        self.assertFalse(self.previous())
        self.env['FAIL_HELPER_VERIFY'] = '0'
        (self.bin / 'xattr').write_text('#!/bin/bash\necho com.apple.quarantine\n')
        p = self.run_manager('uninstall')
        self.assert_failed(p)
        self.assertIn('Refusing quarantined HAL state probe', p.stderr)
        self.assertFalse(self.previous())

    def test_failed_final_verification_rolls_back(self):
        self.installed()
        p = self.run_manager('install', self.src, fail_verify=3)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.dst / 'marker').read_text(), 'original')
        self.assertTrue(list((self.library / 'Audio/.NivoxAudio-history').glob('*/failed.driver')))
        self.assertFalse((self.hal / '.NivoxAudio.lock').exists())

    def test_failed_filesystem_recovery_preserves_original_backup(self):
        self.installed()
        original = hashes(self.dst)
        move = self.bin / 'mv'
        move.write_text('''#!/bin/bash
case "$1" in */recovery.driver) exit 8;; esac
exec /bin/mv "$@"
''')
        move.chmod(0o755)
        p = self.run_manager('install', self.src, fail_restart=1)
        self.assert_failed(p)
        self.assertIn('RECOVERY UNVERIFIED', p.stderr)
        self.assertEqual([hashes(b) for b in self.previous()], [original])
        self.assertEqual([hashes(b) for b in self.failed()], [hashes(self.src)])

    def test_platform_and_root_guards_remain_closed(self):
        self.installed()
        original = hashes(self.dst)
        (self.bin / 'uname').write_text('#!/bin/bash\necho Linux\n')
        p = self.run_manager('install', self.src)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Requires Apple Silicon macOS', p.stderr)
        (self.bin / 'uname').write_text('#!/bin/bash\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n')
        (self.bin / 'id').write_text('#!/bin/bash\necho 501\n')
        p = self.run_manager('install', self.src)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Run with sudo', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.assertFalse(self.previous())

    def test_source_and_installed_symlinks_are_rejected(self):
        self.installed()
        original = hashes(self.dst)
        link = self.base / 'source-link.driver'
        link.symlink_to(self.src, target_is_directory=True)
        p = self.run_manager('install', link)
        self.assert_failed(p)
        self.assertIn('Missing or unsafe source bundle', p.stderr)
        self.assertEqual(hashes(self.dst), original)
        self.dst.rename(self.base / 'retained.driver')
        self.dst.symlink_to(self.base / 'retained.driver', target_is_directory=True)
        p = self.run_manager('install', self.src)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Unsafe installed bundle', p.stderr)
        self.assertEqual(hashes(self.base / 'retained.driver'), original)

    def test_unmanaged_restore_source_is_rejected(self):
        self.installed()
        p = self.run_manager('restore', self.src)
        self.assert_failed(p)
        self.assertIn('Not a managed backup', p.stderr)
        self.assertFalse(self.previous())

    def test_quarantine_rejected_without_mutation(self):
        self.installed()
        (self.src / 'quarantine').touch()
        p = self.run_manager('install', self.src)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('Refusing quarantined', p.stderr)
        self.assertEqual((self.dst / 'marker').read_text(), 'original')
        self.assertTrue((self.src / 'quarantine').exists())
        self.assertFalse(self.previous())

    def test_uninstall_retains_recoverable_bundle(self):
        self.installed()
        p = self.run_manager('uninstall')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(self.dst.exists())
        self.assertEqual((self.previous()[0] / 'marker').read_text(), 'original')

    def test_lock_rejects_parallel_transaction(self):
        self.installed()
        (self.hal / '.NivoxAudio.lock').mkdir()
        p = self.run_manager('install', self.src)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.dst / 'marker').read_text(), 'original')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--manager-source', type=Path, default=SCRIPT,
                        help='Test-only source override for historical counterprobes')
    args, remaining = parser.parse_known_args()
    SCRIPT = args.manager_source.resolve()
    suite = unittest.main(argv=[sys.argv[0], *remaining], verbosity=2, exit=False)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(dict(
            scope='Linux sandbox; substituted OS/HAL commands, NOT native acceptance',
            manager_sha256=hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            tests=suite.result.testsRun, failures=len(suite.result.failures),
            errors=len(suite.result.errors), records=RECORDS), indent=2) + '\n')
    sys.exit(not suite.result.wasSuccessful())
