#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Regresiones del WAV exportado y de la ruta C real; señales SINTÉTICAS, no voz."""
import argparse
import ctypes as c
import json
import math
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from ab_wav import load_wav, channel_metrics
from christian_noise import Engine, read_wav, write_wav
from diagnose_ab import analyze

OUT: Path
LIBRARY: Path
BASELINE: Path | None = None
METRICS = {}


def float_wav(path, samples, rate=48000, channels=1, extensible=False, encoding=3):
    fmt = struct.pack('<HHIIHH', 0xfffe if extensible else encoding,
                      channels, rate, rate*channels*4, channels*4, 32)
    if extensible:
        fmt += struct.pack('<HHI', 22, 32, 0) + struct.pack('<I', encoding) + bytes.fromhex('00001000800000aa00389b71')
    data = struct.pack('<' + str(len(samples)) + 'f', *samples)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
    path.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
    return path


def capture(samples):
    lib = c.CDLL(str(LIBRARY))
    p = c.POINTER(c.c_float)
    lib.np_create.argtypes = [c.c_uint, c.c_uint, c.c_size_t]
    lib.np_create.restype = c.c_void_p
    lib.np_push.argtypes = [c.c_void_p, p, c.c_size_t]
    lib.np_push.restype = c.c_int
    for name in ('np_pump', 'np_finish'):
        getattr(lib, name).argtypes = [c.c_void_p]
        getattr(lib, name).restype = c.c_int
    for name in ('np_close', 'np_destroy'):
        getattr(lib, name).argtypes = [c.c_void_p]
        getattr(lib, name).restype = None
    for name in ('np_raw', 'np_clean'):
        getattr(lib, name).argtypes = [c.c_void_p]
        getattr(lib, name).restype = p
    handle = lib.np_create(48000, 1, len(samples))
    assert handle
    try:
        for i in range(0, len(samples), 1024):
            block = samples[i:i+1024]
            assert lib.np_push(handle, (c.c_float*len(block))(*block), len(block)) in (0, 1)
            assert lib.np_pump(handle) == 0
        lib.np_close(handle)
        assert lib.np_finish(handle) == 0
        return (list(lib.np_raw(handle)[:len(samples)]), list(lib.np_clean(handle)[:len(samples)]))
    finally:
        lib.np_destroy(handle)


