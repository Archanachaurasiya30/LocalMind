import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import compare_experience as comparison


class ExperienceSuiteTests(unittest.TestCase):
    def test_frozen_suite_has_distinct_tasks_multiturn_and_separate_holdout(self):
        cases = comparison.load_suite()
        self.assertEqual(len(cases), 60)
        self.assertEqual(sum(len(c["messages"]) > 1 for c in cases), 20)
        self.assertGreaterEqual(len({c["category"] for c in cases}), 10)
        development = {c["id"] for c in cases if c["split"] == "development"}
        heldout = {c["id"] for c in cases if c["split"] == "held_out"}
        self.assertTrue(development.isdisjoint(heldout))
        self.assertEqual(len(heldout), 20)

    def test_duplicate_tasks_and_broken_roles_are_rejected(self):
        data = json.loads(comparison.DEFAULT_SUITE.read_text(encoding="utf-8"))
        for mutation in ("duplicate", "role", "split"):
            bad = copy.deepcopy(data)
            if mutation == "duplicate":
                bad["cases"][1]["messages"] = bad["cases"][0]["messages"]
            elif mutation == "role":
                bad["cases"][0]["messages"][1]["role"] = "system"
            else:
                bad["cases"][0]["split"] = "training"
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "suite.json"
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    comparison.load_suite(path)

    def test_preparation_never_contacts_a_model_or_requires_a_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(comparison, "execute_one") as execute:
            comparison.main(["prepare", "--out", folder])
            execute.assert_not_called()
            manifest = json.loads((Path(folder) / "manifest.json").read_text())
            self.assertEqual(manifest["cases"], 40)
            self.assertEqual(manifest["human_review"], "pending")

    def test_live_and_paid_runs_need_explicit_flags_before_any_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(comparison, "execute_one") as execute:
            for flags in ([], ["--execute"]):
                with self.subTest(flags=flags), self.assertRaises(SystemExit):
                    comparison.main(["run", "--out", folder, "--arm", "app:openai:fixture",
                                     "--arm", "app:anthropic:fixture", *flags])
            execute.assert_not_called()

    def test_only_loopback_service_urls_and_supported_arms_are_accepted(self):
        self.assertEqual(comparison.local_service_url("http://127.0.0.1:8765/"), "http://127.0.0.1:8765")
        for url in ("https://example.com", "http://user:secret@localhost:8765",
                    "http://localhost:8765/path", "http://localhost:8765/?token=secret", "http://localhost"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                comparison.local_service_url(url)
        with self.assertRaises(ValueError):
            comparison.parse_arm("direct:openai:fixture")
        self.assertEqual(comparison.parse_arm("direct:ollama:qwen3:8b")["model"], "qwen3:8b")


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.case = comparison.load_suite()[0]
        self.arm = comparison.parse_arm("app:openai:fixture")

    def run_stream(self, lines):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"".join(lines))
        result = comparison.execute_one(self.case, self.arm, "http://127.0.0.1:8765", "synthetic-token", opener=opener)
        return result, opener

    def test_partial_output_and_error_are_kept_without_replaying_paid_request(self):
        result, opener = self.run_stream([
            b'data: {"type":"token","content":"A useful partial"}\n\n',
            b'data: {"type":"error","content":"secret upstream text"}\n\n',
        ])
        self.assertEqual(result["answer"], "A useful partial")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "provider_or_service_error")
        self.assertNotIn("secret", json.dumps(result))
        opener.open.assert_called_once()

    def test_done_receipt_required_and_no_empty_answer_pass(self):
        for lines, status in (([b'data: {"type":"token","content":"hi"}\n\n'], "error"),
                              ([b'data: {"type":"done"}\n\n'], "empty"),
                              ([b'data: {"type":"token","content":"hi"}\n\n',
                                b'data: {"type":"done"}\n\n'], "completed")):
            with self.subTest(status=status):
                result, _ = self.run_stream(lines)
                self.assertEqual(result["status"], status)

    def test_reported_usage_maps_cost_without_inventing_missing_prices(self):
        result, _ = self.run_stream([
            b'data: {"type":"token","content":"answer"}\n\n',
            b'data: {"type":"usage","input_tokens":20,"output_tokens":5,"cost_usd":0.002}\n\n',
            b'data: {"type":"done"}\n\n',
        ])
        self.assertEqual(result["estimated_cost_usd"], 0.002)
        failed = {**result, "status": "error", "estimated_cost_usd": None}
        summary = comparison.summarize_runs([result, failed])[self.arm["id"]]
        self.assertEqual(summary["requests_with_unknown_cost"], 1)
        self.assertEqual(summary["known_estimated_cost_usd"], 0.002)
        self.assertEqual(summary["status_counts"], {"completed": 1, "error": 1})

    def test_oversized_output_rejects_before_json_decoding(self):
        result, _ = self.run_stream([b"x" * (comparison.MAX_LINE + 1)])
        self.assertEqual(result["status"], "error")

    def test_ollama_stream_records_visible_text_and_timing_not_thinking(self):
        arm = comparison.parse_arm("direct:ollama:fixture")
        opener = Mock()
        opener.open.return_value = io.BytesIO(
            b'{"message":{"thinking":"private reasoning","content":"Hello"},"done":false}\n'
            b'{"message":{"content":""},"done":true,"eval_count":4}\n')
        result = comparison.execute_one(self.case, arm, "http://127.0.0.1:8765", "", opener=opener)
        self.assertEqual(result["answer"], "Hello")
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertEqual(result["usage"]["eval_count"], 4)

    def test_local_cloud_alias_is_rejected_before_generation(self):
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(json.dumps({"models": [{"name": "fixture", "digest": "a" * 64}]}).encode()),
            io.BytesIO(json.dumps({"remote_host": "https://cloud.invalid", "capabilities": ["completion"]}).encode()),
        ]
        with self.assertRaisesRegex(ValueError, "cloud alias"):
            comparison.verify_local_model("fixture", opener)
        self.assertEqual(opener.open.call_count, 2)


