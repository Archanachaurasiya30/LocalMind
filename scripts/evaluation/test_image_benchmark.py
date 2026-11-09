import copy
import unittest
from image_benchmark import load_spec, hardware_gate, quality_gate


class ImageGateTests(unittest.TestCase):
    def test_missing_review_and_critical_failures_never_pass(self):
        spec = load_spec()
        self.assertFalse(quality_gate({}, spec))
        review = {'reviewer': 'test reviewer', 'human_review_complete': True, 'revision': spec['revision'],
                  'scores': {item['id']: {'prompt_adherence': True, 'readable_exact_text': True,
                                        'spatial_composition': True, 'anatomy': True} for item in spec['prompts']}}
        self.assertTrue(quality_gate(review, spec))
        for item, field in [('text', 'readable_exact_text'), ('composition', 'spatial_composition'), ('hands', 'anatomy')]:
            bad = copy.deepcopy(review)
            bad['scores'][item][field] = False
            self.assertFalse(quality_gate(bad, spec))

    def test_fixed_prompts_and_unreviewed_hardware_gate(self):
        spec = load_spec()
        self.assertEqual(len(spec["revision"]), 40)
        self.assertEqual(spec["configuration"]["batch_size"], 1)
        rows = [{"status": "completed", "seconds": 10, "peak_vram_mib": 5000, "peak_rss_mib": 8000} for _ in range(10)]
        self.assertTrue(hardware_gate(rows, spec))
        self.assertFalse(hardware_gate(rows[:9], spec))
        for key, value in [("status", "failed"), ("peak_vram_mib", 6000), ("peak_rss_mib", 13000)]:
            bad = copy.deepcopy(rows)
            bad[0][key] = value
            self.assertFalse(hardware_gate(bad, spec))
        self.assertTrue(spec["gate"]["human_visual_review_required"])
