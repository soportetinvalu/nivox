#!/usr/bin/env python3
"""Validate Nivox icon bytes without Pillow, network or Apple tools."""
# SPDX-License-Identifier: MIT
import hashlib
import json
import struct
import zlib
from pathlib import Path

# Modern ICNS PNG representations, including Retina variants.
SLOTS = {'icp4': 16, 'icp5': 32, 'icp6': 64, 'ic07': 128,
         'ic08': 256, 'ic09': 512, 'ic10': 1024,
         'ic11': 32, 'ic12': 64, 'ic13': 256, 'ic14': 512}


def png_size(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Expected PNG')
    offset = 8
    dimensions = None
    ended = False
    while offset < len(data):
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        crc = data[offset + 8 + size:offset + 12 + size]
        if len(body) != size or len(crc) != 4:
            raise ValueError('Truncated PNG')
        if zlib.crc32(kind + body) & 0xffffffff != struct.unpack('>I', crc)[0]:
            raise ValueError('PNG CRC mismatch')
        if kind == b'IHDR':
            dimensions = struct.unpack('>II', body[:8])
        offset += size + 12
        if kind == b'IEND':
            ended = True
            break
    if not ended or offset != len(data) or dimensions is None:
        raise ValueError('Incomplete PNG')
    return dimensions


def icns_slots(data):
    if data[:4] != b'icns' or len(data) < 8 or struct.unpack('>I', data[4:8])[0] != len(data):
        raise ValueError('Invalid ICNS length/header')
    offset, result = 8, {}
    while offset < len(data):
        tag = data[offset:offset + 4].decode('ascii')
        length = struct.unpack('>I', data[offset + 4:offset + 8])[0]
        if length <= 8 or offset + length > len(data) or tag in result or tag not in SLOTS:
            raise ValueError('Invalid ICNS slot: ' + tag)
        payload = data[offset + 8:offset + length]
        if png_size(payload) != (SLOTS[tag], SLOTS[tag]):
            raise ValueError('Wrong ICNS dimensions: ' + tag)
        result[tag] = {'pixels': SLOTS[tag], 'sha256': hashlib.sha256(payload).hexdigest()}
        offset += length
    if offset != len(data) or set(result) != set(SLOTS):
        raise ValueError('Incomplete ICNS representations')
    return result


def validate_assets(assets=None):
    assets = Path(assets) if assets else Path(__file__).resolve().parents[1] / 'App/Assets'
    manifest = json.loads((assets / 'icon-manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if Path(name).name != name or hashlib.sha256((assets / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Icon hash mismatch: ' + name)
    source = (assets / 'Nivox-original.png').read_bytes()
    if list(png_size(source)) != manifest['source_dimensions']:
        raise ValueError('Source dimensions mismatch')
    slots = icns_slots((assets / 'Nivox.icns').read_bytes())
    if slots != manifest['slots']:
        raise ValueError('ICNS manifest mismatch')
    return {'icon_sha256': manifest['files']['Nivox.icns'], 'representations': len(slots),
            'source_sha256': manifest['files']['Nivox-original.png']}


if __name__ == '__main__':
    print(json.dumps(validate_assets(), indent=2))
