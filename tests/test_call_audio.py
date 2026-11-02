#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the real C call controller on Linux/macOS; no dependencies or installs.

The backend is injected. This verifies lifecycle/health/error handling, NOT HAL,
SwiftUI, TCC or actual audio. Sanitizers and a negative mutation are mandatory.
"""
import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ci.call_audio_build import test_control


def check_swift_contract(app, model):
    """Source wiring guards only: not a Swift compiler or a UI/TCC test."""
    tab = app.split('.onChange(of: tab)', 1)[1].split('.onDisappear', 1)[0]
    close = app.split('.onDisappear', 1)[1].split('private var probeView', 1)[0]
    assert 'handleLifecycle(NV_CALL_UI_TAB_CHANGED)' in tab
    assert 'handleLifecycle(NV_CALL_UI_WINDOW_CLOSED)' in close
    assert '.onDisappear { closeSurface() }' in app and 'NSWindow.willCloseNotification' in close
    assert 'calls.stop(' not in tab + close and 'calls.start(' not in tab + close
    assert '@NSApplicationDelegateAdaptor(NivoxAppDelegate.self)' in app
    assert 'let calls = CallAudioModel()' in app and 'WindowGroup(ProbeBuild.windowTitle, id: "nivox-main")' in app
    assert 'applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }' in app
    assert 'calls.prepareToQuit { sender.reply(toApplicationShouldTerminate: true) }' in app
    assert '.terminateLater' in app and '.terminateNow' not in app
    assert 'MenuBarExtra' in app and 'openWindow(id: "nivox-main")' in model
    assert 'self.generation == token, self.enabled' in model
    assert 'self.permissionGeneration == token' in app
    assert 'nv_call_ui_should_stop(event, requestingPermission ? 1 : 0)' in model
    assert 'submit(.stop, token: generation)' in model and 'finished?()' in model
    assert '.userInitiatedAllowingIdleSystemSleep' in model and 'forMode: .common' in model
    init = model.split('    init() {', 1)[1].split('    deinit', 1)[0]
    assert '.start(' not in init and 'requestAccess' not in init
    menu_start = model.split('struct CallMenuView', 1)[1].split('audio.start()', 1)[0]
    assert menu_start.index('probe.stop(discard: true') < menu_start.index('probe.stopPlayback()')
    assert 'guard confirmRoutingLoss' in app and 'guard confirmRoutingLoss' in model
    assert 'Nivox no ofrece bypass' in model


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args()
    out = args.out.resolve()
    if (ROOT / 'build').resolve() not in out.parents or out.exists():
        raise SystemExit('New output under build/ required')
    cc = shutil.which('cc')
    if not cc:
        raise SystemExit('BLOQUEADO: C compiler missing; nothing was installed')
    out.mkdir(parents=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
               UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    report = {'ok': False, 'platform': platform.system(), 'mac_compiled': False,
              'backend': 'injected, no physical IO', 'commands': [],
              'input_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ['App/CallAudio.swift', 'App/NoiseProbeApp.swift',
                                            'callaudio/control/nv_call.h', 'callaudio/control/nv_call_ui.h',
                                            'callaudio/control/nv_call.c', 'callaudio/tests/test_call_control.c',
                                            'tests/test_call_audio.py']}}
    with (out / 'commands.log').open('x') as log:
        def run(cmd):
            cmd = list(map(str, cmd))
            proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
            log.write(repr(cmd) + '\n' + proc.stdout + proc.stderr + '\n'); log.flush()
            report['commands'].append({'command': cmd, 'returncode': proc.returncode})
            proc.check_returncode()
            return proc.stdout
        try:
            for name, flags in [('release', ['-O2']), ('asan_ubsan', ['-O1', '-g', '-fsanitize=address,undefined',
                                                                       '-fno-omit-frame-pointer'])]:
                directory = out / name; directory.mkdir()
                report[name] = json.loads(test_control(run, directory, cc, flags).splitlines()[-1])
            # Counterprobe: removing callback readiness must turn the suite red.
            source = (ROOT / 'callaudio/control/nv_call.c').read_text()
            needle = 'c->state = ready ? NV_CALL_RUNNING : NV_CALL_STARTING;'
            assert source.count(needle) == 1
            mutant = out / 'mutant.c'
            mutant.write_text(source.replace(needle, '(void)ready; c->state = NV_CALL_RUNNING;'))
            exe = out / 'mutant'
            run([cc, '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(ROOT / 'callaudio/control'),
                 ROOT / 'callaudio/tests/test_call_control.c', mutant, '-o', exe])
            proc = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
            log.write('counterprobe\n' + proc.stdout + proc.stderr)
            # Require the exact failing readiness assertion, not just any crash.
            assert proc.returncode != 0 and 'test_activity_required_on_both_paths' in proc.stderr, proc.stderr
            report['readiness_mutation_rejected'] = True
            report['mutation_returncode'] = proc.returncode
            # Exercise the same UI policy imported by Swift, with the production
            # controller, and require each unsafe policy mutation to fail by name.
            header = (ROOT / 'callaudio/control/nv_call_ui.h').read_text()
            report['ui_mutations'] = []
            for name, old, new, assertion in [
                ('hide_stops_call', 'return permission_pending != 0;', 'return 1 + 0 * permission_pending;', 'test_ui_hide_keeps_call'),
                ('late_permission', 'return permission_pending != 0;', 'return 0 * permission_pending;', 'test_ui_pending_permission_is_cancelled'),
                ('sleep_keeps_call', 'return 1;', 'return 0;', 'test_ui_sleep_stops_without_restart'),
            ]:
                directory = out / name; directory.mkdir()
                assert header.count(old) == 1
                (directory / 'nv_call_ui.h').write_text(header.replace(old, new))
                shutil.copyfile(ROOT / 'callaudio/control/nv_call.h', directory / 'nv_call.h')
                exe = directory / 'test_mutation'
                run([cc, '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(directory),
                     ROOT / 'callaudio/tests/test_call_control.c', ROOT / 'callaudio/control/nv_call.c', '-o', exe])
                proc = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
                log.write(name + '\n' + proc.stdout + proc.stderr); log.flush()
                assert proc.returncode != 0 and assertion in proc.stderr, proc.stderr
                report['ui_mutations'].append({'name': name, 'rejected': True, 'returncode': proc.returncode})
            app = (ROOT / 'App/NoiseProbeApp.swift').read_text()
            model = (ROOT / 'App/CallAudio.swift').read_text()
            check_swift_contract(app, model)
            # The wiring guard must reject reintroducing the original tab cut.
            broken = app.replace('calls.handleLifecycle(NV_CALL_UI_TAB_CHANGED)', 'calls.stop()')
            try:
                check_swift_contract(broken, model)
            except AssertionError:
                report['swift_contract_mutation_rejected'] = True
            else:
                raise AssertionError('Swift source-contract guard failed to reject tab stop')
            report['swift_source_contract'] = 'passed; not compilation or UI execution'
            report['ok'] = True
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
