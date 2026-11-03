#!/usr/bin/env python3
"""Pinned libASPL compatibility: real archive, fail-closed patch, C++ counterprobe.

The portable C++ fixture checks initializer order and values, not the Apple SDK.
Only Darwin CI can validate the complete driver and CoreAudio behavior.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from callaudio.tools.build_callaudio import ASPL, ASPL_SHA, create_package_stage, fix_aspl_stream_format, safe_extract


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        # Keep all fixtures within the repository; never install or access devices.
        base = ROOT / 'build/compat-regression-tmp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        safe_extract(ASPL, self.root, ASPL_SHA)
        self.aspl = self.root / 'libASPL-3.1.2'
        self.header = self.aspl / 'include/aspl/Stream.hpp'
        self.original = self.header.read_text()

    def test_exact_patch_preserves_values_and_other_bytes(self):
        result = fix_aspl_stream_format(self.aspl)
        patched = self.header.read_text()
        self.assertNotEqual(result['original_sha256'], result['patched_sha256'])
        pattern = r'(?<=    AudioStreamBasicDescription Format = \{).*?(?=    \};)'
        original_match = re.search(pattern, self.original, re.S)
        patched_match = re.search(pattern, patched, re.S)
        assert original_match is not None and patched_match is not None
        before = original_match.group()
        after = patched_match.group()
        self.assertEqual(sorted(before.splitlines()), sorted(after.splitlines()))
        self.assertEqual(re.sub(pattern, '', self.original, flags=re.S),
                         re.sub(pattern, '', patched, flags=re.S))
        self.assertEqual(re.findall(r'\.(m\w+)\s*=', after), [
            'mSampleRate', 'mFormatID', 'mFormatFlags', 'mBytesPerPacket',
            'mFramesPerPacket', 'mBytesPerFrame', 'mChannelsPerFrame', 'mBitsPerChannel'])
        with self.assertRaisesRegex(RuntimeError, 'refusing compatibility patch'):
            fix_aspl_stream_format(self.aspl)
        self.assertEqual(self.header.read_text(), patched)

    def test_changed_vendor_is_rejected_without_write(self):
        changed = self.original.replace('.mBitsPerChannel = 16,', '.mBitsPerChannel = 24,')
        self.header.write_text(changed)
        with self.assertRaisesRegex(RuntimeError, 'refusing compatibility patch'):
            fix_aspl_stream_format(self.aspl)
        self.assertEqual(self.header.read_text(), changed)

    def test_duplicate_initializer_is_rejected_without_write(self):
        changed = self.original + self.original
        self.header.write_text(changed)
        with self.assertRaisesRegex(RuntimeError, 'refusing compatibility patch'):
            fix_aspl_stream_format(self.aspl)
        self.assertEqual(self.header.read_text(), changed)

    def test_cpp_order_counterprobe_and_corrected_values(self):
        compiler = shutil.which(os.environ.get('CXX', 'c++'))
        assert compiler is not None, 'C++ compiler required for compatibility regression'
        # Stand-in declarations use ASBD field order. This is not an Apple SDK build.
        declarations = '''
struct AudioStreamBasicDescription {
    double mSampleRate;
    unsigned mFormatID, mFormatFlags, mBytesPerPacket, mFramesPerPacket;
    unsigned mBytesPerFrame, mChannelsPerFrame, mBitsPerChannel;
    unsigned mReserved = 0;
};
constexpr unsigned kAudioFormatLinearPCM = 1;
constexpr unsigned kAudioFormatFlagIsSignedInteger = 2;
constexpr unsigned kAudioFormatFlagsNativeEndian = 4;
constexpr unsigned kAudioFormatFlagIsPacked = 8;
'''
        def compile_initializer(text, tag):
            match = re.search(r'AudioStreamBasicDescription Format = \{.*?\n    \};', text, re.S)
            assert match is not None
            initializer = match.group()
            source = self.root / f'{tag}.cpp'
            source.write_text(declarations + '\nconstexpr ' + initializer + '''
static_assert(Format.mSampleRate == 44100);
static_assert(Format.mFormatID == kAudioFormatLinearPCM);
static_assert(Format.mFormatFlags == 14);
static_assert(Format.mBytesPerPacket == 4 && Format.mFramesPerPacket == 1);
static_assert(Format.mBytesPerFrame == 4 && Format.mChannelsPerFrame == 2);
static_assert(Format.mBitsPerChannel == 16 && Format.mReserved == 0);
int main() { return 0; }
''')
            return subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                                   '-fsyntax-only', str(source)], text=True, capture_output=True)
        old = compile_initializer(self.original, 'original')
        self.assertNotEqual(old.returncode, 0, 'counterprobe accepted the original disorder')
        self.assertRegex(old.stderr, r'(?i)(designator order|declaration order)')
        fix_aspl_stream_format(self.aspl)
        new = compile_initializer(self.header.read_text(), 'corrected')
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)


class PackagingTests(unittest.TestCase):
    def test_stage_preserves_cli_and_uses_casefold_distinct_namespace(self):
        base = ROOT / 'build/packaging-regression-tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            out = Path(directory)
            cli = out / 'nivox-callaudio'
            cli.write_bytes(b'packaging fixture, not a Mach-O binary')
            stage = create_package_stage(out)
            # Enforce the macOS path constraint even on case-sensitive Linux.
            self.assertNotEqual(stage.relative_to(out).parts[0].casefold(), cli.name.casefold(),
                                'package staging collides with CLI on case-insensitive macOS')
            self.assertEqual(stage.name, 'Nivox-CallAudio')
            self.assertTrue(stage.is_dir())
            self.assertEqual(cli.read_bytes(), b'packaging fixture, not a Mach-O binary')
            marker = stage / 'retained.txt'
            marker.write_text('retain existing staging; never merge or overwrite it')
            with self.assertRaises(FileExistsError):
                create_package_stage(out)
            self.assertEqual(marker.read_text(), 'retain existing staging; never merge or overwrite it')
            self.assertEqual(cli.read_bytes(), b'packaging fixture, not a Mach-O binary')


if __name__ == '__main__':
    unittest.main(verbosity=2)
