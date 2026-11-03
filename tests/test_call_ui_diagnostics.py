#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit tests of failure aggregation only; synthetic records are NOT Mac evidence."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from typing import Any

from test_call_ui_mac import ROOT, collect_diagnostics, verify_matrix


def synthetic_runtime():
    return {'ok': True, 'phase': 'absent', 'matrix_expected': 8, 'matrix_completed': 8,
            'opaque_capture_selftests_passed': True, 'original_capture_regression_passed': True,
            'text_visibility_checks': [
                {'viewport_width': w, 'viewport_height': h, 'tabbed_production_root': tab,
                 'ok': True, 'viewport_preserved': True, 'window_fits_visible_screen': True, 'failures': []}
                for w in [780, 1000] for tab in [False, True]
                for h in ([592, 620] if tab else [560, 620])]}


class DiagnosticOrchestrationTests(unittest.TestCase):
    def scenario(self, defect=None):
        (ROOT / 'build').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='diagnostic-unit-', dir=ROOT / 'build') as tmp:
            out = Path(tmp)
            calls = []
            runtime = synthetic_runtime()
            if defect == 'viewport':
                runtime['ok'] = False
                runtime['text_visibility_checks'][1].update(
                    ok=False, viewport_preserved=False, failures=['viewport expanded'])
            if defect == 'incomplete':
                runtime['text_visibility_checks'].pop()
                runtime['matrix_completed'] = 7
            if defect == 'screen':
                runtime['text_visibility_checks'][0]['window_fits_visible_screen'] = False

            def run(command, expect=True):
                self.assertFalse(expect)
                calls.append(Path(command[1]).name)
                destination = Path(command[1])
                if destination == out:
                    if defect == 'timeout':
                        raise subprocess.TimeoutExpired(command, 120, output='partial native stdout')
                    if defect != 'missing-runtime':
                        (out / 'runtime.json').write_text(json.dumps(runtime))
                    return subprocess.CompletedProcess(command, 1 if defect == 'viewport' else 0, '', '')
                if destination.name == 'counterprobe':
                    message = 'unrelated failure' if defect == 'opposite' else 'real HAL driver presence disagrees with phase'
                    return subprocess.CompletedProcess(command, 1, '', message)
                self.assertEqual(destination.name, 'truncation-counterprobe')
                (destination / 'call-780x560-text.json').write_text(
                    json.dumps({'ok': False, 'missing_spans': ['uno dos tres'],
                                'viewport_preserved': defect != 'mutant-viewport', 'visible_scroll_count': 1,
                                'failures': ['significant text not visible in call-780x560: uno dos tres']}))
                mutant_runtime = synthetic_runtime()
                mutant_runtime['ok'] = False
                for c in mutant_runtime['text_visibility_checks']:
                    c.update(ok=False, missing_spans=['uno dos tres'], visible_scroll_count=1,
                             failures=['significant text not visible: uno dos tres'])
                if defect == 'mutant-tabbed':
                    mutant_runtime['text_visibility_checks'][2]['failures'] = ['capture failed']
                (destination / 'runtime.json').write_text(json.dumps(mutant_runtime))
                message = 'compiler failure' if defect == 'truncation' else 'significant text not visible'
                return subprocess.CompletedProcess(command, 1, '', message)

            def compile_mutant():
                calls.append('compile_mutant')
                return out / 'mutant'

            report: dict[str, Any] = {'ok': False}
            collect_diagnostics(run, out / 'binary', out, 'absent', compile_mutant, report)
            self.assertEqual(calls, [out.name, 'counterprobe', 'compile_mutant', 'truncation-counterprobe'])
            return copy.deepcopy(report)

    def test_all_checks_pass(self):
        report = self.scenario()
        self.assertTrue(report['ok'])
        self.assertTrue(report['opposite_driver_phase_rejected'])
        self.assertTrue(report['real_truncation_rejected'])

    def test_viewport_failure_keeps_runtime_and_runs_both_counterprobes(self):
        report = self.scenario('viewport')
        self.assertFalse(report['ok'])
        self.assertFalse(report['runtime']['ok'])
        self.assertEqual([f['stage'] for f in report['failures']], ['production_matrix'])
        self.assertTrue(report['real_truncation_rejected'])

    def test_timeout_does_not_suppress_counterprobes(self):
        report = self.scenario('timeout')
        self.assertFalse(report['ok'])
        self.assertEqual(report['failures'][0]['stage'], 'production_matrix')
        self.assertTrue(report['real_truncation_rejected'])

    def test_missing_runtime_does_not_suppress_counterprobes(self):
        self.assertFalse(self.scenario('missing-runtime')['ok'])

    def test_incomplete_matrix_cannot_pass(self):
        self.assertFalse(self.scenario('incomplete')['ok'])

    def test_opposite_phase_requires_specific_failure(self):
        report = self.scenario('opposite')
        self.assertFalse(report['ok'])
        self.assertEqual(report['failures'][0]['stage'], 'opposite_driver_phase')
        self.assertTrue(report['real_truncation_rejected'])

    def test_truncation_requires_specific_failure(self):
        report = self.scenario('truncation')
        self.assertFalse(report['ok'])
        self.assertEqual(report['failures'][0]['stage'], 'real_truncation')
        self.assertNotIn('real_truncation_rejected', report)

    def test_duplicate_case_cannot_replace_missing_case(self):
        runtime = synthetic_runtime()
        runtime['text_visibility_checks'][-1] = runtime['text_visibility_checks'][0]
        with self.assertRaises(AssertionError):
            verify_matrix(runtime)

    def test_truncation_at_wrong_viewport_does_not_count(self):
        report = self.scenario('mutant-viewport')
        self.assertFalse(report['ok'])
        self.assertNotIn('real_truncation_rejected', report)

    def test_matrix_count_must_agree(self):
        runtime = synthetic_runtime()
        runtime['matrix_completed'] = 7
        with self.assertRaises(AssertionError):
            verify_matrix(runtime)


    def test_offscreen_window_cannot_pass(self):
        self.assertFalse(self.scenario('screen')['ok'])

    def test_tabbed_mutant_unrelated_failure_cannot_certify_oracle(self):
        report = self.scenario('mutant-tabbed')
        self.assertFalse(report['ok'])
        self.assertNotIn('real_truncation_rejected', report)

    def test_old_impossible_dimensions_cannot_pass(self):
        runtime = synthetic_runtime()
        runtime['text_visibility_checks'][2]['viewport_height'] = 560
        with self.assertRaises(AssertionError):
            verify_matrix(runtime)


if __name__ == '__main__':
    unittest.main(verbosity=2)
