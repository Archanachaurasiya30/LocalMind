"""Real image decoding and SQLite ownership, rather than filename-only checks."""
import base64
from contextlib import closing
from io import BytesIO
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image, PngImagePlugin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import attachments
from app.api.routes.attachments import router
from app.db import conversations


def image_data(fmt="PNG", size=(48, 24)):
    output = BytesIO()
    image = Image.new("RGB", size, "navy")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "do not transmit this comment")
    image.save(output, format=fmt, **({"pnginfo": metadata} if fmt == "PNG" else {}))
    return base64.b64encode(output.getvalue()).decode("ascii")


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.draft = str(uuid4())
        self.conversation = conversations.create_conversation("Images")['id']

    def upload(self, **kwargs):
        return attachments.create(kwargs.get("filename", "Screenshot.png"),
                                  kwargs.get("data", image_data()),
                                  kwargs.get("draft_id", self.draft), kwargs.get("conversation_id"))

    def test_actual_decoder_accepts_supported_formats_and_removes_metadata(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            with self.subTest(fmt=fmt):
                data, mime, width, height = attachments.normalize_image(image_data(fmt))
                self.assertEqual((mime, width, height), ("image/png", 48, 24))
                with Image.open(BytesIO(data)) as decoded:
                    self.assertEqual(decoded.info, {})
                self.assertNotIn(b"private", data)

    def test_orientation_is_applied_without_resizing_text(self):
        image = Image.new("RGB", (30, 10), "white")
        exif = image.getexif()
        exif[274] = 6
        output = BytesIO()
        image.save(output, format="JPEG", exif=exif)
        normalized, _, width, height = attachments.normalize_image(base64.b64encode(output.getvalue()).decode())
        self.assertEqual((width, height), (10, 30))
        with Image.open(BytesIO(normalized)) as decoded:
            self.assertFalse(decoded.getexif())

    def test_corrupt_animated_unsupported_and_oversize_images_are_rejected(self):
        for data in ("not base64!", base64.b64encode(b"<svg></svg>").decode(), image_data("GIF")):
            with self.assertRaises(ValueError):
                attachments.normalize_image(data)
        with patch.object(attachments, "MAX_PIXELS", 10), self.assertRaises(ValueError):
            attachments.normalize_image(image_data())
        with patch.object(attachments, "MAX_BYTES", 10), self.assertRaises(ValueError):
            attachments.normalize_image(image_data())
        output = BytesIO()
        Image.new("RGB", (4, 4), "red").save(output, format="PNG", save_all=True,
                                           append_images=[Image.new("RGB", (4, 4), "blue")])
        with self.assertRaises(ValueError):
            attachments.normalize_image(base64.b64encode(output.getvalue()).decode())

    def test_draft_to_message_binding_survives_reopen_and_cascades_on_delete(self):
        uploaded = self.upload(filename="../../screen.png")
        self.assertEqual(uploaded["filename"], "screen.png")
        images = attachments.resolve([uploaded["id"]], self.conversation, self.draft)
        self.assertEqual(len(images), 1)
        conversations.save_turn(self.conversation, "What is this?", "A screenshot", attachment_ids=[uploaded["id"]])
        messages = conversations.get_messages(self.conversation)
        self.assertEqual(messages[0]["attachments"][0]["id"], uploaded["id"])
        self.assertEqual(attachments.history_images(messages[0]["id"], self.conversation), images)
        with self.assertRaises(ValueError):
            attachments.discard(uploaded["id"], self.draft)
        self.assertTrue(attachments.preview(uploaded["id"], self.conversation)["data_url"].startswith("data:image/png;"))
        conversations.delete_conversation(self.conversation)
        with self.assertRaises(ValueError):
            attachments.preview(uploaded["id"], self.conversation)

    def test_profile_draft_and_conversation_boundaries(self):
        uploaded = self.upload(conversation_id=self.conversation)
        other = conversations.create_conversation("Other")["id"]
        for cid, draft in ((other, self.draft), (self.conversation, str(uuid4()))):
            with self.assertRaises(ValueError):
                attachments.resolve([uploaded["id"]], cid, draft)
        with tempfile.TemporaryDirectory() as other_profile, patch.dict(os.environ, {"LOCALMIND_DATA_DIR": other_profile}):
            with self.assertRaises(ValueError):
                attachments.preview(uploaded["id"], draft_id=self.draft)
        with self.assertRaises(ValueError):
            attachments.preview("../conversations.db", draft_id=self.draft)

    def test_failed_binding_is_atomic_and_does_not_save_half_a_turn(self):
        uploaded = self.upload()
        attachments.resolve([uploaded["id"]], self.conversation, self.draft)
        with self.assertRaises(ValueError):
            conversations.save_turn(self.conversation, "Question", "Answer",
                                    attachment_ids=[uploaded["id"], str(uuid4())])
        self.assertEqual(conversations.get_messages(self.conversation), [])
        # First attachment was rolled back too and can be saved normally.
        conversations.save_turn(self.conversation, "Question", "Answer", attachment_ids=[uploaded["id"]])

    def test_preview_does_not_claim_draft_and_late_upload_can_be_discarded(self):
        uploaded = self.upload()
        attachments.resolve([uploaded["id"]], self.conversation, self.draft, claim=False)
        with closing(conversations._get_conn()) as conn:
            self.assertIsNone(conn.execute("SELECT conversation_id FROM chat_attachments WHERE id=?", (uploaded["id"],)).fetchone()[0])
        attachments.discard(uploaded["id"], self.draft)
        with self.assertRaises(ValueError):
            attachments.resolve([uploaded["id"]], self.conversation, self.draft)

    def test_limits_and_expired_draft_cleanup_do_not_remove_saved_images(self):
        first = self.upload()
        for _ in range(3):
            self.upload()
        with self.assertRaises(ValueError):
            self.upload()
        attachments.resolve([first["id"]], self.conversation, self.draft)
        conversations.save_turn(self.conversation, "Q", "A", attachment_ids=[first["id"]])
        with closing(conversations._get_conn()) as conn, conn:
            conn.execute("UPDATE chat_attachments SET created_at=?", (time.time() - attachments.DRAFT_TTL - 100,))
        self.upload()
        with closing(conversations._get_conn()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM chat_attachments").fetchone()[0], 2)
        with patch.object(attachments, "MAX_PROFILE_BYTES", 1), self.assertRaises(ValueError):
            self.upload()

    def test_api_validation_and_preview_ownership(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.post("/attachments", json={"filename": "shot.png", "data": image_data(), "draft_id": self.draft})
            self.assertEqual(response.status_code, 200)
            key = response.json()["id"]
            self.assertEqual(client.post("/attachments/preview", json={"id": key, "draft_id": str(uuid4())}).status_code, 404)
            self.assertEqual(client.request("DELETE", f"/attachments/{key}", json={"draft_id": self.draft}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
