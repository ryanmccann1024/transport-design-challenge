"""Check that ordinary public runs report and enforce the public criteria."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliChecks(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.policy = self.folder / "policy.json"

    def write_policy(self, rate):
        self.policy.write_text(json.dumps({
            "bulk_rate_bps": rate,
            "bulk_queue_bytes": 4_000,
            "control_first": True,
            "on_disconnect": "drop",
        }))

    def run_case(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "netlab", *args],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_public_run_reports_video_and_update_targets(self):
        self.write_policy(2_000_000)
        result = self.run_case("--case", "outage", "--policy", str(self.policy))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Case: outage [PASS]", result.stdout)
        self.assertIn("Video [PASS]", result.stdout)
        self.assertIn("Target: 20 MB by 125 s", result.stdout)
        self.assertIn("Crew updates [PASS]", result.stdout)
        self.assertIn("2 updates must arrive in under 1,200 ms each", result.stdout)
        self.assertNotIn("camera-status", result.stdout)

    def test_failing_public_run_saves_evidence_and_reports_which_target_missed(self):
        self.write_policy(1_000_000)
        evidence = self.folder / "result.json"
        result = self.run_case("--case", "busy", "--policy", str(self.policy),
                               "--out", str(evidence))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Case: busy [FAIL]", result.stdout)
        self.assertIn("Video [FAIL]", result.stdout)
        self.assertIn("Crew updates [PASS]", result.stdout)
        self.assertIsNone(json.loads(evidence.read_text())["upload_complete_ms"])
