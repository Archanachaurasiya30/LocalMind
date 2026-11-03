import unittest
from unittest.mock import AsyncMock, patch
import httpx
from app.model_readiness import readiness, quality_notice
from app.api.routes.models import model_readiness, ProfileRequest


class ModelReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_reports_exact_profile_and_changed_settings_notice(self):
        profile = {"digest": "a" * 64, "context_window": 8192, "response_tokens": 1024,
                   "settings_notice": "The model changed; settings were reset."}
        with patch("app.model_readiness.resolve_profile", AsyncMock(return_value=profile)) as inspect:
            result = await model_readiness(ProfileRequest(model="qwen2.5:1.5b", preset="speed"))
        inspect.assert_awaited_once_with("qwen2.5:1.5b", "speed")
        self.assertTrue(result["ready"])
        self.assertEqual(result["digest"], "a" * 64)
        self.assertIn("miss instructions", result["notice"])
        self.assertEqual(result["settings_notice"], profile["settings_notice"])

    async def test_missing_embedding_remote_and_invalid_profiles_never_become_ready(self):
        for reason in ("This model is not installed with an identifiable digest.",
                       "This installed model does not advertise text completion.",
                       "Choose a local model for this profile.",
                       "Context must fit within the installed model's supported capacity."):
            with self.subTest(reason=reason), patch("app.model_readiness.resolve_profile", AsyncMock(side_effect=ValueError(reason))):
                result = await readiness("fixture", "balanced")
                self.assertFalse(result["ready"])
                self.assertEqual(result["message"], reason)
                self.assertEqual(result["state"], "needs_setup")

    async def test_offline_is_distinct_from_missing_model_and_hides_transport_details(self):
        with patch("app.model_readiness.resolve_profile", AsyncMock(side_effect=httpx.ConnectError("private transport detail"))):
            result = await readiness("fixture", "balanced")
        self.assertEqual(result["state"], "offline")
        self.assertNotIn("private", str(result))
        self.assertFalse(result["ready"])

    def test_size_notice_is_not_a_quality_guarantee(self):
        self.assertIsNotNone(quality_notice("qwen2.5:1.5b"))
        self.assertIsNotNone(quality_notice("llama:3b-q4"))
        self.assertIsNone(quality_notice("qwen3:8b"))
        self.assertIsNone(quality_notice("custom-model"))
