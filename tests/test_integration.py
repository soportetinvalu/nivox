#!/usr/bin/env python3
"""Pruebas del adaptador np_* frente al motor real cn_*, más CLI y contrapruebas."""
# SPDX-License-Identifier: MIT
import argparse
import ctypes as c
import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from christian_noise import Engine, read_wav, write_wav


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--library', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    lib = c.CDLL(str(args.library.resolve()))
    pointer = c.POINTER(c.c_float)
    lib.np_create.argtypes = [c.c_uint, c.c_uint, c.c_size_t]
    lib.np_create.restype = c.c_void_p
    lib.np_push.argtypes = [c.c_void_p, pointer, c.c_size_t]
    lib.np_push.restype = c.c_int
    for name in ['np_pump', 'np_finish', 'np_error']:
        fn = getattr(lib, name); fn.argtypes = [c.c_void_p]; fn.restype = c.c_int
    for name in ['np_close', 'np_destroy', 'np_abort']:
        fn = getattr(lib, name); fn.argtypes = [c.c_void_p]; fn.restype = None
    lib.np_count.argtypes = [c.c_void_p]; lib.np_count.restype = c.c_size_t
    for name in ['np_raw', 'np_clean']:
        fn = getattr(lib, name); fn.argtypes = [c.c_void_p]; fn.restype = pointer
    cases = []
    def capture(x, block):
        s = lib.np_create(48000, 1, len(x)); assert s
        try:
            assert lib.np_count(s) == 0
            for pos in range(0, len(x), block):
                chunk = x[pos:pos + block]; a = (c.c_float * len(chunk))(*chunk)
                assert lib.np_push(s, a, len(chunk)) in (0, 1)
                assert lib.np_pump(s) == 0
            lib.np_close(s); assert lib.np_finish(s) == 0
            assert lib.np_count(s) == len(x)
            raw = list(lib.np_raw(s)[:len(x)])
            assert raw == list((c.c_float * len(x))(*x))
            y = list(lib.np_clean(s)[:len(x)])
            assert all(math.isfinite(v) and abs(v) <= 1 for v in y)
            return y
        finally:
            lib.np_destroy(s)
    def check_output(actual, reference):
        assert len(actual) == len(reference)
        assert max(abs(a - b) for a, b in zip(actual, reference)) < 1e-7
        assert sum(v*v for v in actual) > 0
    # Odd frame tails, one-sample recordings, full 15 s and variable tap sizes.
    for n in [1, 479, 480, 481, 1001, 15007, 720000]:
        x = [0.12*math.cos(i*0.061) for i in range(n)]
        with Engine(1, library=args.library) as e:
            expected = e.offline(x, 127)
        for block in [127, 1024]:
            got = capture(x, block)
            check_output(got, expected)
            cases.append({'frames': n, 'block': block, 'exact_engine_match': True})
    impulse = [0.0]*3000; impulse[32] = 0.5
    impulse_peaks = []
    for strength in [0, 0.5, 1]:
        with Engine(strength, library=args.library) as e:
            continuous = e.process(impulse)
        peak = max(range(len(continuous)), key=lambda i: abs(continuous[i]))
        assert peak == 32 + 960
        impulse_peaks.append({'strength': strength, 'peak': peak, 'expected': 992})
    # A muted output or wrong crop must fail the same integration oracle.
    rejected = []
    for name, broken in [('muted', [0.0]*len(expected)), ('wrong_delay', [0.0]*480 + expected[:-480])]:
        try:
            check_output(broken, expected)
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError('Countercheck accepted: ' + name)
    r = random.Random(73)
    x = [r.uniform(-0.1, 0.1) for _ in range(48000)]
    y = capture(x, 127)
    attenuation = 10*math.log10(sum(v*v for v in x[14400:]) / sum(v*v for v in y[14400:]))
    assert attenuation > 6
    assert lib.np_create(44100, 1, 100) is None
    assert lib.np_create(48000, 2, 100) is None
    s = lib.np_create(48000, 1, 100)
    assert s
    lib.np_abort(s); assert lib.np_error(s) == -1
    lib.np_close(s); assert lib.np_finish(s) == -1
    lib.np_destroy(s)
    source, destination = args.out / 'synthetic-input.wav', args.out / 'synthetic-output.wav'
    write_wav(source, x)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    cmd = [sys.executable, ROOT / 'python/christian_noise.py', source, destination, '--library', args.library, '--block', '127']
    cli = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr
    actual = read_wav(destination)
    with Engine(1, library=args.library) as e:
        expected = e.offline(read_wav(source), 127)
    assert len(actual) == len(x)
    assert max(abs(a-b) for a,b in zip(actual,expected)) <= 1/32768
    destination_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
    repeat = subprocess.run(list(map(str,cmd)), capture_output=True, text=True)
    assert repeat.returncode != 0 and 'Output already exists' in repeat.stderr
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == destination_sha
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    report = {'ok': True, 'engine': 'SpeexDSP 1.2.1', 'cases': cases,
              'counterchecks_rejected': rejected, 'invalid_rate_channels_abort_rejected': True,
              'np_white_noise_attenuation_db': attenuation, 'cli_exact_quantized_engine_match': True,
              'cli_overwrite_rejected': True, 'cli_result': json.loads(cli.stdout),
              'impulse_peaks': impulse_peaks, 'microphone_tested': False,
              'note': 'Portable C/CLI test; Mac compilation is recorded separately by build.py.'}
    # Build.py records the actual host and Mac compilation separately.
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