class BlindReviewTests(unittest.TestCase):
    def setUp(self):
        self.cases = comparison.load_suite()[:2]
        self.rows = [{"case_id": case["id"], "arm": arm, "answer": answer, "status": "completed"}
                     for case in self.cases for arm, answer in (
                         ("app:openai:one", "Concise useful answer"),
                         ("direct:ollama:two", "Longer answer with more words but no automatic preference"))]

    def test_review_hides_arm_names_and_metrics_and_is_reproducibly_shuffled(self):
        review, key = comparison.blind_review(self.cases, self.rows, 17)
        self.assertEqual((review, key), comparison.blind_review(self.cases, self.rows, 17))
        text = json.dumps(review)
        self.assertNotIn("openai", text)
        self.assertNotIn("ollama", text)
        self.assertNotIn("total_seconds", text)
        self.assertEqual(len(review["cards"]), 4)
        self.assertEqual(sum(row["mirror_of"] is not None for row in key["keys"]), 2)

    def test_scores_require_human_input_and_mirrors_detect_position_preference(self):
        review, key = comparison.blind_review(self.cases, self.rows, 17)
        with self.assertRaises(ValueError):
            comparison.score_review(review, key)
        for card in review["cards"]:
            card["preferred"] = "A"  # Always choosing the first position should fail mirror consistency.
            for answer in card["answers"].values():
                answer["scores"] = dict.fromkeys(comparison.SCORE_FIELDS, 3)
        summary = comparison.score_review(review, key)
        self.assertEqual(summary["position_audit"], {"paired_cards": 2, "consistent_choices": 0})
        self.assertEqual(sum(summary["preference_counts"].values()), 2)  # Mirrors never double-count quality.
        first = review["cards"][0]
        first["answers"]["A"]["scores"]["correctness"] = True
        with self.assertRaises(ValueError):
            comparison.score_review(review, key)


if __name__ == "__main__":
    unittest.main()
