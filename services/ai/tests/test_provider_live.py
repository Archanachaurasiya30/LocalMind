"""Explicit paid smoke test; excluded unless the developer opts in.

Set LOCALMIND_PROVIDER_LIVE_TEST=1 and LOCALMIND_PROVIDER_LIVE=openai (or
anthropic/gemini). Configure that provider, its key and consent in the isolated
test profile first. This uses only a synthetic prompt, never saved conversations.
The configured account may charge for one short generation. No API key is read
from an environment variable or copied into the test output.
"""

import os
import unittest
from uuid import uuid4

from app import provider_store, providers


@unittest.skipUnless(os.environ.get("LOCALMIND_PROVIDER_LIVE_TEST") == "1", "Paid provider smoke test requires explicit opt-in")
class LiveProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_short_synthetic_response(self):
        provider = os.environ.get("LOCALMIND_PROVIDER_LIVE", "")
        provider_store.provider_id(provider)
        cfg = provider_store.config(provider)
        self.assertTrue(cfg["model"], "Configure the model in the selected isolated profile first.")
        self.assertIsNotNone(cfg["consent_id"], "Approve messages for this provider in the selected profile first.")
        events = [event async for event in providers.stream_chat(
            provider, cfg["model"], [{"role": "user", "content": "Reply with one short greeting."}],
            scopes=["messages"], request_id=str(uuid4()), consent_id=cfg["consent_id"], max_tokens=128,
        )]
        self.assertFalse(any(event["type"] == "error" for event in events), "Provider smoke failed; check the selected account and model.")
        self.assertTrue(any(event["type"] == "token" and str(event["content"]).strip() for event in events))
        self.assertEqual(events[-1]["type"], "done")
