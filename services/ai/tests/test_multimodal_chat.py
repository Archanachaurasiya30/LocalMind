"""Exercise the route-to-provider boundary with real isolated chat/image storage."""
import base64
from io import BytesIO
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from PIL import Image
from app import attachments, provider_store
from app.api.routes import chat
from app.db import conversations, projects


class MultimodalChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_excerpts_match_packed_payload_and_do_not_require_vision(self):
        item = attachments.create('notes.txt', base64.b64encode(b'Unique delivery reference: 30 parcels.').decode(), self.draft, self.cid)
        sources = []
        request = self.request(attachment_ids=[item['id']])
        _, packed, count = await chat._prepare_chat(request, '', '', sources)
        self.assertEqual(count, 0)
        excerpt = next(source for source in sources if source['kind'] == 'document')
        reference_message = next(message for message in packed.messages if message['content'].startswith('Supplied reference data'))
        self.assertIn(excerpt['snippet'], json.loads(reference_message['content'].split('\n', 1)[1])[0]['content'])
        self.assertIn('30 parcels', excerpt['snippet'])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.cid = conversations.create_conversation("Screenshot discussion")["id"]
        provider_store.configure_policy({"local_only": False})
        provider_store.configure("openai", {"model": "test-model"})
        self.draft = str(uuid4())
        image = BytesIO()
        Image.new("RGB", (20, 20), "green").save(image, format="PNG")
        self.file = attachments.create("screenshot.png", base64.b64encode(image.getvalue()).decode(), self.draft)

    def request(self, **values):
        return chat.ChatRequest(**{
            "message": "Describe this screenshot", "model": "test-model", "provider": "openai",
            "conversation_id": self.cid, "connector_context_enabled": False,
            "attachment_ids": [self.file["id"]], "attachment_draft_id": self.draft,
            "consent_id": str(uuid4()), **values,
        })

    async def test_cloud_route_receives_validated_images_canonical_history_and_scopes(self):
        conversations.save_turn(self.cid, "The chart tracks rainfall.", "We can compare the months.")
        captured = {}

        async def stream(provider, model, messages, **kwargs):
            captured.update(provider=provider, model=model, messages=messages, **kwargs)
            yield {"type": "token", "content": "The chart shows rainfall."}
            yield {"type": "done"}

        with patch("app.providers.stream_chat", stream), patch.object(chat, "resolve_profile", AsyncMock()) as local_profile:
            events = [json.loads(line.removeprefix("data: ")) async for line in chat._stream_ollama(self.request(
                history=[{"role": "user", "content": "Another conversation's private topic"}],
            ))]
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("attachments", captured["scopes"])
        self.assertNotIn("Another conversation", json.dumps(captured["messages"]))
        self.assertIn("rainfall", json.dumps(captured["messages"]))
        self.assertEqual(captured["messages"][-1]["images"][0]["mime_type"], "image/png")
        local_profile.assert_not_awaited()

    async def test_preview_has_exact_packed_text_and_does_not_call_provider_or_claim_draft(self):
        with patch("app.providers.stream_chat") as remote:
            preview = await chat.preview_cloud_context(self.request())
        remote.assert_not_called()
        self.assertEqual(preview["messages"][-1]["content"], "Describe this screenshot")
        self.assertEqual(preview["scopes"], ["messages", "attachments"])
        # A preview alone does not associate the draft with any saved conversation.
        with self.assertRaises(ValueError):
            attachments.preview(self.file["id"], conversation_id=self.cid)

    async def test_text_only_local_model_cannot_receive_images(self):
        with patch.object(chat, "inspect_model", AsyncMock(return_value={"capabilities": ["completion"]})), patch.object(chat.httpx, "AsyncClient") as network:
            events = [json.loads(line.removeprefix("data: ")) async for line in chat._stream_ollama(self.request(provider="ollama"))]
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("cannot read images", events[-1]["content"])
        network.assert_not_called()

    async def test_current_weather_without_today_still_requires_recent_evidence(self):
        with patch.object(chat, "_run_web_search", AsyncMock(return_value=("", []))) as search:
            await chat._search_for_request("weather in Paris", False)
        search.assert_awaited_once_with("weather in Paris", True, max_age_seconds=3 * 3600)

    async def test_offline_weather_reply_can_save_its_attached_user_turn(self):
        events = [json.loads(line.removeprefix("data: ")) async for line in chat._stream_ollama(
            self.request(message="What is the weather in Paris today?"))]
        self.assertEqual(events[-1]["type"], "done")
        answer = "".join(event["content"] for event in events if event["type"] == "token")
        conversations.save_turn(self.cid, "What is the weather in Paris today?", answer,
                                attachment_ids=[self.file["id"]])
        self.assertEqual(conversations.get_messages(self.cid)[0]["attachments"][0]["id"], self.file["id"])

    async def test_image_followup_restores_only_its_saved_message_images(self):
        attachments.resolve([self.file["id"]], self.cid, self.draft)
        conversations.save_turn(self.cid, "Read the chart", "Rainfall by month", attachment_ids=[self.file["id"]])
        preview = await chat.preview_cloud_context(self.request(message="What does the green bar represent?", attachment_ids=[]))
        with_images = [msg for msg in preview["messages"] if msg.get("images")]
        self.assertEqual(len(with_images), 1)
        self.assertEqual(with_images[0]["content"], "Read the chart")
        self.assertIn("attachments", preview["scopes"])
        other = conversations.create_conversation("Unrelated")["id"]
        unrelated = await chat.preview_cloud_context(self.request(conversation_id=other, attachment_ids=[]))
        self.assertFalse(any(msg.get("images") for msg in unrelated["messages"]))

    async def test_implicit_saved_project_is_disclosed_as_knowledge(self):
        project = projects.create_project("Private project")
        projects.assign_conversation(self.cid, project["id"])
        preview = await chat.preview_cloud_context(self.request(attachment_ids=[]))
        self.assertIn("knowledge", preview["scopes"])
        self.assertIn("Private project", json.dumps(preview["messages"]))

    async def test_unavailable_current_information_does_not_invoke_a_cloud_model(self):
        with patch("app.providers.stream_chat") as remote:
            events = [json.loads(line.removeprefix("data: ")) async for line in chat._stream_ollama(self.request(message="What is the weather in Paris today?", attachment_ids=[]))]
        remote.assert_not_called()
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("web", " ".join(event.get("content", "") for event in events).lower())

    async def test_disabled_cloud_requests_cannot_trigger_preparatory_network_reads(self):
        provider_store.configure_policy({"local_only": True})
        with patch.object(chat, "_run_web_search", AsyncMock()) as search, patch.object(chat, "_gather_connector_context", AsyncMock()) as connectors:
            events = [json.loads(line.removeprefix("data: ")) async for line in chat._stream_ollama(self.request(
                message="Find the latest transit news", web_search_enabled=True, connector_context_enabled=True,
            ))]
            search.assert_not_awaited()
            connectors.assert_not_awaited()
        self.assertEqual(events[-1]["type"], "error")


if __name__ == "__main__":
    unittest.main()
