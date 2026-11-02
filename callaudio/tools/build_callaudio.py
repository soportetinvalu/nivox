#!/usr/bin/env python3
"""Build and test Nivox call audio (HAL plug-in + real-time engine).

linux: SpeexDSP static + core tests (release with RT-allocation guard, ASan+UBSan, TSan).
mac:   (Darwin arm64 only) core tests, libASPL, NivoxAudio.driver, nivox-callaudio,
       nv-e2e, ad hoc signatures and protected ZIP installer. Never installs anything.
"""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
CA = ROOT / 'callaudio'
SPEEX = ROOT / 'vendor/speexdsp-1.2.1.tar.gz'
SPEEX_SHA = '8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d'
ASPL = ROOT / 'vendor/libASPL-3.1.2.tar.gz'
ASPL_SHA = '428f1ed5cecea725ac4e2db3ab65889a72d133cff8d23ac21b07bf6e2f0561e4'
DRIVER_ID = 'local.christian.nivox.audio-driver'


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def safe_extract(archive, dest, sha):
    if digest(archive) != sha:
        raise SystemExit(f'SHA-256 mismatch: {archive}')
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            p = Path(m.name)
            if p.is_absolute() or '..' in p.parts or not (m.isfile() or m.isdir()):
                raise SystemExit(f'Unsafe archive member: {m.name}')
        tar.extractall(dest, filter='data')


