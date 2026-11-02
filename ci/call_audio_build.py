# SPDX-License-Identifier: MIT
"""Offline build helpers shared by both app and call-audio delivery recipes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CA = ROOT / 'callaudio'
CONTROL_SOURCES = [CA / 'control/nv_call.c', CA / 'mac/nv_call_mac.c', CA / 'mac/nv_hal.c',
                   CA / 'mac/nv_path.c', CA / 'core/nv_bridge.c', CA / 'core/nv_driver_core.c']


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
