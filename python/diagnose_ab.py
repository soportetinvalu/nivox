#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mide WAV A/B locales; no captura, reproduce, convierte ni modifica el audio."""
import argparse
import hashlib
import json
import math
from pathlib import Path
from ab_wav import load_wav, channel_metrics


def analyze(path, start=0.0, end=None):
    wav = load_wav(path)
    rate = wav['sample_rate']
    stop = wav['frames'] if end is None else min(wav['frames'], int(end * rate))
    first = int(start * rate)
    if first >= stop:
        raise ValueError('Requested window has no samples')
    return {
        'file': str(path), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        **{k: v for k, v in wav.items() if k != 'samples'},
        'seconds': wav['frames'] / rate,
        'window_frames': [first, stop],
        'channel_metrics': [channel_metrics(x[first:stop], rate) for x in wav['samples']],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original', type=Path)
    parser.add_argument('clean', type=Path, nargs='?')
    parser.add_argument('--start', type=float, default=0.0)
    parser.add_argument('--end', type=float)
    args = parser.parse_args()
    if (not math.isfinite(args.start) or args.start < 0 or
            args.end is not None and (not math.isfinite(args.end) or args.end <= args.start)):
        parser.error('Expected finite window: 0 <= start < end')
    try:
        original = analyze(args.original, args.start, args.end)
        report = {'original': original, 'scope': 'offline WAV only; not device/cause/voice-isolation evidence'}
        if args.clean:
            clean = analyze(args.clean, args.start, args.end)
            if any(original[k] != clean[k] for k in ('sample_rate', 'channels', 'frames')):
                raise ValueError('A/B files differ in rate/channels/frame count; no implicit alignment or conversion')
            report['clean'] = clean
            report['clean_minus_original_rms_db'] = [
                b['rms_dbfs'] - a['rms_dbfs'] if a['rms_dbfs'] is not None and b['rms_dbfs'] is not None else None
                for a, b in zip(original['channel_metrics'], clean['channel_metrics'])]
        print(json.dumps(report, indent=2, allow_nan=False))
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
