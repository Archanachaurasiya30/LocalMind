"""Partial replies remain recoverable without entering completed model history."""

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import conversations as db
from app.api.routes.conversations import router


class InterruptedTurnTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        env = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.conversation = db.create_conversation("Synthetic draft")["id"]
        self.request = str(uuid4())

    def save(self, **changes):
        values = {"conversation_id": self.conversation, "request_id": self.request,
                  "user_content": "Explain orbital mechanics", "assistant_content": "Gravity changes",
                  "error": "Connection interrupted"}
        values.update(changes)
        db.save_interrupted_turn(**values)

    def test_idempotent_snapshots_preserve_longest_partial_and_cancel_state(self):
        self.save()
        self.save(assistant_content="Gravity changes direction", assistant_thinking="Notes", cancelled=True)
        self.save(assistant_content="Gravity", assistant_thinking="N")
        rows = db.get_messages(self.conversation)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["id"], self.request)
        self.assertEqual(rows[1]["content"], "Gravity changes direction")
        self.assertEqual(rows[1]["thinking"], "Notes")
        self.assertTrue(rows[1]["cancelled"])
        self.assertTrue(all(row["partial"] and row["status"] == "interrupted" for row in rows))

    def test_interrupted_turn_survives_restart_and_successful_retry_is_separate(self):
        self.save()
        result = subprocess.run([sys.executable, "-c", (
            "import json,sys; from app.db import conversations as db; "
            "print(json.dumps(db.get_messages(sys.argv[1])))"
        ), self.conversation], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True, timeout=15)
        rows = json.loads(result.stdout)
        self.assertEqual(rows[1]["content"], "Gravity changes")
        self.assertTrue(rows[1]["error"])
        self.assertEqual(rows[1]["error_message"], "Connection interrupted")
        db.save_turn(self.conversation, "Explain orbital mechanics", "A completed answer")
        rows = db.get_messages(self.conversation)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["status"] for row in rows], ["interrupted", "interrupted", "completed", "completed"])

    def test_deleted_conversation_cannot_be_resurrected_by_late_save(self):
        self.save()
        db.delete_conversation(self.conversation)
        with self.assertRaises(LookupError):
            self.save(assistant_content="Late content")
        self.assertEqual(db.get_messages(self.conversation), [])
        self.assertIsNone(db.get_conversation(self.conversation))

    def test_request_cannot_move_between_conversations_or_profiles(self):
        self.save()
        other = db.create_conversation("Other")["id"]
        with self.assertRaises(ValueError):
            self.save(conversation_id=other)
        with self.assertRaises(ValueError):
            self.save(user_content="Another request")
        self.assertEqual(db.get_messages(other), [])
        with tempfile.TemporaryDirectory() as other_profile, patch.dict(os.environ, {"LOCALMIND_DATA_DIR": other_profile}):
            self.assertEqual(db.get_messages(self.conversation), [])
            with self.assertRaises(LookupError):
                self.save()

    def test_empty_partial_answer_is_kept_for_failed_request(self):
        self.save(assistant_content="")
        rows = db.get_messages(self.conversation)
        self.assertEqual(rows[1]["content"], "")
        self.assertEqual(rows[0]["content"], "Explain orbital mechanics")
        self.assertTrue(rows[1]["error"])

    def test_draft_attachments_are_not_consumed_by_interrupted_save(self):
        attachment_id, draft_id = str(uuid4()), str(uuid4())
        with closing(db._get_conn()) as conn, conn:
            conn.execute("INSERT INTO chat_attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (attachment_id, draft_id, self.conversation, None, "image.png", "image/png", 1, 1, b"data", 0))
        self.save(attachment_ids=[attachment_id])
        rows = db.get_messages(self.conversation)
        self.assertEqual(rows[0]["attachments"][0]["id"], attachment_id)
        self.assertEqual(rows[0]["attachment_draft_id"], draft_id)
        with closing(db._get_conn()) as conn:
            self.assertIsNone(conn.execute("SELECT message_id FROM chat_attachments WHERE id=?", (attachment_id,)).fetchone()[0])
        db.save_turn(self.conversation, "Retry", "Completed", attachment_ids=[attachment_id])
        self.assertEqual(db.get_messages(self.conversation)[2]["attachments"][0]["id"], attachment_id)
        self.assertIsNone(db.get_messages(self.conversation)[0]["attachment_draft_id"])

    def test_interrupted_save_rejects_combining_distinct_drafts(self):
        ids = [str(uuid4()), str(uuid4())]
        with closing(db._get_conn()) as conn, conn:
            for identifier in ids:
                conn.execute("INSERT INTO chat_attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (identifier, str(uuid4()), self.conversation, None, "image.png", "image/png", 1, 1, b"data", 0))
        with self.assertRaisesRegex(ValueError, "same draft"):
            self.save(attachment_ids=ids)
        self.assertEqual(db.get_messages(self.conversation), [])

    def test_endpoint_returns_missing_and_conflict_without_partial_writes(self):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            payload = {"request_id": self.request, "user_content": "Hi", "assistant_content": "Hello", "cancelled": True}
            response = client.post(f"/conversations/{self.conversation}/interrupted", json=payload)
            self.assertEqual(response.status_code, 200)
            response = client.post(f"/conversations/{self.conversation}/interrupted", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(client.get(f"/conversations/{self.conversation}/messages").json()["messages"]), 2)
            response = client.post(f"/conversations/{self.conversation}/interrupted", json={**payload, "user_content": "Changed"})
            self.assertEqual(response.status_code, 409)
            response = client.post(f"/conversations/{uuid4()}/interrupted", json=payload)
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
