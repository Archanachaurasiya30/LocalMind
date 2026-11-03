import io
import json
from pathlib import Path
import tempfile
import unittest
from compare_chat_policy import load_cases, case_checks, stream_response, summarize, validate_local_model


class PolicyComparisonTests(unittest.TestCase):
    def test_twenty_distinct_synthetic_cases_load_and_known_violations_fail(self):
        cases = load_cases(Path(__file__).resolve().parents[2] / "evaluation/chat-quality-cases.json")
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case["id"] for case in cases}), 20)
        self.assertTrue(all(case_checks(cases[1], "Friday").values()))
        self.assertFalse(all(case_checks(cases[1], "Monday").values()))
        self.assertFalse(all(case_checks(cases[3], "15 outline; 20 demo; 10 rehearse").values()))

    def test_rejects_duplicate_ids_and_unsupported_constraints(self):
        case = {"id": "x", "prompt": "Hi", "history": [], "constraints": {"exact": "Hi"}}
        for cases in ([case, case], [{**case, "constraints": {"execute": "x"}}], [{**case, "constraints": {"bullets": True}}]):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "cases.json"
                path.write_text(json.dumps({"schema_version": 1, "cases": cases}))
                with self.assertRaises(ValueError):
                    load_cases(path)

    def test_stream_collects_only_answer_text_and_requires_terminal_receipt(self):
        class Opener:
            def __init__(self, chunks): self.chunks = chunks
            def open(self, *_args, **_kwargs): return io.BytesIO(b"\n".join(json.dumps(chunk).encode() for chunk in self.chunks))
        chunks = [{"message": {"thinking": "hidden"}}, {"message": {"content": "Hello"}}, {"done": True, "eval_count": 1}]
        text, terminal, elapsed, first = stream_response(Opener(chunks), {})
        self.assertEqual(text, "Hello")
        self.assertEqual(terminal["eval_count"], 1)
        self.assertLessEqual(first, elapsed)
        with self.assertRaises(ValueError):
            stream_response(Opener(chunks[:-1]), {})
        with self.assertRaises(ValueError):
            stream_response(Opener([{"message": {"content": 4}}]), {})

    def test_infrastructure_failure_is_not_counted_as_pass_and_no_default_is_approved(self):
        report = summarize([{"arm": "minimal", "status": "error", "checks": {}, "seconds": 5},
                            {"arm": "minimal", "status": "completed", "checks": {"exact": True}, "seconds": 2}])
        self.assertEqual(report["minimal"]["mechanical_passes"], 1)
        self.assertEqual(report["minimal"]["errors"], 1)
        self.assertEqual(report["minimal"]["median_seconds"], 2)
        self.assertEqual(report["minimal"]["human_review"], "pending")
        self.assertFalse(report["minimal"]["default_change_authorized"])

    def test_requires_local_chat_weights_and_caps_requested_context(self):
        installed = {"digest": "a" * 64}
        metadata = {"capabilities": ["completion"], "model_info": {"fixture.context_length": 8192}}
        self.assertEqual(validate_local_model(metadata, installed), 8192)
        for shown, local in (({**metadata, "remote_host": "cloud"}, installed),
                             ({**metadata, "capabilities": ["embedding"]}, installed), (metadata, {})):
            with self.assertRaises(ValueError):
                validate_local_model(shown, local)


if __name__ == "__main__":
    unittest.main()
