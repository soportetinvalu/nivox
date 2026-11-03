#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render production SwiftUI with real HAL; never grants TCC or opens audio."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--driver', required=True, choices=['absent', 'present'])
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('BLOQUEADO: real Darwin arm64 GUI session required; no simulated fallback')
    build, out = args.build.resolve(), args.out.resolve()
    if (ROOT / 'build').resolve() not in out.parents or out.exists():
        raise SystemExit('New output under build/ required')
    app = build / 'app-build'
    libs = [app / 'libNivoxCallAudio.a', app / 'libNoiseProbe.a',
            app / 'speex-build/libspeexdsp/.libs/libspeexdsp.a']
    if not all(p.is_file() for p in libs):
        raise SystemExit('BLOQUEADO: complete production Mac app build required')
    out.mkdir(parents=True)
    report = {'ok': False, 'scope': 'production model/view hosted in test process + real HAL; not packaged app launch or click automation',
              'driver': args.driver, 'commands': [], 'input_hashes': {}}
    for name in ['App/NoiseProbeApp.swift', 'App/CallAudio.swift', 'ci/CallAudioUISmoke.swift', 'tests/test_call_ui_mac.py']:
        report['input_hashes'][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    # Preserve every production declaration/body; only relocate the entry point.
    source = (ROOT / 'App/NoiseProbeApp.swift').read_text()
    marker = '@main @MainActor struct NivoxApp: App {'
    assert source.count(marker) == 1
    generated = out / 'NoiseProbeApp.swift'
    generated.write_text(source.replace(marker, '@MainActor struct NivoxApp: App {'))
    with (out / 'commands.log').open('x') as log:
        def run(command, expect=True):
            command = list(map(str, command))
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
            log.write(repr(command) + '\n' + proc.stdout + proc.stderr + '\n')
            log.flush()
            report['commands'].append({'command': command, 'returncode': proc.returncode})
            if expect:
                proc.check_returncode()
            return proc
        try:
            sdk = run(['xcrun', '--sdk', 'macosx', '--show-sdk-path']).stdout.strip()
            binary = out / 'ui-smoke'
            run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library', '-Onone',
                 '-target', 'arm64-apple-macos13.0', '-sdk', sdk, '-I', ROOT / 'CNoise/include',
                 '-I', ROOT / 'callaudio/control', generated, ROOT / 'App/CallAudio.swift',
                 ROOT / 'ci/CallAudioUISmoke.swift', *libs,
                 '-framework', 'CoreAudio', '-framework', 'AudioToolbox', '-framework', 'CoreFoundation',
                 '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'AVFoundation', '-o', binary])
            # Keep production hardened runtime/audio-input entitlement. No TCC overrides.
            run(['codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', ROOT / 'App/Entitlements.plist', binary])
            run([binary, out, args.driver])
            runtime = json.loads((out / 'runtime.json').read_text())
            assert runtime['ok'] and runtime['phase'] == args.driver
            # Counterprobe: same binary must specifically reject the opposite HAL phase.
            negative = out / 'counterprobe'
            negative.mkdir()
            proc = run([binary, negative, 'present' if args.driver == 'absent' else 'absent'], expect=False)
            assert proc.returncode == 1 and 'real HAL driver presence disagrees with phase' in proc.stderr, proc.stderr
            assert not (negative / 'runtime.json').exists()
            report.update(ok=True, opposite_driver_phase_rejected=True, runtime=runtime,
                          png_sha256=hashlib.sha256((out / 'production-call-view.png').read_bytes()).hexdigest())
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
