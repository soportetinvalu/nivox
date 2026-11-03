#!/usr/bin/env python3
"""Exportación local determinista por allowlist; no usa red ni publica."""
# SPDX-License-Identifier: MIT
import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FILES = [
    '.gitignore', '.github/workflows/build-speex-arm64.yml', 'build.py', 'README.md', 'LICENSE', 'LICENSES.md',
    'src/speex_noise.c', 'include/christian_noise.h', 'CNoise/NoiseProbe.c',
    'CNoise/include/NoiseProbe.h', 'CNoise/include/module.modulemap',
    'App/NoiseProbeApp.swift', 'App/Info.plist', 'App/Entitlements.plist',
    'App/Assets/Nivox-original.png', 'App/Assets/Nivox.icns', 'App/Assets/icon-manifest.json',
    'App/Assets/BRANDING.md', 'python/christian_noise.py',
    'tests/stream_test.c', 'tests/test_engine.py', 'tests/test_integration.py', 'tests/test_delivery.py',
    'ci/FrameworkSmoke.swift', 'ci/icon_assets.py', 'ci/export_sources.py',
    'vendor/speexdsp-1.2.1.tar.gz', 'docs/RESULTADOS.md', 'docs/ACEPTACION_MAC.md', 'docs/INTEGRACION.md',
    'App/CallAudio.swift', 'ci/call_audio_build.py', 'tests/test_call_audio.py',
    'callaudio/control/nv_call.h', 'callaudio/control/nv_call_ui.h', 'callaudio/control/nv_call.c', 'callaudio/control/module.modulemap',
    'callaudio/mac/nv_call_mac.c', 'callaudio/mac/nv_hal.h', 'callaudio/mac/nv_hal.c',
    'callaudio/mac/nv_path.h', 'callaudio/mac/nv_path.c', 'callaudio/mac/nivox_callaudio.c',
    'callaudio/mac/nv_e2e.c', 'callaudio/core/nv_core.h', 'callaudio/core/nv_bridge.c',
    'callaudio/core/nv_driver_core.c', 'callaudio/tests/test_core.c', 'callaudio/tests/test_call_control.c',
    'callaudio/driver/Info.plist', 'callaudio/driver/NivoxAudioDriver.cpp',
    'callaudio/installer/install-driver.sh', 'callaudio/installer/uninstall-driver.sh',
    'callaudio/installer/postinstall', 'callaudio/tools/build_callaudio.py',
    'callaudio/README.md', 'callaudio/LICENSES.md', 'docs/CALL_AUDIO_UI.md', 'docs/CALL_AUDIO_UI_EVIDENCE.json',
    'vendor/libASPL-3.1.2.tar.gz', '.github/workflows/build-callaudio-arm64.yml',
    'callaudio/core/nv_watch.h', 'callaudio/core/nv_signal.h', 'callaudio/mac/audio-input.entitlements',
    'callaudio/installer/manage-driver.sh', 'callaudio/installer/restore-driver.sh',
    'callaudio/tests/test_mac_clock.c', 'callaudio/tests/test_installer.py',
    'callaudio/tests/test_build_compat.py',
    'callaudio/tests/test_call_mac_contract.c',
    'tests/test_call_lifecycle_swift.py',
    'docs/CALL_AUDIO_SAFE_INTEGRATION.md', 'docs/CALL_AUDIO_SAFE_EVIDENCE.json',
]


def collect_payload():
    if len(FILES) != len(set(FILES)):
        raise ValueError('Duplicate allowlist entry')
    payload = {}
    for name in FILES:
        p = HERE / name
        if (Path(name).is_absolute() or '..' in Path(name).parts or not p.is_file()
                or HERE not in p.resolve().parents
                or any(q.is_symlink() for q in [p, *p.parents] if q != HERE and HERE in q.parents)):
            raise ValueError('Missing/unsafe allowlisted input: ' + name)
        payload[name] = p.read_bytes()
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    out = a.out.resolve()
    if (HERE / 'build').resolve() not in out.parents or out.exists():
        raise SystemExit('Output must be a NEW directory inside delivery/build/')
    payload = collect_payload()
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    payload['source-manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    out.mkdir(parents=True)
    package = out / 'Nivox-public-sources.tar.gz'
    with package.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz, tarfile.open(fileobj=gz, mode='w') as tar:
        for name, data in sorted(payload.items()):
            item = tarfile.TarInfo('nivox/' + name)
            item.size = len(data)
            item.mode = 0o644
            item.mtime = 0
            tar.addfile(item, io.BytesIO(data))
    with tarfile.open(package) as tar:
        members = tar.getmembers()
        expected = {'nivox/' + n for n in payload}
        if {m.name for m in members} != expected or len(members) != len(expected):
            raise RuntimeError('Archive allowlist mismatch')
        for m in members:
            handle = tar.extractfile(m)
            if not m.isfile() or not handle or handle.read() != payload[m.name.removeprefix('nivox/')]:
                raise RuntimeError('Archive bytes mismatch: ' + m.name)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (out / 'SHA256SUMS').write_text(digest + '  ' + package.name + '\n')
    (out / 'source-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    result = {'package': package.name, 'sha256': digest, 'source_files': len(manifest),
              'archive_entries': len(payload), 'exact_members_and_bytes_verified': True,
              'published': False, 'mac_compiled': False}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
