#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ejercita el C usado por A/B. Linux no acredita captura/reproducción Apple."""
import argparse
import ctypes as c
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from ab_wav import load_wav
import test_ab_diagnosis as dsp

class Levels(c.Structure):
    _fields_ = [(n, c.c_double) for n in ('applied_gain_db', 'raw_rms_dbfs', 'filtered_rms_dbfs', 'peak_dbfs')] + [
        ('full_scale_samples', c.c_size_t), ('gain_limited', c.c_int), ('bypassed', c.c_int)]

LIB: c.CDLL
ARGS: argparse.Namespace
METRICS = {}

def prepare(raw, wet, gain=0, mix=.25, library=None):
    lib = library or LIB
    n = len(raw)
    p = c.POINTER(c.c_float)
    lib.np_prepare_ab.argtypes = [p, p, c.c_size_t, c.c_double, c.c_double, p, p, c.POINTER(Levels)]
    lib.np_prepare_ab.restype = c.c_int
    r, w = (c.c_float*n)(*raw), (c.c_float*n)(*wet)
    a, b = (c.c_float*n)(*([.123]*n)), (c.c_float*n)(*([.123]*n))
    s = Levels()
    rc = lib.np_prepare_ab(r, w, n, gain, mix, a, b, c.byref(s))
    return rc, list(a), list(b), {field[0]: getattr(s, field[0]) for field in Levels._fields_}

def rms(x):
    return math.sqrt(sum(v*v for v in x)/len(x))

def content_oracle(a, b):
    assert rms(a) > 1e-9, 'missing original content'
    assert rms(b) >= .74999*rms(a), 'filtered energy below dry floor'
    assert max(map(abs, a+b)) <= 10**(-1/20)+1e-7, 'peak ceiling exceeded'

