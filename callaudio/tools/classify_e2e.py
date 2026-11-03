#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Diagnose an E2E failure without changing its assertions, samples or exit code."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def classify(directory, exit_code=None):
    directory = Path(directory)
    explicit_exit_code = exit_code is not None
    issues, inputs, captures = [], {}, []

    def read(path):
        data = path.read_bytes()
        inputs[str(path.relative_to(directory))] = hashlib.sha256(data).hexdigest()
        return data

    try:
        source = json.loads(read(directory / 'e2e.json'))
        if not isinstance(source, dict):
            raise ValueError('expected an object')
    except (OSError, ValueError) as exc:
        source = {}
        issues.append(f'e2e.json: {exc}')
    if exit_code is None:
        try:
            exit_code = int(read(directory / 'e2e.exitcode'))
        except (OSError, ValueError) as exc:
            issues.append(f'e2e.exitcode: {exc}')
    try:
        lines = read(directory / 'coreaudio.log').decode(errors='replace').splitlines()
        overloads = [line for line in lines if 'Audio IO Overload thread:' in line
                     or 'Overload possibly due to safety violation' in line]
    except OSError as exc:
        overloads = []
        issues.append(f'coreaudio.log: {exc}')
    # Keep stderr as well as the JSON. An early crash can leave invalid JSON.
    if (directory / 'e2e.stderr').exists():
        read(directory / 'e2e.stderr')
    for path in sorted((directory / 'e2e-captures').glob('capture-*.json')):
        try:
            meta = json.loads(read(path))
            csv_path = path.with_suffix('.csv')
            rows = list(csv.DictReader(read(csv_path).decode().splitlines()))
            previous, jumps, maximum = None, 0, 0.0
            for row in rows:
                if int(row['flags']) & 1:  # kAudioTimeStampSampleTimeValid
                    sample = float(row['sample_time'])
                    if previous is not None:
                        delta = abs(sample - previous)
                        jumps += delta > 0.5
                        maximum = max(maximum, delta)
                    previous = sample + int(row['frames'])
                else:
                    previous = None
            read(path.with_suffix('.f32'))  # Trace the unfiltered capture, too.
            captures.append({'file': str(path.relative_to(directory)),
                             'generator_timestamp_jumps': int(meta['generator_timestamp_jumps']),
                             'generator_max_timestamp_jump': meta['generator_max_timestamp_jump'],
                             'capture_timestamp_jumps': jumps, 'capture_max_timestamp_jump': maximum})
        except (OSError, ValueError, KeyError) as exc:
            issues.append(f'{path.name}: {exc}')
    expected = {f'e2e-captures/capture-{i:02d}.json' for i in range(1, 8)}
    if {c['file'] for c in captures} != expected:
        issues.append(f'expected unmodified captures 01..07, found {[c["file"] for c in captures]}')
    e2e_passed = (exit_code == 0 and source.get('ok') is True and source.get('failures') == 0)
    failed = exit_code not in (None, 0) or source.get('ok') is False
    jumps = sum(c['generator_timestamp_jumps'] + c['capture_timestamp_jumps'] for c in captures)
    if e2e_passed:
        category = 'passed' if not issues else 'incomplete_diagnostics'
    elif failed and overloads and jumps:
        category = 'failed_with_host_overload_evidence'
    elif failed:
        category = 'failed_without_confirmed_host_overload'
    else:
        category = 'incomplete_e2e'
    return {'ok': e2e_passed and not issues, 'e2e_passed': e2e_passed,
            'classification': category, 'original_exit_code': exit_code,
            'source_directory': str(directory.resolve()),
            'exit_code_source': 'explicit historical RC argument' if explicit_exit_code else 'e2e.exitcode',
            'original_ok': source.get('ok'), 'original_failures': source.get('failures'),
            'original_notes': source.get('notes'), 'root_cause_proven': False,
            'interpretation': 'Overload logs and timestamp discontinuities are diagnostic evidence, not proof of cause or a waiver of glitches. No retry, filtering or threshold change.',
            'coreaudio_overload_lines': overloads, 'captures': captures,
            'diagnostic_issues': issues, 'input_sha256': inputs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--exit-code', type=int, help='Explicit original RC for historical evidence only')
    args = parser.parse_args()
    report = classify(args.directory, args.exit_code)
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    args.report.write_text(text)
    print(text, end='')
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    main()