class Runner:
    def __init__(self, out, env):
        self.out, self.env, self.records = out, env, []
        self.log = (out / 'commands.log').open('x')

    def __call__(self, cmd, cwd=None, check=True):
        cmd = [str(c) for c in cmd]
        p = subprocess.run(cmd, cwd=cwd or ROOT, env=self.env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.records.append({'command': cmd, 'returncode': p.returncode})
        self.log.write('$ ' + shlex.join(cmd) + '\n' + p.stdout + '\n')
        self.log.flush()
        if check and p.returncode:
            raise RuntimeError(f'command failed ({p.returncode}): {shlex.join(cmd)}\n{p.stdout[-4000:]}')
        return p.stdout


def last_json(text):
    for line in reversed(text.splitlines()):
        if line.startswith('{'):
            return json.loads(line)
    raise RuntimeError('no JSON result line')


def build_speex(run, out, cc, flags, tag):
    src = out / 'speexdsp-1.2.1'
    if not src.exists():
        safe_extract(SPEEX, out, SPEEX_SHA)
    b = out / f'speex-{tag}'
    b.mkdir()
    run([src / 'configure', '--disable-shared', '--enable-static', '--disable-examples', '--disable-sse',
         '--disable-neon', '--disable-fixed-point', 'CC=' + cc, 'CFLAGS=' + shlex.join(flags)], cwd=b)
    run(['make', '-j4'], cwd=b)
    return b / 'libspeexdsp/.libs/libspeexdsp.a', [f'-I{src / "include"}', f'-I{b / "include/speex"}']


CORE_C = [CA / 'core/nv_driver_core.c', CA / 'core/nv_bridge.c', ROOT / 'src/speex_noise.c']
INC = [f'-I{ROOT / "include"}', f'-I{CA / "core"}']
WARN = ['-std=c11', '-Wall', '-Wextra', '-Werror']


def audit_macho(run, binary):
    arch = run(['lipo', '-archs', binary]).strip()
    if arch != 'arm64':
        raise RuntimeError(f'{binary}: expected arm64 only, got {arch}')
    deps = run(['otool', '-L', binary]).splitlines()[1:]
    bad = [d.strip() for d in deps if not d.strip().startswith(('/System/Library/', '/usr/lib/'))]
    if bad:
        raise RuntimeError(f'{binary}: unexpected dependencies {bad}')
    return {'arch': arch, 'deps': [d.strip().split(' (')[0] for d in deps]}


def linux(run, out, report):
    cc = shutil.which('cc') or 'cc'
    report['compiler'] = run([cc, '--version']).splitlines()[0]
    lib, sinc = build_speex(run, out, cc, ['-O2', '-fPIC'], 'release')
    exe = out / 'test_core'
    wrap = ['-DWRAP_ALLOC'] + ['-Wl,--wrap=' + f for f in ['malloc', 'calloc', 'realloc', 'free']]
    run([cc, '-O2', *WARN, *INC, *sinc, CA / 'tests/test_core.c', *CORE_C, lib, '-lm', '-pthread', *wrap, '-o', exe])
    report['release'] = last_json(run([exe]))
    report['muted_denoiser_countertest'] = last_json(run([exe, '--denoise-only', '--mute-denoise-output'], check=False))
    if report['muted_denoiser_countertest'].get('ok') is not False:
        raise RuntimeError('signal-preservation test accepted a muted denoiser')
    asan_flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
    alib, asinc = build_speex(run, out, cc, ['-fPIC', *asan_flags], 'asan')
    aexe = out / 'test_core_asan'
    run([cc, *asan_flags, *WARN, *INC, *asinc, CA / 'tests/test_core.c', *CORE_C, alib, '-lm', '-pthread', '-o', aexe])
    report['asan_ubsan'] = last_json(run([aexe, '--quick']))
    tsan = ['-O1', '-g', '-fsanitize=thread']
    texe = out / 'test_core_tsan'
    run([cc, *tsan, *WARN, *INC, *sinc, CA / 'tests/test_core.c', *CORE_C, lib, '-lm', '-pthread', '-o', texe])
    # TSan cannot map its shadow with 32-bit mmap ASLR entropy (recent kernels);
    # setarch -R disables ASLR for this one process only (no system change).
    prefix = ['setarch', platform.machine(), '-R'] if shutil.which('setarch') else []
    report['tsan'] = last_json(run([*prefix, texe, '--quick']))
    for k in ['release', 'asan_ubsan', 'tsan']:
        if not report[k].get('ok'):
            raise RuntimeError(f'{k} tests failed')


def mac(run, out, report):
    sdk = run(['xcrun', '--sdk', 'macosx', '--show-sdk-path']).strip()
    report['sdk'] = run(['xcrun', '--sdk', 'macosx', '--show-sdk-version']).strip()
    cc = run(['xcrun', '--find', 'clang']).strip()
    cxx = run(['xcrun', '--find', 'clang++']).strip()
    report['compiler'] = run([cc, '--version']).splitlines()[0]
    target = ['-arch', 'arm64', '-isysroot', sdk, '-mmacosx-version-min=13.0']
    lib, sinc = build_speex(run, out, cc, ['-O2', '-fPIC', *target], 'release')
    exe = out / 'test_core'
    run([cc, '-O2', *target, *WARN, *INC, *sinc, CA / 'tests/test_core.c', *CORE_C, lib, '-lm', '-o', exe])
    # libASPL (MIT), compiled from the pinned release tarball
    safe_extract(ASPL, out, ASPL_SHA)
    aspl = out / 'libASPL-3.1.2'
    objs = []
    for src in sorted((aspl / 'src').glob('*.cpp')):
        o = out / 'aspl-obj' / (src.stem + '.o')
        o.parent.mkdir(exist_ok=True)
        run([cxx, '-std=c++17', '-O2', '-fPIC', *target, '-Wall', '-Wno-unused-parameter',
             f'-I{aspl / "include"}', f'-I{aspl / "src"}', '-c', src, '-o', o])
        objs.append(o)
    libaspl = out / 'libASPL.a'
    run(['ar', 'rcs', libaspl, *objs])
    # Driver bundle
    drv = out / 'NivoxAudio.driver'
    (drv / 'Contents/MacOS').mkdir(parents=True)
    res = drv / 'Contents/Resources'
    res.mkdir()
    shutil.copy2(CA / 'driver/Info.plist', drv / 'Contents/Info.plist')
    info = plistlib.loads((drv / 'Contents/Info.plist').read_bytes())
    if info['CFBundleIdentifier'] != DRIVER_ID or info['CFBundleExecutable'] != 'NivoxAudio':
        raise RuntimeError('driver Info.plist identity mismatch')
    core_o = out / 'nv_driver_core.o'
    run([cc, '-O2', '-fPIC', *target, *WARN, *INC, '-c', CA / 'core/nv_driver_core.c', '-o', core_o])
    drv_exe = drv / 'Contents/MacOS/NivoxAudio'
    run([cxx, '-std=c++17', '-O2', '-fPIC', *target, '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
         f'-I{aspl / "include"}', *INC, '-bundle', CA / 'driver/NivoxAudioDriver.cpp', core_o, libaspl,
         '-framework', 'CoreFoundation', '-framework', 'CoreAudio', '-o', drv_exe])
    nm = run(['nm', '-gU', drv_exe])
    if '_NivoxAudioEntryPoint' not in nm:
        raise RuntimeError('factory symbol NivoxAudioEntryPoint not exported')
    for src, name in [(ROOT / 'LICENSE', 'Own-code-MIT.txt'), (aspl / 'LICENSE', 'libASPL-LICENSE.txt'),
                      (aspl / 'LICENSE.apple2012', 'libASPL-LICENSE.apple2012.txt'),
                      (aspl / 'LICENSE.apple2020', 'libASPL-LICENSE.apple2020.txt')]:
        shutil.copy2(src, res / name)
    report['driver_audit'] = audit_macho(run, drv_exe)
    run(['codesign', '--force', '--sign', '-', '--timestamp=none', drv])
    run(['codesign', '--verify', '--strict', '--verbose=2', drv])
    # Engine CLI + E2E tool
    mac_c = [CA / 'mac/nv_hal.c', CA / 'mac/nv_path.c']
    fw = ['-framework', 'CoreAudio', '-framework', 'AudioToolbox', '-framework', 'CoreFoundation']
    tools = {}
    for name, main in [('nivox-callaudio', CA / 'mac/nivox_callaudio.c'), ('nv-e2e', CA / 'mac/nv_e2e.c'),
                       ('nv-hal-test', CA / 'tests/test_mac_clock.c')]:
        t = out / name
        run([cc, '-O2', *target, *WARN, *INC, *sinc, f'-I{CA / "mac"}', main, *mac_c, *CORE_C, lib, *fw, '-o', t])
        tools[name] = audit_macho(run, t)
        run(['codesign', '--force', '--sign', '-', '--options', 'runtime',
             '--entitlements', CA / 'mac/audio-input.entitlements', '--timestamp=none', t])
        run(['codesign', '--verify', '--strict', t])
        signed = run(['codesign', '-d', '--entitlements', ':-', t])
        ent = plistlib.loads(signed[signed.index('<?xml'):signed.index('</plist>') + len('</plist>')].encode())
        if ent.get('com.apple.security.device.audio-input') is not True:
            raise RuntimeError(f'{name}: signed audio-input entitlement missing')
        tools[name]['audio_input_entitlement'] = True
    # Compile/audit all Apple code before executing tests, so failures retain build evidence.
    report['core_tests'] = last_json(run([exe]))
    report['muted_denoiser_countertest'] = last_json(run([exe, '--denoise-only', '--mute-denoise-output'], check=False))
    if report['muted_denoiser_countertest'].get('ok') is not False:
        raise RuntimeError('signal-preservation test accepted a muted denoiser')
    if not report['core_tests'].get('ok'):
        raise RuntimeError('core tests failed on macOS')
    report['mac_clock_stress'] = last_json(run([out / 'nv-hal-test']))
    if not report['mac_clock_stress'].get('ok'):
        raise RuntimeError('Mac host-time concurrent read test failed')
    report['tools'] = tools
    # ZIP (driver + tools + scripts + notices), re-verified after extraction
    stage = out / 'Nivox-CallAudio'
    stage.mkdir()
    shutil.copytree(drv, stage / 'NivoxAudio.driver', symlinks=True)
    for name in tools:
        shutil.copy2(out / name, stage / name)
    for s in ['install-driver.sh', 'uninstall-driver.sh', 'restore-driver.sh', 'manage-driver.sh']:
        shutil.copy2(CA / 'installer' / s, stage / s)
    shutil.copy2(CA / 'README.md', stage / 'README.md')
    zip_path = out / 'Nivox-CallAudio-arm64-adhoc.zip'
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', stage, zip_path])
    ex = out / 'zip-extracted'
    ex.mkdir()
    run(['ditto', '-x', '-k', zip_path, ex])
    run(['codesign', '--verify', '--strict', '--verbose=2', ex / 'Nivox-CallAudio/NivoxAudio.driver'])
    # A direct-payload PKG replaces an existing installation before postinstall;
    # disable it until it can provide the same protected transaction as the ZIP.
    report.update(zip_sha256=digest(zip_path), pkg_status='disabled: unsafe direct payload replacement',
                  signature='ad-hoc (not Developer ID, not notarized)')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', required=True, choices=['linux', 'mac'])
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    out = a.out.resolve()
    if (ROOT / 'build').resolve() not in out.parents:
        raise SystemExit('--out must be inside build/')
    if out.exists():
        raise SystemExit('--out exists; choose a NEW directory')
    if a.target == 'mac' and (platform.system() != 'Darwin' or platform.machine() != 'arm64'):
        raise SystemExit('BLOQUEADO: mac target requires real Darwin arm64 with Xcode SDK')
    if a.target == 'linux' and platform.system() != 'Linux':
        raise SystemExit('linux target requires Linux')
    out.mkdir(parents=True)
    tmp = out / 'tmp'
    tmp.mkdir()
    env = dict(os.environ, TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE='1',
               ASAN_OPTIONS='detect_leaks=1:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1',
               TSAN_OPTIONS='halt_on_error=1')
    run = Runner(out, env)
    inputs = sorted([*CA.rglob('*.c'), *CA.rglob('*.h'), *CA.rglob('*.cpp'), *CA.rglob('*.plist'),
                     *CA.rglob('*.sh'), *CA.rglob('*.entitlements'),
                     CA / 'tools/build_callaudio.py', ROOT / 'src/speex_noise.c', ROOT / 'include/christian_noise.h'])
    report = {'target': a.target, 'platform': platform.platform(), 'machine': platform.machine(),
              'input_hashes': {str(p.relative_to(ROOT)): digest(p) for p in inputs if p.is_file()},
              'installed_anything': False, 'notarized': False, 'ok': False}
    try:
        (linux if a.target == 'linux' else mac)(run, out, report)
        report['ok'] = True
    except Exception as e:  # report then fail
        report['error'] = str(e)
        raise
    finally:
        report['commands'] = run.records
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        files = [p for p in sorted(out.iterdir()) if p.is_file() and p.name != 'SHA256SUMS']
        (out / 'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in files))
    print(json.dumps({'ok': report['ok'], 'out': str(out)}))


if __name__ == '__main__':
    main()