class Tests(unittest.TestCase):
    def test_identity_without_gain_and_bypass(self):
        x = [0, .1, -.5, .2]
        rc, a, b, s = prepare(x, [0]*4, mix=0)
        self.assertEqual(rc, 0)
        exact = list((c.c_float*4)(*x))
        self.assertEqual(a, exact); self.assertEqual(b, exact)
        self.assertEqual(s['applied_gain_db'], 0)

    def test_low_input_explicit_gain_both_copies(self):
        x = [.001, -.002, .003, 0]
        rc, a, b, s = prepare(x, x, gain=24)
        self.assertEqual(rc, 0)
        self.assertAlmostEqual(a[0]/x[0], 10**(24/20), places=5)
        self.assertEqual(a, b); self.assertEqual(s['gain_limited'], 0)
        self.assertAlmostEqual(s['applied_gain_db'], 24)

    def test_peak_limit_common_gain_not_clip(self):
        x = [.9, -.2, 0, .1]
        rc, a, b, s = prepare(x, [0]*4, gain=24)
        self.assertEqual(rc, 0); self.assertEqual(s['gain_limited'], 1)
        self.assertAlmostEqual(max(map(abs, a)), 10**(-1/20), places=6)
        for i in (0, 1, 3): self.assertAlmostEqual(b[i]/a[i], .75, places=6)
        content_oracle(a, b)

    def test_silence_does_not_normalize_noise_or_nan(self):
        for wet in ([0]*40, [.1]*40):
            rc, a, b, s = prepare([0]*40, wet, gain=24)
            self.assertEqual(rc, 0); self.assertEqual(a+b, [0]*80)
            self.assertEqual(s['bypassed'], 1)
            self.assertEqual(s['applied_gain_db'], 0)

    def test_anticorrelation_bypasses_not_cancels(self):
        x = [.2, -.1, .4, -.2]
        rc, a, b, s = prepare(x, [-v for v in x])
        self.assertEqual(rc, 0); self.assertEqual(a, b); self.assertEqual(s['bypassed'], 1)

    def test_muted_wet_retains_dry_floor(self):
        x = [.2*math.sin(i*.1) for i in range(2000)]
        rc, a, b, _ = prepare(x, [0]*len(x))
        self.assertEqual(rc, 0); content_oracle(a, b)
        self.assertAlmostEqual(20*math.log10(rms(b)/rms(a)), 20*math.log10(.75), places=5)
        for bad in ([0]*len(b), [v*.1 for v in b]):
            with self.assertRaisesRegex(AssertionError, 'filtered energy below dry floor'):
                content_oracle(a, bad)
        METRICS['muted_and_extra_attenuated_counterchecks'] = 'rejected by content floor'

    def test_invalid_settings_and_samples_fail_closed(self):
        for gain, mix in [(-1,.25),(24.01,.25),(math.nan,.25),(0,-.1),(0,.26),(0,math.inf)]:
            rc, a, b, _ = prepare([.1], [.1], gain, mix)
            self.assertEqual(rc, -1)
            self.assertEqual(a, list((c.c_float*1)(.123)))
        for value in (math.nan, math.inf, -math.inf, 1.01, -1.01):
            self.assertEqual(prepare([value],[0])[0], -1)
            self.assertEqual(prepare([0],[value])[0], -1)
        self.assertEqual(prepare([],[])[0], -1)

    def test_full_scale_warning_and_no_added_clipping(self):
        rc, a, b, s = prepare([1,-1,.1], [1,-1,.1], gain=24)
        self.assertEqual(rc, 0); self.assertEqual(s['full_scale_samples'], 2)
        content_oracle(a, b)

    def test_valid_timestamps_and_discontinuity_rejected(self):
        p = c.POINTER(c.c_float)
        LIB.np_create.argtypes = [c.c_uint,c.c_uint,c.c_size_t]; LIB.np_create.restype = c.c_void_p
        LIB.np_push_timed.argtypes = [c.c_void_p,p,c.c_size_t,c.c_int64]
        LIB.np_destroy.argtypes = [c.c_void_p]
        LIB.np_error.argtypes = [c.c_void_p]
        block = (c.c_float*100)(*([0]*100))
        for second, expected in [(1100,0),(1101,-1),(1000,-1),(1099,-1),(2**63-1,-1)]:
            h = LIB.np_create(48000,1,1000); self.assertTrue(h)
            try:
                self.assertEqual(LIB.np_push_timed(h,block,100,1000), 0)
                self.assertEqual(LIB.np_push_timed(h,block,100,second), expected)
                if expected:
                    self.assertEqual(LIB.np_error(h), -1)
                    self.assertEqual(LIB.np_push_timed(h,block,100,1100), -1)
            finally: LIB.np_destroy(h)

    def test_real_dsp_synthetic_quiet_signal_not_muted(self):
        x = [.001*math.sin(2*math.pi*173*i/48000) for i in range(48000)]
        raw, wet = dsp.capture(x)
        rc, a, b, s = prepare(raw, wet, gain=18)
        self.assertEqual(rc, 0); content_oracle(a,b)
        self.assertLess(abs(s['applied_gain_db']-18), 1e-6)
        METRICS['synthetic_quiet_tone'] = s

    def test_compiled_mutants_detect_peak_and_content_regressions(self):
        original = (ROOT/'CNoise/ProbeLevels.c').read_text()
        mutants = {
            'uncapped_gain': ('fmin(requested, ceiling / peak)', 'requested', 'peak ceiling exceeded'),
            'muted_output': ('b[i] = (float)(((1 - mix) * raw[i] + mix * wet[i]) * gain);', 'b[i] = 0;', 'filtered energy below dry floor')}
        for name, (old,new,diagnostic) in mutants.items():
            self.assertEqual(original.count(old), 1)
            src = ARGS.out.parent/(name+'.c'); binary = src.with_suffix('.so' if sys.platform != 'darwin' else '.dylib')
            src.write_text(original.replace(old,new))
            link = '-dynamiclib' if sys.platform == 'darwin' else '-shared'
            command = ['cc','-O2','-fPIC',link,'-I'+str(ROOT/'CNoise/include'),str(src),'-lm','-o',str(binary)]
            proc = subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            rc,a,b,_ = prepare([.8,-.2],[0,0],gain=24,library=c.CDLL(str(binary)))
            self.assertEqual(rc,0)
            # Isolate the named defect; uncapped gain also preserves content.
            with self.assertRaisesRegex(AssertionError, diagnostic): content_oracle(a,b)
        METRICS['compiled_mutants'] = list(mutants)

    def test_strided_channel_extraction_and_failures(self):
        """C half of the IOProc capture: same function the Swift callback calls."""
        p = c.POINTER(c.c_float)
        LIB.np_create.argtypes = [c.c_uint,c.c_uint,c.c_size_t]; LIB.np_create.restype = c.c_void_p
        LIB.np_destroy.argtypes = [c.c_void_p]; LIB.np_error.argtypes = [c.c_void_p]
        LIB.np_received.argtypes = [c.c_void_p]; LIB.np_received.restype = c.c_size_t
        LIB.np_blocks.argtypes = [c.c_void_p]; LIB.np_blocks.restype = c.c_size_t
        LIB.np_push_strided_timed.argtypes = [c.c_void_p,p,c.c_size_t,c.c_size_t,c.c_size_t,c.c_int64]
        LIB.np_pump.argtypes = [c.c_void_p]; LIB.np_close.argtypes = [c.c_void_p]
        LIB.np_finish.argtypes = [c.c_void_p]; LIB.np_count.argtypes = [c.c_void_p]; LIB.np_count.restype = c.c_size_t
        LIB.np_raw.argtypes = [c.c_void_p]; LIB.np_raw.restype = p
        def push(h, data, frames, stride, offset, when):
            buf = (c.c_float*len(data))(*data)
            return LIB.np_push_strided_timed(h, buf, frames, stride, offset, when)
        # Interleaved stereo: channel 1 only, 10000 frames > 4096 scratch, order preserved.
        n = 10000
        stereo = [v for i in range(n) for v in (.25, (i % 1000)*1e-4)]
        h = LIB.np_create(48000,1,20000); self.assertTrue(h)
        try:
            self.assertEqual(push(h, stereo, n, 2, 1, 500), 0)
            self.assertEqual(LIB.np_received(h), n); self.assertEqual(LIB.np_blocks(h), 1)
            self.assertEqual(LIB.np_pump(h), 0); LIB.np_close(h); self.assertEqual(LIB.np_finish(h), 0)
            raw = LIB.np_raw(h)
            expected = list((c.c_float*n)(*[(i % 1000)*1e-4 for i in range(n)]))
            self.assertEqual([raw[i] for i in range(n)], expected)
        finally: LIB.np_destroy(h)
        # Contiguity across calls, then every defect is sticky and fail-closed.
        block = [0.1]*960
        cases = [('gap', 481), ('repeat', 479)]
        for name, second in cases:
            h = LIB.np_create(48000,1,20000)
            try:
                self.assertEqual(push(h, block, 480, 2, 0, 0), 0)
                self.assertEqual(push(h, block, 480, 2, 0, second), -1, name)
                self.assertEqual(LIB.np_error(h), -1, name)
                self.assertEqual(push(h, block, 480, 2, 0, second+480), -1, name+' sticky')
            finally: LIB.np_destroy(h)
        bad = [('offset>=stride', 480, 2, 2), ('zero stride', 480, 0, 0), ('stride too large', 1, 1025, 0)]
        for name, frames, stride, offset in bad:
            h = LIB.np_create(48000,1,20000)
            try:
                self.assertEqual(push(h, block, frames, stride, offset, 0), -1, name)
                self.assertEqual(LIB.np_error(h), -1, name)
            finally: LIB.np_destroy(h)
        for name, value in [('NaN', float('nan')), ('out of range', 1.5)]:
            h = LIB.np_create(48000,1,20000)
            try:
                self.assertEqual(push(h, [value]*960, 480, 2, 1, 0), -1, name)
                self.assertEqual(LIB.np_error(h), -1, name)
            finally: LIB.np_destroy(h)
        h = LIB.np_create(48000,1,20000)
        try:
            self.assertEqual(LIB.np_push_strided_timed(h, None, 480, 2, 0, 0), -1)
            self.assertEqual(LIB.np_push_strided_timed(None, (c.c_float*2)(), 1, 1, 0, 0), -1)
        finally: LIB.np_destroy(h)
        METRICS['strided_capture_cases'] = len(cases)+len(bad)+4

    def test_swift_wiring_contract_not_native_execution(self):
        app = (ROOT/'App/NoiseProbeApp.swift').read_text()
        route = (ROOT/'App/ProbeInput.swift').read_text()
        def check(a, r):
            # Explicit device, explicit channel, direct HAL data: no AVAudioEngine capture/bus format.
            self.assertIn('HALCapture(device: d, channel: selectedChannel, session: s)',a)
            self.assertIn('ProbeGuard.difference(from: base, to: ProbeInput.snapshot(d.deviceID))',a)
            self.assertIn('ProbeGuard.captureProblem(snapshot, channel: selectedChannel)',a)
            self.assertIn('stop(discard: true, reason: "Cambio de ruta/formato real:',a)
            self.assertNotIn('installTap',a)
            self.assertIn('AudioDeviceCreateIOProcIDWithBlock(&created, device.deviceID, nil, block)',r)
            self.assertIn('np_push_strided_timed(handle',r)
            self.assertIn('channel - first, sampleTime',r)
            self.assertIn('p.volume = Float(listenVolume)',a)
            self.assertIn('(0...1).contains(listenVolume)',a)
            self.assertIn('!recording, !requesting, headphones',a)
        check(app, route)
        self.assertIn('confirmedOutputUID == output.id',app)
        self.assertIn('probe.confirmHeadphones(enabled)',app)
        self.assertIn('stopPlayback()\n        requesting = true',app)
        self.assertIn('Slider(value: $probe.listenVolume, in: 0...1',app)
        self.assertIn('Slider(value: $probe.copyGainDB, in: 0...24',app)
        self.assertIn('Slider(value: $probe.filterMix, in: 0...0.25',app)
        # Guards still present in both layers (polling and HAL notification re-check).
        self.assertEqual(app.count('ProbeGuard.difference(from: base'), 2)
        self.assertIn('hal?.stop(); hal = nil',app)
        with self.assertRaises(AssertionError): check(app, route.replace('channel - first, sampleTime','0, sampleTime'))
        with self.assertRaises(AssertionError): check(app.replace('ProbeGuard.difference(from: base, to: ProbeInput.snapshot(d.deviceID))','nil'), route)
        self.assertNotIn('AudioObjectSetPropertyData',route)
        self.assertIn('AudioUnitGetProperty',route)
        self.assertIn('kAudioDeviceTransportTypeVirtual',route)
        METRICS['wiring_scope'] = 'source contract only; not Apple compilation or HAL validation'

    @unittest.skipUnless('--speech-dir' in sys.argv, 'Existing local real speech only, never downloaded by tests')
    def test_existing_real_speech(self):
        entries = []
        for name in ('6930-75918-0000-clean.wav','6930-75918-0006-clean.wav','6930-75918-0007-clean.wav'):
            path = ARGS.speech_dir/name
            wav = load_wav(path)
            self.assertEqual(wav['sample_rate'],48000); self.assertEqual(wav['channels'],1)
            source = wav['samples'][0][:720000]
            for scale in (1,.01):
                raw,wet = dsp.capture([v*scale for v in source])
                rc,a,b,s = prepare(raw,wet,gain=0 if scale == 1 else 24)
                self.assertEqual(rc,0); content_oracle(a,b)
                # Independent windows ensure total RMS cannot hide muted chunks.
                deltas = []
                for i in range(0,len(a)-2400+1,2400):
                    ra,rb = rms(a[i:i+2400]),rms(b[i:i+2400])
                    if ra > 1e-6:
                        delta = 20*math.log10(max(rb,1e-30)/ra)
                        self.assertGreater(delta,-6, 'real speech 50ms window lost >6dB')
                        deltas.append(delta)
                entries.append({'name':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'frames':len(source),'scale':scale,'levels':s,'min_50ms_delta_db':min(deltas)})
        METRICS['real_speech'] = entries
        # Real public speech plus declared synthetic mains; not a Hollyland recording.
        noisy = []
        for name in ('6930-75918-0000-clean.wav','6930-75918-0006-clean.wav','6930-75918-0007-clean.wav'):
            source = load_wav(ARGS.speech_dir/name)['samples'][0][:720000]
            reference = [v*.01 for v in source]
            mixture = [v+.03*math.sin(2*math.pi*60*i/48000) for i,v in enumerate(reference)]
            raw, wet = dsp.capture(mixture)
            rc,a,b,s = prepare(raw,wet,gain=12)
            self.assertEqual(rc,0); content_oracle(a,b)
            aa = sum(v*t for v,t in zip(a,reference))
            bb = sum(v*t for v,t in zip(b,reference))
            ratio = bb/aa
            self.assertGreater(ratio,.5, 'known real speech projection lost >6dB')
            noisy.append({'name':name,'speech_scale':.01,'synthetic_hum_peak':.03,
                'speech_projection_delta_db':20*math.log10(ratio),'levels':s})
        METRICS['real_speech_plus_synthetic_hum'] = noisy


def main():
    global LIB,ARGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--speech-dir',type=Path)
    ARGS = parser.parse_args()
    if ARGS.out.exists(): raise SystemExit('Output exists')
    ARGS.out.parent.mkdir(parents=True,exist_ok=True)
    LIB = c.CDLL(str(ARGS.library.resolve())); dsp.LIBRARY = ARGS.library.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    report = {'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'ok':result.wasSuccessful(),'mac_audio_tested':False,'metrics':METRICS,
        'library_sha256':hashlib.sha256(ARGS.library.read_bytes()).hexdigest()}
    ARGS.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    raise SystemExit(not result.wasSuccessful())

if __name__ == '__main__': main()
