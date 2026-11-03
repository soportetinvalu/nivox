#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic classifier unit tests; NOT native audio or overload evidence."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'callaudio/tools/classify_e2e.py'
spec = importlib.util.spec_from_file_location('classify_e2e', SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ClassificationTests(unittest.TestCase):
    def scenario(self, *, passed=False, overload=True, jump=True, defect=None):
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='e2e-classification-', dir=ROOT / 'build') as tmp:
            out = Path(tmp)
            (out / 'e2e.json').write_text(json.dumps({'ok': passed, 'failures': 0 if passed else 3,
                                                   'notes': '' if passed else 'FAIL loopback glitches=5'}))
            (out / 'e2e.exitcode').write_text('0' if passed else '1')
            (out / 'e2e.stderr').write_text('original stderr\n')
            (out / 'coreaudio.log').write_text('Audio IO Overload thread: test\n' if overload else 'no event\n')
            cap = out / 'e2e-captures'
            cap.mkdir()
            for i in range(1, 8):
                name = cap / f'capture-{i:02d}'
                name.with_suffix('.json').write_text(json.dumps({'generator_timestamp_jumps': int(jump),
                                                               'generator_max_timestamp_jump': 776 if jump else 0}))
                name.with_suffix('.csv').write_text('index,host_ns,sample_time,frames,flags\n0,100,0,512,1\n512,200,512,512,1\n')
                name.with_suffix('.f32').write_bytes(b'\0' * 16)
            if defect == 'missing-json':
                (out / 'e2e.json').unlink()
            if defect == 'bad-json':
                (out / 'e2e.json').write_text('{')
            if defect == 'bad-rc':
                (out / 'e2e.exitcode').write_text('1')
            if defect == 'missing-capture':
                (cap / 'capture-07.f32').unlink()
            result = module.classify(out)
            # Exercise the real CLI: diagnostics must not turn the E2E green.
            proc = subprocess.run(['python3', str(SCRIPT), str(out), '--report', str(out / 'diagnosis.json')],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0 if result['ok'] else 1, proc.stderr)
            self.assertEqual(json.loads((out / 'diagnosis.json').read_text()), result)
            return result

    def test_overload_evidence_does_not_pass_or_prove_root_cause(self):
        r = self.scenario()
        self.assertEqual(r['classification'], 'failed_with_host_overload_evidence')
        self.assertFalse(r['ok'])
        self.assertFalse(r['e2e_passed'])
        self.assertFalse(r['root_cause_proven'])
        self.assertEqual(r['original_failures'], 3)
        self.assertEqual(r['original_notes'], 'FAIL loopback glitches=5')
        self.assertIn('e2e-captures/capture-01.f32', r['input_sha256'])

    def test_jumps_alone_do_not_prove_overload(self):
        self.assertEqual(self.scenario(overload=False)['classification'], 'failed_without_confirmed_host_overload')

    def test_logs_alone_do_not_prove_overload(self):
        self.assertEqual(self.scenario(jump=False)['classification'], 'failed_without_confirmed_host_overload')

    def test_true_success_is_not_reclassified_as_failure_by_logs(self):
        self.assertTrue(self.scenario(passed=True)['ok'])

    def test_missing_or_broken_json_fails_closed(self):
        for defect in ['missing-json', 'bad-json']:
            with self.subTest(defect=defect):
                self.assertFalse(self.scenario(defect=defect)['ok'])

    def test_nonzero_rc_cannot_be_overridden_by_ok_json(self):
        self.assertFalse(self.scenario(passed=True, defect='bad-rc')['ok'])

    def test_missing_capture_cannot_certify_diagnostics(self):
        r = self.scenario(passed=True, defect='missing-capture')
        self.assertFalse(r['ok'])
        self.assertTrue(r['diagnostic_issues'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
