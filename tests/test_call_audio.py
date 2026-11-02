#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the real C call controller on Linux/macOS; no dependencies or installs.

The backend is injected. This verifies lifecycle/health/error handling, NOT HAL,
SwiftUI, TCC or actual audio. Sanitizers and a negative mutation are mandatory.
"""
import argparse
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
              'backend': 'injected, no physical IO', 'commands': []}
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
            report['ok'] = True
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
