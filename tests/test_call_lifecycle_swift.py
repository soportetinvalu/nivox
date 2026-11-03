#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile the production lifecycle method against the real C module, then run it.

Requires a real Swift compiler (Linux or Darwin). Extracts the method verbatim;
only the surrounding model state/stop observer is a harness. This is NOT an
AppKit/SwiftUI build, a TCC test, or evidence of a working Mac application.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def lifecycle_method(model):
    methods = re.findall(r'^    func handleLifecycle\(_ event: Int(?:32)?\) \{\n.*?^    \}',
                         model, flags=re.M | re.S)
    if len(methods) != 1:
        raise AssertionError('Expected exactly one production lifecycle method')
    return methods[0]


def fixture(method, events):
    # No fake C declarations: the compiler imports the actual module map/header.
    return '''import NivoxCallAudio

@MainActor final class LifecycleHarness {
    var terminating = false
    var requestingPermission = false
    var reasons: [String] = []
    func stop(reason: String) { reasons.append(reason) }
''' + method + '''
}

@main struct LifecycleRegression {
    @MainActor static func importedCallSites(_ calls: LifecycleHarness) {
''' + ''.join('        calls.handleLifecycle(' + event + ')\n' for event in events) + '''    }
    @MainActor static func main() {
        // These calls use the exact constants found at the three UI call sites.
        let events: [Int] = [''' + ', '.join(events) + ''']
        var cases = 0
        for event in events {
            for pending in [false, true] {
                for terminating in [false, true] {
                    let calls = LifecycleHarness()
                    calls.requestingPermission = pending
                    calls.terminating = terminating
                    calls.handleLifecycle(event)
                    let mustStop = !terminating && (pending || event == NV_CALL_UI_SLEEP)
                    precondition(calls.reasons.count == (mustStop ? 1 : 0),
                                 "lifecycle stop policy changed")
                    if mustStop {
                        let prefix = event == NV_CALL_UI_SLEEP
                            ? "Suspensión:" : "Solicitud de micrófono cancelada"
                        precondition(calls.reasons[0].hasPrefix(prefix), "wrong stop reason")
                    }
                    cases += 1
                }
            }
        }
        // Unknown C policy events must do nothing, even with permission pending.
        for pending in [false, true] {
            let calls = LifecycleHarness()
            calls.requestingPermission = pending
            calls.handleLifecycle(-1)
            precondition(calls.reasons.isEmpty, "unknown event stopped audio")
            cases += 1
        }
        print("lifecycle cases passed: \\(cases)")
    }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--swiftc', default='swiftc', help='Path to a real Swift compiler')
    args = parser.parse_args()
    compiler = shutil.which(args.swiftc)
    if compiler is None:
        raise SystemExit('BLOQUEADO: real Swift compiler required; no source-only fallback')
    out = args.out.resolve()
    if (ROOT / 'build').resolve() not in out.parents or out.exists():
        raise SystemExit('New output under build/ required')
    model = (ROOT / 'App/CallAudio.swift').read_text()
    app = (ROOT / 'App/NoiseProbeApp.swift').read_text()
    method = lifecycle_method(model)
    events = sorted(set(re.findall(r'\.handleLifecycle\((NV_CALL_UI_\w+)\)', model + app)))
    if events != ['NV_CALL_UI_SLEEP', 'NV_CALL_UI_TAB_CHANGED', 'NV_CALL_UI_WINDOW_CLOSED']:
        raise AssertionError('Missing/unexpected lifecycle call sites: ' + repr(events))
    out.mkdir(parents=True)
    report = {'ok': False, 'mac_compiled': False, 'scope': 'extracted Swift method + real C importer/policy',
              'events': events, 'commands': [],
              'input_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ['App/CallAudio.swift', 'App/NoiseProbeApp.swift',
                                            'callaudio/control/module.modulemap',
                                            'callaudio/control/nv_call.h', 'callaudio/control/nv_call_ui.h',
                                            'tests/test_call_lifecycle_swift.py']}}
    with (out / 'commands.log').open('x') as log:
        def run(command, expected_success=True):
            command = list(map(str, command))
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            log.write(repr(command) + '\n' + proc.stdout + proc.stderr + '\n')
            log.flush()
            report['commands'].append({'command': command, 'returncode': proc.returncode})
            if expected_success:
                proc.check_returncode()
            return proc

        def compile_method(text, name):
            source = out / (name + '.swift')
            source.write_text(fixture(text, events))
            binary = out / name
            proc = run([compiler, '-swift-version', '5', '-parse-as-library', '-warnings-as-errors',
                        '-I', ROOT / 'callaudio/control', source, '-o', binary], expected_success=False)
            return proc, binary

        try:
            report['compiler'] = run([compiler, '--version']).stdout.strip()
            result, binary = compile_method(method, 'production')
            result.check_returncode()
            report['runtime'] = run([binary]).stdout.strip()
            cases = len(events) * 2 * 2 + 2
            assert report['runtime'] == f'lifecycle cases passed: {cases}', report['runtime']
            report['cases_passed'] = cases
            # Recreate the exact pre-fix signature/C call, not an arbitrary syntax error.
            signature = 'func handleLifecycle(_ event: Int)'
            boundary = 'nv_call_ui_should_stop(Int32(event), requestingPermission ? 1 : 0)'
            assert method.count(signature) == 1 and method.count(boundary) == 1
            original = method.replace(signature, 'func handleLifecycle(_ event: Int32)')
            original = original.replace(boundary, 'nv_call_ui_should_stop(event, requestingPermission ? 1 : 0)')
            negative, _ = compile_method(original, 'original_int32_signature')
            diagnostic = "cannot convert value of type 'Int' to expected argument type 'Int32'"
            assert negative.returncode != 0 and diagnostic in negative.stderr, negative.stderr
            report['original_type_mismatch_rejected'] = True
            # The Int-facing method must still explicitly cross the C int boundary.
            missing_cast = method.replace(boundary, 'nv_call_ui_should_stop(event, requestingPermission ? 1 : 0)')
            negative, _ = compile_method(missing_cast, 'missing_c_boundary_cast')
            assert negative.returncode != 0 and diagnostic in negative.stderr, negative.stderr
            report['missing_c_cast_rejected'] = True
            report['ok'] = True
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
