#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Production raw-tone generator/analyzer, synthetic transport; not native HAL.

Compile release + ASan/UBSan, then demand that fixed-tail and last-nonzero
mutants fail the same C regression. No SDK, threshold changes or filtering.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / 'callaudio/mac/nv_e2e_tone.h'
GENERATOR = ROOT / 'callaudio/mac/nv_e2e.c'
TEST = ROOT / 'callaudio/tests/test_e2e_tone.c'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT / 'build/e2e-tone-regression')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = GENERATOR.read_text()
    generator = source[source.index('enum { GEN_SINE'):source.index('typedef struct {\n    float *buf;')]
    (out / 'generator.inc').write_text(generator)
    cc = shutil.which('cc') or 'cc'
    report = {'scope': 'synthetic transport; production generator/analyzer; not native Mac acceptance',
              'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [HEADER, GENERATOR, TEST]}, 'runs': []}
    log = out / 'commands.log'

    def run(cmd):
        p = subprocess.run([str(x) for x in cmd], text=True, capture_output=True, timeout=60)
        with log.open('a') as f:
            f.write('$ ' + ' '.join(map(str, cmd)) + '\n' + p.stdout + p.stderr + f'\nRC={p.returncode}\n')
        return p

    def build(name, header_dir, flags):
        exe = out / name
        p = run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', *flags,
                 '-I' + str(header_dir), '-I' + str(out), TEST, '-lm', '-o', exe])
        if p.returncode:
            raise RuntimeError('compilation failed: ' + p.stderr)
        return exe

    for name, flags in [('release', ['-O2']), ('asan-ubsan', ['-O1', '-g', '-fsanitize=address,undefined',
                                        '-fno-omit-frame-pointer', '-fno-sanitize-recover=all'])]:
        p = run([build(name, HEADER.parent, flags)])
        if p.returncode:
            raise RuntimeError(f'{name} failed: {p.stdout}{p.stderr}')
        result = json.loads(p.stdout)
        assert result['ok'] and result['positives'] > 0 and result['negatives'] > 0
        report['runs'].append({'name': name, 'returncode': p.returncode, 'result': result})

    header = HEADER.read_text()
    endpoint = 'a.end = a.marker_index + (size_t)generated;'
    assert header.count(endpoint) == 1
    mutants = {
        'fixed-tail': (header.replace(endpoint, 'a.end = len > 4800 ? len - 4800 : 0;'), 'a.start == onset + 24000 && a.end == end'),
        'last-nonzero': (header.replace(endpoint, 'a.end = len; while (a.end && buf[a.end - 1] == 0) a.end--;'), 'FAIL negative real zero cut incl terminal'),
    }
    for name, (mutated, expected) in mutants.items():
        directory = out / name
        directory.mkdir()
        (directory / HEADER.name).write_text(mutated)
        p = run([build(name + '-probe', directory, ['-O2'])])
        if p.returncode != 1 or expected not in p.stderr:
            raise RuntimeError(f'{name} not rejected by intended oracle: {p.returncode} {p.stdout} {p.stderr}')
        report['runs'].append({'name': name, 'returncode': p.returncode, 'expected_failure': expected,
                               'stderr': p.stderr, 'rejected': True})
    report['ok'] = True
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
