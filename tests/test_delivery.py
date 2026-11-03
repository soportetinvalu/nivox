#!/usr/bin/env python3
"""Identidad, icono, guardas y parser: no sustituyen una compilación Apple."""
# SPDX-License-Identifier: MIT
import argparse
import hashlib

import json
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci import icon_assets, export_sources
from ci.call_audio_build import verify_audio_entitlement
import build as recipe
OUT: Path = ROOT / 'build/uninitialized-test-output'


class DeliveryTests(unittest.TestCase):
    def fixture(self, name):
        p = OUT / name
        p.mkdir()
        return p

    def reject(self, command, marker, absent=None):
        p = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(marker, p.stdout + p.stderr)
        if absent:
            self.assertFalse(absent.exists())

    def test_original_icon_hash_and_slots(self):
        result = icon_assets.validate_assets()
        self.assertEqual(result['source_sha256'], 'c493e3898244e0b27fe0e5ce4d4215c1908fc83421314c892cdd3ed8b8955a2e')
        self.assertEqual(result['icon_sha256'], '9511557b16733f118e58aee0c6a6f4c4796de26549a57a08c5ac70be2881c2c5')
        self.assertEqual(result['representations'], 11)
        self.assertEqual(icon_assets.png_size((ROOT/'App/Assets/Nivox-original.png').read_bytes()), (1254,1254))

    def test_modified_icon_rejected(self):
        f = self.fixture('modified-icon')
        shutil.copytree(ROOT/'App/Assets', f/'assets')
        p = f/'assets/Nivox.icns'
        p.write_bytes(p.read_bytes()[:-1] + b'!')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            icon_assets.validate_assets(f/'assets')

    def test_modified_original_rejected(self):
        f = self.fixture('modified-original')
        shutil.copytree(ROOT/'App/Assets', f/'assets')
        p = f/'assets/Nivox-original.png'
        p.write_bytes(b'not-the-original')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            icon_assets.validate_assets(f/'assets')

    def test_truncated_icon_rejected(self):
        with self.assertRaises(ValueError):
            icon_assets.icns_slots((ROOT/'App/Assets/Nivox.icns').read_bytes()[:-1])

    def test_missing_slot_rejected(self):
        data = (ROOT/'App/Assets/Nivox.icns').read_bytes()
        first_size = struct.unpack('>I', data[12:16])[0]
        altered = b'icns' + struct.pack('>I', len(data)-first_size) + data[8+first_size:]
        with self.assertRaisesRegex(ValueError, 'Incomplete ICNS'):
            icon_assets.icns_slots(altered)

    def test_png_crc_rejected(self):
        data = bytearray((ROOT/'App/Assets/Nivox-original.png').read_bytes())
        data[29] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC mismatch'):
            icon_assets.png_size(bytes(data))

    def test_metadata_and_active_engine(self):
        info = plistlib.loads((ROOT/'App/Info.plist').read_bytes())
        for k in ['CFBundleName','CFBundleDisplayName','CFBundleExecutable']:
            self.assertEqual(info[k], 'Nivox')
        self.assertEqual(info['CFBundleIdentifier'], 'local.christian.nivox.speex.experimental')
        self.assertEqual(info['CFBundleIconFile'], 'Nivox.icns')
        self.assertIn('SpeexDSP', info['NSMicrophoneUsageDescription'])
        self.assertEqual(plistlib.loads((ROOT/'App/Entitlements.plist').read_bytes()),
                         {'com.apple.security.device.audio-input': True})
        swift = (ROOT/'App/NoiseProbeApp.swift').read_text()
        self.assertIn('@MainActor struct NivoxApp', swift)
        self.assertIn('SpeexDSP 1.2.1', swift)
        self.assertNotIn('CI_NO_MODEL', swift)
        self.assertNotIn('RNNoise', swift)
        self.assertIn('NP_DELAY=960', (ROOT/'CNoise/include/NoiseProbe.h').read_text())
        self.assertIn('#include "christian_noise.h"', (ROOT/'CNoise/NoiseProbe.c').read_text())
        self.assertEqual(hashlib.sha256((ROOT/'vendor/speexdsp-1.2.1.tar.gz').read_bytes()).hexdigest(), recipe.SHA)

    def test_outside_output_rejected(self):
        p = ROOT/'forbidden-output'
        self.reject([sys.executable,ROOT/'build.py','--target','linux','--out',p], 'Output must be inside', p)

    def test_existing_output_rejected(self):
        self.reject([sys.executable,ROOT/'build.py','--target','linux','--out',OUT], 'Output exists')

    @unittest.skipIf(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Real Mac not a negative case')
    def test_mac_cannot_be_simulated(self):
        p = OUT/'forbidden-mac'
        self.reject([sys.executable,ROOT/'build.py','--target','macapp','--out',p], 'actual Darwin arm64', p)

    def test_corrupted_upstream_rejected(self):
        f = self.fixture('bad-upstream')
        (f/'vendor').mkdir()
        (f/'ci').mkdir()
        shutil.copyfile(ROOT/'build.py', f/'build.py')
        shutil.copyfile(ROOT/'ci/icon_assets.py', f/'ci/icon_assets.py')
        shutil.copyfile(ROOT/'ci/call_audio_build.py', f/'ci/call_audio_build.py')
        (f/'vendor/speexdsp-1.2.1.tar.gz').write_bytes(b'corrupted')
        p = f/'build/forbidden'
        self.reject([sys.executable,f/'build.py','--target','linux','--out',p], 'SHA-256 mismatch', p)

    def test_public_allowlist_positive(self):
        payload = export_sources.collect_payload()
        self.assertEqual(set(payload), set(export_sources.FILES))
        self.assertIn('App/Assets/Nivox-original.png', payload)
        self.assertIn('vendor/speexdsp-1.2.1.tar.gz', payload)
        self.assertFalse(any('build' in Path(p).parts or '.git' in Path(p).parts or '.env' in Path(p).parts for p in payload))

    def test_safe_call_audio_export_complete(self):
        payload = export_sources.collect_payload()
        required = {'callaudio/core/nv_watch.h', 'callaudio/core/nv_signal.h',
                    'callaudio/mac/audio-input.entitlements', 'callaudio/control/nv_call_ui.h',
                    'callaudio/installer/manage-driver.sh', 'callaudio/installer/restore-driver.sh',
                    'callaudio/tests/test_installer.py', 'callaudio/tests/test_mac_clock.c',
                    'callaudio/tests/test_call_mac_contract.c', 'tests/test_call_lifecycle_swift.py'}
        self.assertTrue(required <= set(payload))

    def test_source_manifest_matches_payload(self):
        payload = export_sources.collect_payload()
        manifest = json.loads((ROOT / 'source-manifest.json').read_text())
        self.assertEqual(manifest, {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()})

    def test_signed_entitlement_parser_positive_and_negative(self):
        # Only the parser is tested here, not codesign or Apple's signature trust.
        target = Path('synthetic-entitlement-fixture')
        def fixture(ent):
            return lambda cmd: 'diagnostic header\n' + plistlib.dumps(ent).decode()
        self.assertTrue(verify_audio_entitlement(fixture({'com.apple.security.device.audio-input': True}), target))
        for ent in [{}, {'com.apple.security.device.audio-input': False},
                    {'com.apple.security.device.audio-input': 1},
                    {'com.apple.security.device.audio-input': True, 'com.apple.security.cs.disable-library-validation': True}]:
            with self.subTest(ent=ent), self.assertRaises(RuntimeError):
                verify_audio_entitlement(fixture(ent), target)
        for text in ['codesign succeeded', 'com.apple.security.device.audio-input=true', '<?xml invalid </plist>']:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                verify_audio_entitlement(lambda cmd: text, target)

    def test_symbolic_allowlist_file_rejected(self):
        f = self.fixture('symbolic-file')
        (f/'real').write_text('fixture')
        (f/'link').symlink_to(f/'real')
        with patch.object(export_sources,'HERE',f), patch.object(export_sources,'FILES',['link']):
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                export_sources.collect_payload()

    def test_symbolic_parent_rejected(self):
        f = self.fixture('symbolic-parent')
        (f/'real').mkdir()
        (f/'real/file').write_text('fixture')
        (f/'link').symlink_to(f/'real',target_is_directory=True)
        with patch.object(export_sources,'HERE',f), patch.object(export_sources,'FILES',['link/file']):
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                export_sources.collect_payload()

    def test_export_outside_rejected(self):
        p = ROOT/'forbidden-export'
        self.reject([sys.executable,ROOT/'ci/export_sources.py','--out',p], 'NEW directory', p)

    def test_export_existing_rejected(self):
        self.reject([sys.executable,ROOT/'ci/export_sources.py','--out',OUT], 'NEW directory')


class AuditParserTests(unittest.TestCase):
    def audit(self, arch='arm64', dependency='/usr/lib/libSystem.B.dylib', rpath=''):
        loads = '' if not rpath else 'cmd LC_RPATH\ncmdsize 40\npath ' + rpath + ' (offset 12)\n'
        # Synthetic fixtures test only parser rejection, not Apple tools on Linux.
        with patch.object(recipe.subprocess, 'check_output', side_effect=[
                arch + '\n', 'test:\n\t' + dependency + ' (compatibility version 1.0.0)\n', loads]):
            return recipe.audit_macho(Path('synthetic-parser-fixture'))

    def test_system_dependency(self):
        self.assertEqual(self.audit()['architecture'], 'arm64')

    def test_reject_intel_and_universal(self):
        for arch in ['x86_64', 'x86_64 arm64', '']:
            with self.subTest(arch=arch), self.assertRaises(RuntimeError):
                self.audit(arch=arch)

    def test_reject_homebrew_dependency(self):
        with self.assertRaises(RuntimeError):
            self.audit(dependency='/opt/homebrew/lib/libfoo.dylib')

    def test_system_swift_rpath(self):
        self.audit(dependency='@rpath/libswiftCore.dylib', rpath='/usr/lib/swift')

    def test_reject_unresolved_or_arbitrary_rpath(self):
        for dep,rp in [('@rpath/libswiftCore.dylib',''),('@rpath/libfoo.dylib','/usr/lib/swift'),
                       ('@rpath/libswiftCore.dylib','/opt/homebrew/lib')]:
            with self.subTest(dep=dep,rpath=rp), self.assertRaises(RuntimeError):
                self.audit(dependency=dep,rpath=rp)


def main():
    global OUT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    OUT = args.out.resolve()
    if (ROOT/'build').resolve() not in OUT.parents or OUT.exists():
        raise SystemExit('New output under delivery/build/ required')
    OUT.mkdir(parents=True)
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in [DeliveryTests,AuditParserTests]])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'ok':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),
              'errors':len(result.errors),'skipped':len(result.skipped),'mac_compiled':False,
              'parser_uses_synthetic_fixtures':True}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    main()
