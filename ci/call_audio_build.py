# SPDX-License-Identifier: MIT
"""Offline build helpers shared by both app and call-audio delivery recipes."""
from pathlib import Path
import plistlib
from xml.parsers.expat import ExpatError

ROOT = Path(__file__).resolve().parents[1]
CA = ROOT / 'callaudio'
CONTROL_SOURCES = [CA / 'control/nv_call.c', CA / 'mac/nv_call_mac.c', CA / 'mac/nv_hal.c',
                   CA / 'mac/nv_path.c', CA / 'core/nv_bridge.c', CA / 'core/nv_driver_core.c']


def verify_audio_entitlement(run, target):
    """Read the signed entitlement, not the source plist or success text."""
    signed = run(['codesign', '-d', '--entitlements', ':-', target])
    try:
        ent = plistlib.loads(signed[signed.index('<?xml'):signed.index('</plist>') + len('</plist>')].encode())
    except (ValueError, plistlib.InvalidFileException, ExpatError) as error:
        raise RuntimeError(f'{target}: signed entitlement plist missing/invalid') from error
    if not isinstance(ent, dict) or len(ent) != 1 or ent.get('com.apple.security.device.audio-input') is not True:
        raise RuntimeError(f'{target}: unexpected signed audio-input entitlements')
    return True


def test_control(run, out, cc, flags):
    exe = out / 'test_call_control'
    run([cc, *flags, '-std=c11', '-Wall', '-Wextra', '-Werror', f'-I{CA / "control"}',
         CA / 'tests/test_call_control.c', CA / 'control/nv_call.c', '-o', exe])
    return run([exe])


def build_app_library(run, out, cc, common):
    objects = []
    for source in CONTROL_SOURCES:
        obj = out / ('app_' + source.stem + '.o')
        run([cc, *common, f'-I{CA / "control"}', f'-I{CA / "mac"}', f'-I{CA / "core"}',
             '-c', source, '-o', obj])
        objects.append(obj)
    library = out / 'libNivoxCallAudio.a'
    run(['ar', 'rcs', library, *objects])
    return library
