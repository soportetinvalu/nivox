#!/usr/bin/env python3
"""Recovery harness regression using unmodified stderr from GitHub run 41694069376.

Source: artifact 13262290198, callaudio-mac/cli-recovery.jsonl, job 126120695677.
Mutations below are negative probes, not additional Apple executions.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "callaudio/tools/verify_cli_recovery.py"
spec = importlib.util.spec_from_file_location("verify_cli_recovery", TOOL)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
RAW = (Path(__file__).parent / "fixtures/cli-recovery-41694069376.stderr").read_text()


def events():
    return [json.loads(line) for line in RAW.splitlines() if line != verifier.SPEEX_VAD_WARNING]


def render(items):
    return "".join(json.dumps(e) + "\n" for e in items)


class RecoveryTests(unittest.TestCase):
    def test_actual_artifact_recovers_and_preserves_warning(self):
        result = verifier.verify_recovery(RAW)
        self.assertEqual(result["restart_count"], 1)
        self.assertEqual(result["post_restart_stats"], 7)
        self.assertEqual(result["first_counters"], {"pushed": 82560, "pulled": 83520})
        self.assertEqual(result["last_counters"], {"pushed": 710880, "pulled": 711360})
        self.assertEqual([d["line"] for d in result["diagnostics"]], [1, 6])

    def test_original_parser_reproduces_ci_failure(self):
        with self.assertRaises(json.JSONDecodeError):
            [json.loads(line) for line in RAW.splitlines()]

    def test_valid_json_only_is_also_accepted(self):
        self.assertEqual(verifier.verify_recovery(render(events()))["diagnostics"], [])

    def test_missing_lifecycle_evidence_rejected(self):
        for name, items, message in (
            ("no restart", [e for e in events() if e["event"] != "restart"], "no restart"),
            ("no initial start", events()[1:], "no initial start"),
            ("no reopened path", events()[:4] + events()[5:], "no start after"),
            ("pre-restart stats only", events()[:5] + events()[-1:], "at least two stats"),
            ("no clean stop", events()[:-1], "stop cleanly"),
            ("empty log", [], "no restart"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                verifier.verify_recovery(render(items))

    def test_frozen_or_broken_callbacks_rejected(self):
        for key, value, message in (
            ("pushed", 82560, "did not advance"),
            ("pulled", 83520, "did not advance"),
            ("pushed", 0, "invalid pushed"),
            ("pulled", 0, "invalid pulled"),
            ("primed", 0, "not primed"),
            ("underruns", 1, "underruns or overflows"),
            ("overflows", 1, "underruns or overflows"),
            ("path", "mic", "missing or duplicate"),
        ):
            items = events()
            items[6]["paths"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, message):
                verifier.verify_recovery(render(items))

    def test_later_unrecovered_restart_cannot_use_earlier_stats(self):
        items = events()
        items.insert(-1, {"event": "restart", "path": "speaker"})
        with self.assertRaisesRegex(ValueError, "no start after final restart"):
            verifier.verify_recovery(render(items))

    def test_errors_and_unknown_diagnostics_are_not_silently_filtered(self):
        for line, message in (
            ('{"event":"error","detail":"open failed"}', "CLI error"),
            ('{"event":"stats",', "malformed JSON"),
            ("warning: unexpected diagnostic", "unexpected diagnostic"),
            ("[]", "invalid event"),
            ("{}", "invalid event"),
        ):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, message):
                verifier.verify_recovery(line + "\n" + RAW)

    def test_command_line_on_real_fixture(self):
        proc = subprocess.run([sys.executable, str(TOOL), str(Path(__file__).parent /
                              "fixtures/cli-recovery-41694069376.stderr")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["recovered_after_coreaudiod_restart"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