class Tests(unittest.TestCase):
    def test_float32_export_format_preserved(self):
        expected = [0, 0.123456, -0.7654321, 1, -1]
        exact = list(struct.unpack('<5f', struct.pack('<5f', *expected)))
        for extended in (False, True):
            path = float_wav(OUT / ('float-ext.wav' if extended else 'float.wav'), expected, extensible=extended)
            self.assertEqual(read_wav(path), exact)
            self.assertEqual(load_wav(path)['encoding'], 'IEEE_FLOAT32')
        if BASELINE:
            command = [sys.executable, '-B', '-c',
                       'import sys; sys.path.insert(0,sys.argv[1]); from christian_noise import read_wav; read_wav(sys.argv[2])',
                       str(BASELINE / 'python'), str(OUT / 'float.wav')]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('unknown format: 3', result.stderr)
            METRICS['baseline_float32_failure'] = {'returncode': result.returncode, 'stderr': result.stderr}

    def test_float32_cli_matches_real_engine(self):
        source = float_wav(OUT / 'float-cli-source.wav',
                           [.2*math.sin(2*math.pi*440*i/48000) for i in range(4800)])
        destination = OUT / 'float-cli-output.wav'
        before = source.read_bytes()
        command = [sys.executable, '-B', str(ROOT / 'python/christian_noise.py'),
                   str(source), str(destination), '--library', str(LIBRARY)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.read_bytes(), before)
        with Engine(library=LIBRARY) as engine:
            reference = engine.offline(read_wav(source))
        actual = read_wav(destination)
        self.assertEqual(len(actual), len(reference))
        self.assertGreater(sum(v*v for v in reference), .01)
        self.assertLessEqual(max(abs(a-b) for a,b in zip(actual, reference)), 1/32768)
        output_before = destination.read_bytes()
        rejected = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('Output already exists', rejected.stderr)
        self.assertEqual(destination.read_bytes(), output_before)

    def test_pcm16_exact_compatibility(self):
        path = OUT / 'pcm.wav'
        values = [-1, -.125, 0, .125, 32767/32768]
        write_wav(path, values)
        self.assertEqual(read_wav(path), values)

    def test_rate_and_stereo_not_silently_converted(self):
        for name, rate, channels in [('44100', 44100, 1), ('stereo', 48000, 2)]:
            path = float_wav(OUT / (name + '.wav'), [0]*20, rate=rate, channels=channels)
            self.assertEqual(load_wav(path)['sample_rate'], rate)
            with self.assertRaisesRegex(ValueError, 'mono, 48000'):
                read_wav(path)

    def test_nonfinite_and_out_of_range_rejected(self):
        for i, value in enumerate((math.nan, math.inf, -math.inf, 1.01)):
            path = float_wav(OUT / ('invalid-' + str(i) + '.wav'), [value])
            with self.assertRaises(ValueError):
                read_wav(path)

    def test_truncated_and_malformed_rejected(self):
        path = float_wav(OUT / 'truncated.wav', [.25]*5)
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            load_wav(path)
        path = float_wav(OUT / 'alignment.wav', [0, 0])
        malformed = bytearray(path.read_bytes())
        struct.pack_into('<H', malformed, 32, 8)
        path.write_bytes(malformed)
        with self.assertRaisesRegex(ValueError, 'alignment'):
            load_wav(path)
        path = float_wav(OUT / 'unknown-encoding.wav', [0], encoding=6)
        with self.assertRaisesRegex(ValueError, 'PCM16 or IEEE Float32'):
            load_wav(path)

    def test_raw_is_exact_and_no_hum_from_silence(self):
        for name, source in [('silence', [0.0]*48000),
                             ('hum-dc', [.04+.3*math.sin(2*math.pi*60*i/48000) for i in range(48000)])]:
            raw, wet = capture(source)
            expected = list((c.c_float*len(source))(*source))
            self.assertEqual(raw, expected)
            if name == 'silence':
                self.assertTrue(all(x == 0 for x in raw + wet))
            else:
                METRICS['hum_dc_raw'] = channel_metrics(raw, 48000)
                METRICS['hum_dc_clean'] = channel_metrics(wet, 48000)
            float_wav(OUT / (name + '-raw.wav'), raw)
            float_wav(OUT / (name + '-clean.wav'), wet)

    def test_first_channel_can_exclude_signal_on_second(self):
        # Reproduce la selección fija buffer.floatChannelData?[0] de Swift.
        # El segundo tono NO es habla; no afirma que el Mac de Christian sea estéreo.
        n = 48000
        hum = [.3*math.sin(2*math.pi*60*i/48000) for i in range(n)]
        signal = [.15*math.sin(2*math.pi*997*i/48000) for i in range(n)]
        stereo = [v for pair in zip(hum, signal) for v in pair]
        path = float_wav(OUT / 'two-channels.wav', stereo, channels=2)
        channels = load_wav(path)['samples']
        raw1, _ = capture(channels[0])
        raw2, _ = capture(channels[1])
        # Keep the exact sample oracle without unittest's quadratic list diff
        # on intentional 48,000-sample failures (notably Python 3.12).
        def assert_raw_exact(actual, expected):
            self.assertTrue(actual == expected, 'raw sample identity mismatch')
        assert_raw_exact(raw1, channels[0])
        assert_raw_exact(raw2, channels[1])
        self.assertGreater(max(abs(a-b) for a,b in zip(raw1, raw2)), .2)
        METRICS['channel_example'] = {'frames': n, 'original_uses_channel': 1, 'other_signal_on_channel': 2,
                                      'channel1': channel_metrics(raw1, 48000), 'channel2': channel_metrics(raw2, 48000)}
        # Mismo oráculo de identidad debe rechazar silencio y canal equivocado.
        rejected = []
        for name, broken in [('muted', [0.0]*n), ('wrong_channel', raw2)]:
            try:
                assert_raw_exact(broken, channels[0])
            except AssertionError:
                rejected.append(name)
        self.assertEqual(rejected, ['muted', 'wrong_channel'])
        METRICS['raw_oracle_counterchecks_rejected'] = rejected

    def test_level_and_spectral_oracle(self):
        n = 4800
        source = [.25*math.sin(2*math.pi*60*i/48000) for i in range(n)]
        measured = channel_metrics(source, 48000)
        expected = 20*math.log10(.25/math.sqrt(2))
        self.assertAlmostEqual(measured['rms_dbfs'], expected, places=8)
        self.assertAlmostEqual(measured['line_rms_dbfs_at_hz']['60'], expected, places=8)
        self.assertEqual(channel_metrics([0.0]*n, 48000)['rms_dbfs'], None)
        self.assertGreater(measured['line_rms_dbfs_at_hz']['60'], measured['line_rms_dbfs_at_hz']['50'] + 100)
        METRICS['playback_gain_0_35_db'] = 20*math.log10(.35)

    def test_diagnostic_cli_pair_and_reject_mismatch(self):
        path = float_wav(OUT / 'cli-float.wav', [.125]*4800)
        self.assertEqual(analyze(path)['encoding'], 'IEEE_FLOAT32')
        command = [sys.executable, '-B', str(ROOT / 'python/diagnose_ab.py'), str(path), str(path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['clean_minus_original_rms_db'], [0])
        other = float_wav(OUT / 'cli-other.wav', [0]*4800, rate=44100)
        result = subprocess.run(command[:-1] + [str(other)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('differ in rate/channels/frame count', result.stderr)
        result = subprocess.run(command + ['--start', 'nan'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Expected finite window', result.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--baseline-root', type=Path)
    args = parser.parse_args()
    OUT, LIBRARY, BASELINE = args.out, args.library.resolve(), args.baseline_root
    OUT.mkdir(parents=True, exist_ok=False)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    report = {'ok': result.wasSuccessful(), 'tests': result.testsRun,
              'failures': len(result.failures), 'errors': len(result.errors), 'metrics': METRICS,
              'scope': 'real C DSP on synthetic samples; no physical Mac/device/speaker-isolation test'}
    (OUT / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'metrics'}))
    sys.exit(not result.wasSuccessful())
