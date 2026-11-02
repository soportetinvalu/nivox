#!/usr/bin/env python3
"""Exercise the real transaction shell in a sandbox, not a macOS/HAL test.

Only OS commands and the /Library root are substituted. Every mutation occurs
inside build/; production scripts have no environment-variable path overrides.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'callaudio/installer/manage-driver.sh'


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
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'])
        commands = {
            'uname': 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi',
            'id': 'echo 0',
            'stat': 'echo 0:700',
            'xattr': 'if [ -e "$2/quarantine" ]; then echo com.apple.quarantine; fi',
            'ditto': 'cp -R "$1" "$2"',
            'chown': ':',
            'killall': ':',
            'codesign': '''n=0
[ ! -e "$STATE/count" ] || n=$(cat "$STATE/count")
n=$((n+1)); printf '%s' "$n" > "$STATE/count"
[ "$n" != "${FAIL_VERIFY:-0}" ]''',
        }
        self.env['STATE'] = str(self.base)
        text = SCRIPT.read_text().replace('/Library', str(self.library))
        for cmd, body in commands.items():
            target = self.bin / cmd
            target.write_text('#!/bin/bash\nset -eu\n' + body + '\n')
            target.chmod(0o755)
            for prefix in ['/usr/bin/', '/usr/sbin/']:
                text = text.replace(prefix + cmd, str(target))
        self.script = self.base / 'manager.sh'
        self.script.write_text(text)
        self.src = self.base / 'source.driver'
        self.src.mkdir()
        (self.src / 'marker').write_text('candidate')

    def tearDown(self):
        self.tmp.cleanup()

    def run_manager(self, *args, fail_verify=0):
        return subprocess.run(['bash', str(self.script), *map(str, args)],
                              env=dict(self.env, FAIL_VERIFY=str(fail_verify)),
                              text=True, capture_output=True)

    def previous(self):
        return list((self.library / 'Audio/.NivoxAudio-history').glob('transaction.*/previous.driver'))

    def installed(self):
        self.dst.mkdir()
        (self.dst / 'marker').write_text('original')

    def test_replace_and_restore_retain_both_versions(self):
        self.installed()
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

    def test_failed_final_verification_rolls_back(self):
        self.installed()
        p = self.run_manager('install', self.src, fail_verify=3)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual((self.dst / 'marker').read_text(), 'original')
        self.assertTrue(list((self.library / 'Audio/.NivoxAudio-history').glob('*/failed.driver')))
        self.assertFalse((self.hal / '.NivoxAudio.lock').exists())

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
    unittest.main(verbosity=2)
