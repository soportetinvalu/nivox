#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lectura local estricta de WAV PCM16 / IEEE Float32, sin convertir tasa/canales."""
import math
import struct
from pathlib import Path

MAX_BYTES = 128 * 1024 * 1024


def load_wav(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('WAV exceeds 128 MiB diagnostic limit')
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('Expected little-endian RIFF/WAVE')
    end = 8 + struct.unpack_from('<I', data, 4)[0]
    if end != len(data):
        raise ValueError('Truncated WAV or trailing bytes outside RIFF')
    fmt = payload = None
    pos = 12
    while pos < end:
        if pos + 8 > end:
            raise ValueError('Truncated WAV chunk header')
        tag, size = struct.unpack_from('<4sI', data, pos)
        pos += 8
        if pos + size + (size & 1) > end:
            raise ValueError('Truncated WAV chunk payload/padding')
        chunk = data[pos:pos + size]
        if tag == b'fmt ':
            if fmt is not None:
                raise ValueError('Duplicate WAV fmt chunk')
            fmt = chunk
        elif tag == b'data':
            if payload is not None:
                raise ValueError('Duplicate WAV data chunk')
            payload = chunk
        pos += size + (size & 1)
    if fmt is None or payload is None or len(fmt) < 16:
        raise ValueError('Missing WAV fmt/data or invalid fmt size')
    encoding, channels, rate, byte_rate, align, bits = struct.unpack_from('<HHIIHH', fmt)
    extensible = encoding == 0xfffe
    if extensible:
        if len(fmt) < 40 or struct.unpack_from('<H', fmt, 16)[0] < 22:
            raise ValueError('Invalid WAVE_FORMAT_EXTENSIBLE')
        valid_bits = struct.unpack_from('<H', fmt, 18)[0]
        guid = fmt[24:40]
        if guid[4:] != bytes.fromhex('00001000800000aa00389b71'):
            raise ValueError('Unsupported WAV subtype GUID')
        encoding = struct.unpack_from('<I', guid)[0]
        if valid_bits != bits:
            raise ValueError('Unsupported WAV valid bits')
    if (encoding, bits) not in ((1, 16), (3, 32)):
        raise ValueError('Expected WAV PCM16 or IEEE Float32')
    if not 1 <= channels <= 32 or not 1 <= rate <= 384000:
        raise ValueError('Invalid WAV channel count/sample rate')
    width = bits // 8
    if align != channels * width or byte_rate != rate * align or len(payload) % align:
        raise ValueError('Invalid WAV block alignment/byte rate/payload length')
    values = [v[0] for v in struct.iter_unpack('<h' if encoding == 1 else '<f', payload)]
    if encoding == 1:
        values = [v / 32768.0 for v in values]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Non-finite WAV samples')
    return {
        'encoding': 'PCM16' if encoding == 1 else 'IEEE_FLOAT32',
        'extensible': extensible, 'sample_rate': rate, 'channels': channels,
        'frames': len(values) // channels,
        'samples': [values[i::channels] for i in range(channels)],
    }


def level_db(amplitude):
    return 20 * math.log10(amplitude) if amplitude > 0 else None


def channel_metrics(samples, rate):
    n = len(samples)
    if not n:
        return {'frames': 0}
    dc = math.fsum(samples) / n
    rms = math.sqrt(math.fsum(x*x for x in samples) / n)
    ac_rms = math.sqrt(math.fsum((x-dc)**2 for x in samples) / n)
    lines = {}
    # Proyección puntual, NO clasificador del origen del zumbido.
    for frequency in sorted({base*k for base in (50, 60) for k in range(1, 11)}):
        if frequency >= rate / 2:
            continue
        omega = 2 * math.pi * frequency / rate
        cosine = math.fsum(x*math.cos(omega*i) for i, x in enumerate(samples))
        sine = math.fsum(x*math.sin(omega*i) for i, x in enumerate(samples))
        line_rms = math.sqrt(2*(cosine*cosine + sine*sine)) / n
        lines[str(frequency)] = level_db(line_rms)
    return {
        'frames': n, 'peak_dbfs': level_db(max(abs(x) for x in samples)),
        'rms_dbfs': level_db(rms), 'ac_rms_dbfs': level_db(ac_rms), 'dc': dc,
        'near_full_scale_fraction': sum(abs(x) >= 32767/32768 for x in samples) / n,
        'over_full_scale_fraction': sum(abs(x) > 1 for x in samples) / n,
        'exact_zero_fraction': sum(x == 0 for x in samples) / n,
        'line_rms_dbfs_at_hz': lines,
    }
