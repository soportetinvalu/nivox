#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""OCR production SwiftUI with real HAL; does not request/change TCC or open audio."""
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
    report = {'ok': False, 'scope': 'production call view and tabbed root hosted in test process + real HAL + pixel OCR; not packaged app launch or click automation',
              'driver': args.driver, 'commands': [], 'input_hashes': {}}
    for name in ['App/NoiseProbeApp.swift', 'App/CallAudio.swift', 'ci/CallAudioUISmoke.swift',
                 'tests/test_call_ui_mac.py', 'tests/fixtures/call-ui-transparent-41701415863.png']:
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
            # Compile the actual PNG loader in isolation against AppKit, before
            # linking the UI. No invented NSBitmapImageRep declaration or stub.
            smoke = (ROOT / 'ci/CallAudioUISmoke.swift').read_text()
            start = '    @MainActor static func snapshotFixture(_ fixture: URL) -> CGImage? {\n'
            assert smoke.count(start) == 1
            method = start + smoke.split(start, 1)[1].split('\n    }', 1)[0] + '\n    }'
            assert smoke.count('guard let raw = snapshotFixture(fixture) else {') == 1
            def fixture_program(loader):
                return '''import AppKit
@main struct SnapshotFixtureRegression {
''' + loader + '''
    @MainActor static func main() {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let fixture = root.appendingPathComponent("tests/fixtures/call-ui-transparent-41701415863.png")
        guard let image = snapshotFixture(fixture) else { fatalError("genuine PNG was not decoded") }
        precondition(image.width == 780 && image.height == 560, "wrong fixture geometry")
        precondition(image.alphaInfo != .none && image.alphaInfo != .noneSkipLast && image.alphaInfo != .noneSkipFirst,
                     "genuine fixture lost transparency")
        precondition(snapshotFixture(fixture.appendingPathComponent("missing.png")) == nil,
                     "missing fixture was accepted")
        precondition(snapshotFixture(root.appendingPathComponent("ci/CallAudioUISmoke.swift")) == nil,
                     "non-image data was accepted")
        print("snapshot fixture cases passed: 3")
    }
}
'''
            fixture_source, fixture_binary = out / 'SnapshotFixture.swift', out / 'snapshot-fixture'
            fixture_source.write_text(fixture_program(method))
            fixture_flags = ['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library',
                             '-warnings-as-errors', '-target', 'arm64-apple-macos13.0', '-sdk', sdk]
            run([*fixture_flags, fixture_source, '-framework', 'AppKit', '-o', fixture_binary])
            result = run([fixture_binary])
            assert result.stdout.strip() == 'snapshot fixture cases passed: 3', result.stdout
            # Reproduce the original API error, not just any compiler failure.
            original = '''    @MainActor static func snapshotFixture(_ fixture: URL) -> CGImage? {
        guard let bitmap = NSBitmapImageRep(contentsOf: fixture) else { return nil }
        return bitmap.cgImage
    }'''
            original_source = out / 'SnapshotFixture-original.swift'
            original_source.write_text(fixture_program(original))
            rejected = run([*fixture_flags, original_source, '-framework', 'AppKit',
                            '-o', out / 'snapshot-fixture-original'], expect=False)
            assert rejected.returncode != 0 and 'argument passed to call that takes no arguments' in rejected.stderr, rejected.stderr
            report.update(snapshot_fixture_cases_passed=3, original_fixture_initializer_rejected=True)
            def compile_view(view_source, executable):
                run(['xcrun', 'swiftc', '-swift-version', '5', '-parse-as-library', '-Onone',
                 '-target', 'arm64-apple-macos13.0', '-sdk', sdk, '-I', ROOT / 'CNoise/include',
                 '-I', ROOT / 'callaudio/control', generated, view_source,
                 ROOT / 'ci/CallAudioUISmoke.swift', *libs,
                 '-framework', 'CoreAudio', '-framework', 'AudioToolbox', '-framework', 'CoreFoundation',
                 '-framework', 'SwiftUI', '-framework', 'AppKit', '-framework', 'AVFoundation',
                 '-framework', 'Vision', '-o', executable])
                run(['codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', ROOT / 'App/Entitlements.plist', executable])
            # Keep production hardened runtime/audio-input entitlement. No TCC overrides.
            compile_view(ROOT / 'App/CallAudio.swift', binary)
            run([binary, out, args.driver])
            runtime = json.loads((out / 'runtime.json').read_text())
            assert runtime['ok'] and runtime['phase'] == args.driver
            assert runtime['opaque_capture_selftests_passed'] and runtime['original_capture_regression_passed']
            # Counterprobe: same binary must specifically reject the opposite HAL phase.
            negative = out / 'counterprobe'
            negative.mkdir()
            proc = run([binary, negative, 'present' if args.driver == 'absent' else 'absent'], expect=False)
            assert proc.returncode == 1 and 'real HAL driver presence disagrees with phase' in proc.stderr, proc.stderr
            assert not (negative / 'runtime.json').exists()
            report.update(opposite_driver_phase_rejected=True, runtime=runtime)
            # Real layout regression, not mocked OCR: rebuild a disposable copy
            # forcing the notices to a single truncated line. Same renderer/oracle.
            mutant_source = out / 'CallAudio-truncated.swift'
            view_source = (ROOT / 'App/CallAudio.swift').read_text()
            marker = '.fixedSize(horizontal: false, vertical: true)'
            assert view_source.count(marker) >= 7
            mutant_source.write_text(view_source.replace(marker, '.lineLimit(1)'))
            mutant_binary = out / 'ui-smoke-truncated'
            compile_view(mutant_source, mutant_binary)
            truncated = out / 'truncation-counterprobe'
            truncated.mkdir()
            proc = run([mutant_binary, truncated, args.driver], expect=False)
            assert proc.returncode == 1 and 'significant text not visible' in proc.stderr, proc.stderr
            failure = json.loads((truncated / 'call-780x560-text.json').read_text())
            assert not failure['ok'] and failure['missing_spans']
            report.update(ok=True, real_truncation_rejected=True,
                          png_sha256={str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(out.rglob('*.png'))})
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
