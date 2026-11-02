#!/usr/bin/env python3
"""Build aislado y sin red: SpeexDSP estático, pruebas reales y app arm64 opcional."""
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
from ci.icon_assets import validate_assets
from ci.call_audio_build import build_app_library, test_control, CONTROL_SOURCES

HERE = Path(__file__).resolve().parent
SHA = '8c777343e4a6399569c72abc38a95b24db56882c83dbdb6c6424a5f4aeb54d3d'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_macho(binary):
    arch = subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip()
    if arch != 'arm64':
        raise RuntimeError('Expected arm64 ONLY: ' + arch)
    deps = subprocess.check_output(['otool', '-L', str(binary)], text=True)
    loads = subprocess.check_output(['otool', '-l', str(binary)], text=True)
    rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset', loads)
    if any(not p.startswith(('/usr/lib/', '/System/Library/')) for p in rpaths):
        raise RuntimeError('Unexpected runtime search paths: ' + repr(rpaths))
    for row in deps.splitlines()[1:]:
        dep = row.strip().split(' (')[0]
        # A dylib's first entry is its own install_name, not a dependency.
        if binary.suffix == '.dylib' and dep == '@rpath/' + binary.name:
            continue
        swift_system = re.fullmatch(r'@rpath/libswift[A-Za-z0-9_]+\.dylib', dep) and '/usr/lib/swift' in rpaths
        if not dep.startswith(('/System/Library/', '/usr/lib/')) and not swift_system:
            raise RuntimeError('Unexpected external dependency: ' + dep)
    return {'architecture': arch, 'dependencies': deps, 'rpaths': rpaths}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', required=True, choices=['linux', 'macapp'])
    ap.add_argument('--out', required=True, type=Path, help='Directorio nuevo bajo build/')
    ap.add_argument('--sanitize', action='store_true')
    a = ap.parse_args()
    if sys.version_info < (3, 12):
        raise SystemExit('Python >=3.12 required')
    out = a.out.resolve()
    if (HERE / 'build').resolve() not in out.parents:
        raise SystemExit('Output must be inside this stage/build/')
    if out.exists():
        raise SystemExit('Output exists; choose a NEW directory')
    mac = a.target == 'macapp'
    if mac and (platform.system() != 'Darwin' or platform.machine() != 'arm64'):
        raise SystemExit('BLOQUEADO: actual Darwin arm64 + Apple SDK required; Mac compilation NOT tested on Linux')
    if not mac and platform.system() != 'Linux':
        raise SystemExit('Linux target requires Linux')
    if mac and a.sanitize:
        raise SystemExit('Sanitizers supported only by the separate Linux native harness')
    for tool in ['make', 'ar'] + (['xcrun', 'codesign', 'lipo', 'otool', 'ditto'] if mac else ['cc']):
        if not shutil.which(tool):
            raise SystemExit('BLOQUEADO: missing tool: ' + tool)
    archive = HERE / 'vendor/speexdsp-1.2.1.tar.gz'
    if digest(archive) != SHA:
        raise SystemExit('SpeexDSP archive SHA-256 mismatch')
    icon = validate_assets(HERE / 'App/Assets')
    info = plistlib.loads((HERE / 'App/Info.plist').read_bytes())
    if (info.get('CFBundleExecutable') != 'Nivox' or info.get('CFBundleName') != 'Nivox'
            or info.get('CFBundleDisplayName') != 'Nivox' or info.get('CFBundleIconFile') != 'Nivox.icns'
            or info.get('CFBundleIdentifier') != 'local.christian.nivox.speex.experimental'
            or 'SpeexDSP' not in info.get('NSMicrophoneUsageDescription', '')):
        raise SystemExit('Invalid Nivox/SpeexDSP bundle identity')
    entitlements = plistlib.loads((HERE / 'App/Entitlements.plist').read_bytes())
    if entitlements != {'com.apple.security.device.audio-input': True}:
        raise SystemExit('Unexpected entitlements')
    sdk = sdk_version = swift = None
    cc = shutil.which('cc') or ''
    flags = ['-O3', '-fPIC']
    if mac:
        sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
        sdk_version = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-version'], text=True).strip()
        if int(sdk_version.split('.')[0]) < 13:
            raise SystemExit('macOS SDK >=13 required')
        cc = subprocess.check_output(['xcrun', '--find', 'clang'], text=True).strip()
        swift = subprocess.check_output(['xcrun', '--find', 'swiftc'], text=True).strip()
        flags += ['-arch', 'arm64', '-isysroot', sdk, '-mmacosx-version-min=13.0']
    san = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if a.sanitize else []
    flags += san
    out.mkdir(parents=True)
    tmp = out / 'tmp'
    tmp.mkdir()
    env = os.environ.copy()
    env.update(TMPDIR=str(tmp), CLANG_MODULE_CACHE_PATH=str(tmp / 'clang-cache'), PYTHONDONTWRITEBYTECODE='1')
    if a.sanitize:
        env.update(ASAN_OPTIONS='detect_leaks=1:halt_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    records = []
    report = {'target': a.target, 'engine': 'SpeexDSP 1.2.1', 'noise_suppress_db': -15,
              'strength': 1, 'delay_samples': 960, 'weights_included': False,
              'platform': platform.platform(), 'machine': platform.machine(), 'sdk': sdk_version,
              'sanitize': a.sanitize, 'mac_compiled': False, 'microphone_tested': False,
              'gui_launched': False, 'gatekeeper_tested': False, 'notarized': False,
              'signature': None, 'commands': records, 'upstream_sha256': SHA, 'icon': icon,
              'input_hashes': {str(p.relative_to(HERE)): digest(p)
                               for p in [Path(__file__).resolve(), HERE / 'src/speex_noise.c',
                                         HERE / 'CNoise/NoiseProbe.c', HERE / 'CNoise/include/NoiseProbe.h',
                                         HERE / 'include/christian_noise.h', HERE / 'tests/stream_test.c',
                                         HERE / 'tests/test_engine.py', HERE / 'tests/test_integration.py',
                                         HERE / 'python/christian_noise.py', HERE / 'ci/FrameworkSmoke.swift',
                                         HERE / 'App/NoiseProbeApp.swift', HERE / 'App/Info.plist',
                                         HERE / 'App/Entitlements.plist', HERE / 'App/Assets/Nivox.icns',
                                         HERE / 'App/Assets/Nivox-original.png', HERE / 'App/Assets/icon-manifest.json',
                                         HERE / 'ci/icon_assets.py', HERE / 'ci/call_audio_build.py',
                                         HERE / 'App/CallAudio.swift', HERE / 'callaudio/control/nv_call.h',
                                         HERE / 'callaudio/control/nv_call_ui.h',
                                         HERE / 'callaudio/control/module.modulemap',
                                         HERE / 'callaudio/tests/test_call_control.c',
                                         *CONTROL_SOURCES]}}
    with (out / 'commands.log').open('x') as log:
        def run(command, cwd=HERE):
            command = list(map(str, command))
            p = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            records.append({'command': command, 'cwd': str(cwd), 'returncode': p.returncode, 'output': p.stdout})
            log.write('$ ' + shlex.join(command) + '\n' + p.stdout + '\n')
            log.flush()
            p.check_returncode()
            return p.stdout
        try:
            report['compiler'] = run([cc, '--version']).splitlines()[0]
            # No links or traversal are expected in this official source archive.
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    p = Path(member.name)
                    if p.is_absolute() or '..' in p.parts or not (member.isfile() or member.isdir()):
                        raise RuntimeError('Unsafe upstream archive member: ' + member.name)
                tar.extractall(out, filter='data')
            source = out / 'speexdsp-1.2.1'
            upstream = out / 'speex-build'
            upstream.mkdir()
            run([source / 'configure', '--disable-shared', '--enable-static', '--disable-examples',
                 '--disable-sse', '--disable-neon', '--disable-fixed-point',
                 'CC=' + cc, 'CFLAGS=' + shlex.join(flags)], upstream)
            run(['make', '-j2'], upstream)
            speex = upstream / 'libspeexdsp/.libs/libspeexdsp.a'
            common = [*flags, '-std=c11', '-Wall', '-Wextra', '-Werror',
                      '-I' + str(HERE / 'include'), '-I' + str(HERE / 'CNoise/include'),
                      '-I' + str(source / 'include'), '-I' + str(upstream / 'include/speex')]
            report['call_control_tests'] = json.loads(test_control(run, out, cc, flags).splitlines()[-1])
            objects = []
            for src in [HERE / 'src/speex_noise.c', HERE / 'CNoise/NoiseProbe.c']:
                obj = out / (src.stem + '.o')
                run([cc, *common, '-c', src, '-o', obj])
                objects.append(obj)
            own = out / 'libNoiseProbe.a'
            run(['ar', 'rcs', own, *objects])
            native = out / 'stream_test'
            wrap = [] if mac or a.sanitize else ['-DWRAP_ALLOC'] + ['-Wl,--wrap=' + x for x in ['malloc', 'calloc', 'realloc', 'free']]
            run([cc, *common, HERE / 'tests/stream_test.c', own, speex, '-lm', '-pthread', *wrap, '-o', native])
            report['native_test'] = json.loads(run([native]).splitlines()[-1])
            report['dsp_executed'] = True
            lib = out / ('libNoiseProbe.dylib' if mac else 'libNoiseProbe.so')
            if not a.sanitize:
                link = ['-dynamiclib', '-Wl,-install_name,@rpath/libNoiseProbe.dylib'] if mac else ['-shared', '-Wl,-z,defs']
                run([cc, *flags, *link, *objects, speex, '-lm', '-o', lib])
                run([sys.executable, HERE / 'tests/test_engine.py', '--library', lib,
                     '--strength', '1', '--latency', '960', '--temp-dir', tmp,
                     '--result', out / 'unit-tests.json'])
                report['unit_tests'] = json.loads((out / 'unit-tests.json').read_text())
                run([sys.executable, HERE / 'tests/test_integration.py', '--library', lib, '--out', out / 'integration'])
                report['integration_tests'] = json.loads((out / 'integration/report.json').read_text())
            if mac:
                swift_flags = [swift, '-swift-version', '5', '-parse-as-library', '-O',
                               '-module-name', 'NivoxSpeexApp', '-target', 'arm64-apple-macos13.0', '-sdk', sdk,
                               '-Xlinker', '-rpath', '-Xlinker', '/usr/lib/swift', '-I', HERE / 'CNoise/include']
                smoke = out / 'framework_smoke'
                run([*swift_flags, HERE / 'ci/FrameworkSmoke.swift', own, speex,
                     '-framework', 'AVFoundation', '-o', smoke])
                report['swift_test'] = json.loads(run([smoke]).splitlines()[-1])
                app = out / 'Nivox.app'
                exe = app / 'Contents/MacOS/Nivox'
                resources = app / 'Contents/Resources'
                exe.parent.mkdir(parents=True)
                resources.mkdir()
                shutil.copy2(HERE / 'App/Info.plist', app / 'Contents/Info.plist')
                info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
                if info.get('CFBundleExecutable') != 'Nivox' or not info.get('NSMicrophoneUsageDescription'):
                    raise RuntimeError('Invalid bundle identity or microphone purpose')
                for name in ['Nivox.icns', 'Nivox-original.png', 'icon-manifest.json', 'BRANDING.md']:
                    shutil.copy2(HERE / 'App/Assets' / name, resources / name)
                if validate_assets(resources) != icon:
                    raise RuntimeError('Bundled icon differs from original branding')
                call_library = build_app_library(run, out, cc, common)
                run([*swift_flags, '-I', HERE / 'callaudio/control',
                     HERE / 'App/NoiseProbeApp.swift', HERE / 'App/CallAudio.swift',
                     call_library, own, speex, '-framework', 'CoreAudio', '-framework', 'AudioToolbox',
                     '-framework', 'CoreFoundation', '-framework', 'SwiftUI', '-framework', 'AppKit',
                     '-framework', 'AVFoundation', '-o', exe])
                for src, name in [(HERE / 'LICENSE', 'Own-code-MIT.txt'), (source / 'COPYING', 'SpeexDSP-COPYING.txt'),
                                  (HERE / 'LICENSES.md', 'LICENSES.md'), (HERE / 'README.md', 'README-Speex.md')]:
                    shutil.copy2(src, resources / name)
                # Preserve individual source notices in addition to COPYING.
                notices = resources / 'upstream-notices'
                for pattern in ['libspeexdsp/*.c', 'libspeexdsp/*.h', 'include/speex/*.h']:
                    for src in source.glob(pattern):
                        dst = notices / src.relative_to(source)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                report['app_audit'] = audit_macho(exe)
                audit_macho(native)
                audit_macho(smoke)
                audit_macho(lib)
                run(['codesign', '--force', '--sign', '-', '--options', 'runtime', '--entitlements', HERE / 'App/Entitlements.plist', app])
                run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', app])
                signature = run(['codesign', '--display', '--verbose=4', app])
                if 'Signature=adhoc' not in signature:
                    raise RuntimeError('Expected ad hoc signature')
                package = out / 'Nivox-arm64-experimental.zip'
                run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, package])
                extracted = out / 'extracted'
                extracted.mkdir()
                run(['ditto', '-x', '-k', package, extracted])
                copy = extracted / app.name
                run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', copy])
                report['extracted_audit'] = audit_macho(copy / 'Contents/MacOS/Nivox')
                if validate_assets(copy / 'Contents/Resources') != icon:
                    raise RuntimeError('Extracted icon validation failed')
                def hashes(folder):
                    return {str(p.relative_to(folder)): digest(p) for p in folder.rglob('*') if p.is_file()}
                if hashes(app) != hashes(copy):
                    raise RuntimeError('Bundle archive round trip changed bytes')
                report.update(mac_compiled=True, signature='ad-hoc', archive_roundtrip_verified=True,
                              bundle_hashes=hashes(app), archive_sha256=digest(package))
            report['ok'] = True
        except Exception as e:
            report.update(ok=False, error=str(e))
            raise
        finally:
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    sums = ['report.json', 'commands.log', 'stream_test']
    if mac:
        sums += ['Nivox-arm64-experimental.zip']
    (out / 'SHA256SUMS').write_text(''.join(digest(out / p) + '  ' + p + '\n' for p in sums))
    print(json.dumps({'ok': True, 'out': str(out), 'mac_compiled': mac, 'sanitize': a.sanitize}))


if __name__ == '__main__':
    main()
