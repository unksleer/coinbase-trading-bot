import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from utils.learner_policy import (
    MODE_ADVISORY,
    MODE_APPLY_APPROVED,
    assess_candidate,
    atomic_write_json,
    load_approved_ids,
    normalize_mode,
    recommendation_id,
    should_apply_recommendation,
)


class LearnerV2PolicyTests(unittest.TestCase):
    def test_unknown_or_missing_mode_fails_safe_to_advisory(self):
        self.assertEqual(normalize_mode(None), MODE_ADVISORY)
        self.assertEqual(normalize_mode("automatic"), MODE_ADVISORY)
        self.assertEqual(normalize_mode("apply_approved"), MODE_APPLY_APPROVED)

    def test_advisory_mode_cannot_apply_even_when_id_is_approved(self):
        self.assertFalse(should_apply_recommendation(MODE_ADVISORY, "abc", {"abc"}))
        self.assertTrue(should_apply_recommendation(MODE_APPLY_APPROVED, "abc", {"abc"}))
        self.assertFalse(should_apply_recommendation(MODE_APPLY_APPROVED, "abc", set()))

    def test_candidate_must_pass_every_evidence_gate(self):
        candidate = {
            "val_trades": 8,
            "val_net_profit": 2.0,
            "val_profit_factor": 1.4,
            "profitable_folds": 2,
            "validation_folds": 2,
            "stability_pass_rate": 0.75,
        }
        decision = assess_candidate(
            candidate, {"val_net_profit": 0.5}, benchmark_profit=0.25
        )
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["reasons"], [])

        candidate["profitable_folds"] = 1
        candidate["stability_pass_rate"] = 0.25
        decision = assess_candidate(
            candidate, {"val_net_profit": 0.5}, benchmark_profit=0.25
        )
        self.assertFalse(decision["eligible"])
        self.assertTrue(any("folds" in reason for reason in decision["reasons"]))
        self.assertTrue(any("stability" in reason for reason in decision["reasons"]))

    def test_recommendation_id_is_stable_and_parameter_specific(self):
        params = {"short": 5, "long": 30, "stop_loss": 5, "take_profit": 8}
        first = recommendation_id("btc-usdc", params)
        second = recommendation_id("BTC-USDC", dict(reversed(list(params.items()))))
        self.assertEqual(first, second)
        changed = dict(params, long=40)
        self.assertNotEqual(first, recommendation_id("BTC-USDC", changed))

    def test_approval_file_and_atomic_recommendation_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            approval_path = Path(temp_dir) / "approvals.json"
            approval_path.write_text(json.dumps({"approved": ["abc", "def"]}))
            self.assertEqual(load_approved_ids(approval_path), {"abc", "def"})

            output_path = Path(temp_dir) / "recommendations.json"
            payload = {"mode": MODE_ADVISORY, "recommendations": [{"id": "abc"}]}
            atomic_write_json(output_path, payload)
            self.assertEqual(json.loads(output_path.read_text()), payload)

    def test_malformed_approval_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "approvals.json"
            path.write_text("not-json")
            self.assertEqual(load_approved_ids(path), set())

    def test_atomic_json_converts_scalar_like_values(self):
        class ScalarLike:
            def item(self):
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            atomic_write_json(path, {"passed": ScalarLike()})
            self.assertEqual(json.loads(path.read_text()), {"passed": True})

    def test_review_command_only_approves_known_recommendation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            recommendations = Path(temp_dir) / "recommendations.json"
            approvals = Path(temp_dir) / "approvals.json"
            recommendations.write_text(json.dumps({
                "recommendations": [{
                    "id": "known", "pair": "BTC-USDC",
                    "proposed": {"short": 5, "long": 30, "stop_loss": 5, "take_profit": 8},
                    "evidence": {
                        "validation_net_profit": 1.0,
                        "validation_profit_factor": 1.2,
                        "validation_trades": 8,
                        "stability_pass_rate": 0.75,
                    },
                }]
            }))
            root = Path(__file__).resolve().parents[1]
            unknown = subprocess.run([
                sys.executable, "learner_review.py", "--approve", "unknown",
                "--recommendations", str(recommendations), "--approvals", str(approvals),
            ], cwd=root, capture_output=True, text=True)
            self.assertEqual(unknown.returncode, 2)
            self.assertFalse(approvals.exists())

            known = subprocess.run([
                sys.executable, "learner_review.py", "--approve", "known",
                "--recommendations", str(recommendations), "--approvals", str(approvals),
            ], cwd=root, capture_output=True, text=True)
            self.assertEqual(known.returncode, 0)
            self.assertEqual(load_approved_ids(approvals), {"known"})


if __name__ == "__main__":
    unittest.main()
