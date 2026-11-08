"""Real SQLite checks for upgrades, failed writes, restarts and profile boundaries."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.db import conversations, memories


class DatabasePersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name) / "profile"
        self.profile.mkdir()
        environment = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": str(self.profile)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_turn_survives_a_new_process_with_sources_and_unicode(self):
        conversation = conversations.create_conversation("研究 notes")
        sources = [{"title": "Gradient ∂L/∂w", "url": "https://example.com/paper"}]
        conversations.save_turn(conversation["id"], "Explain ∂L/∂w", "A gradient answer.",
                                "Model notes", sources)
        result = subprocess.run(
            [sys.executable, "-c", (
                "import json, sys; from app.db import conversations; "
                "print(json.dumps(conversations.get_messages(sys.argv[1])))"
            ), conversation["id"]],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True, timeout=15,
        )
        rows = json.loads(result.stdout)
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["content"], "Explain ∂L/∂w")
        self.assertEqual(rows[1]["sources"], sources)
        self.assertEqual(rows[1]["thinking"], "Model notes")

    def test_failed_second_message_rolls_back_and_releases_write_lock(self):
        conversation = conversations.create_conversation("Atomic turns")
        conversations.save_turn(conversation["id"], "Existing question", "Existing answer")
        original = conversations.get_messages(conversation["id"])
        with closing(sqlite3.connect(self.profile / "conversations.db")) as connection:
            original_updated = connection.execute(
                "SELECT updated_at FROM conversations WHERE id = ?", (conversation["id"],),
            ).fetchone()[0]
            connection.execute("""
                CREATE TRIGGER reject_assistant BEFORE INSERT ON conversation_messages
                WHEN NEW.role = 'assistant'
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END
            """)
            connection.commit()

        # Retain the traceback as a real error handler can. Cleanup must not
        # depend on garbage collection closing a connection left in that frame.
        failure = None
        try:
            conversations.save_turn(conversation["id"], "Must roll back", "Rejected answer")
        except sqlite3.IntegrityError as exc:
            failure = exc
        self.assertIsNotNone(failure)
        self.assertIn("simulated storage failure", str(failure))
        with closing(sqlite3.connect(self.profile / "conversations.db", timeout=0)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM conversation_messages",
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT updated_at FROM conversations WHERE id = ?", (conversation["id"],),
            ).fetchone()[0], original_updated)
            connection.execute("DROP TRIGGER reject_assistant")
            connection.commit()
        self.assertEqual(conversations.get_messages(conversation["id"]), original)
        conversations.save_turn(conversation["id"], "Retry question", "Retry answer")
        self.assertEqual(len(conversations.get_messages(conversation["id"])), 4)

    def test_parallel_turns_keep_both_messages_and_their_pair_order(self):
        conversation = conversations.create_conversation("Concurrent writes")
        def save(index):
            conversations.save_turn(conversation["id"], f"question {index}", f"answer {index}")
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(save, range(12)))
        rows = conversations.get_messages(conversation["id"])
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({row["id"] for row in rows}), 24)
        for user, assistant in zip(rows[::2], rows[1::2]):
            self.assertEqual((user["role"], assistant["role"]), ("user", "assistant"))
            self.assertEqual(user["content"].split()[-1], assistant["content"].split()[-1])

    def test_profile_switch_cannot_read_or_delete_other_profile_records(self):
        first = conversations.create_conversation("Private first profile")
        conversations.save_turn(first["id"], "Private question", "Private answer")
        memory = memories.insert_memories(["I prefer private explanations with practical Python examples."])[0]
        second_profile = self.profile.parent / "second-profile"
        with patch.dict(os.environ, {"LOCALMIND_DATA_DIR": str(second_profile)}):
            self.assertEqual(conversations.list_conversations(), [])
            self.assertEqual(conversations.get_messages(first["id"]), [])
            self.assertFalse(conversations.delete_conversation(first["id"]))
            self.assertEqual(memories.list_all(), [])
            self.assertFalse(memories.delete_by_id(memory["id"]))
            second = conversations.create_conversation("Second profile")
        self.assertIsNone(conversations.get_conversation(second["id"]))
        self.assertEqual(len(conversations.get_messages(first["id"])), 2)
        self.assertEqual(memories.list_all()[0]["id"], memory["id"])

    def test_legacy_conversation_schema_upgrade_preserves_messages(self):
        with closing(sqlite3.connect(self.profile / "conversations.db")) as connection:
            connection.executescript("""
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE conversation_messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, thinking TEXT, created_at TEXT NOT NULL
                );
                INSERT INTO conversations VALUES ('old-chat', 'Existing chat', '2025-01-01', '2025-01-01');
                INSERT INTO conversation_messages VALUES
                    ('old-message', 'old-chat', 'assistant', 'Keep this answer', NULL, '2025-01-01');
            """)
        for _ in range(2):
            self.assertIsNone(conversations.get_conversation("old-chat")["project_id"])
            row = conversations.get_messages("old-chat")[0]
            self.assertEqual(row["content"], "Keep this answer")
            self.assertEqual(row["sources"], [])
        conversations.save_turn("old-chat", "New question", "New answer")
        self.assertEqual(len(conversations.get_messages("old-chat")), 3)

    def test_legacy_memory_upgrade_preserves_content_and_adds_scope(self):
        with closing(sqlite3.connect(self.profile / "memories.db")) as connection:
            connection.executescript("""
                CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL);
                INSERT INTO memories VALUES ('old-memory', 'You prefer concise examples when learning mathematics.', '2025-01-01');
            """)
        for _ in range(2):
            row = memories.list_all()[0]
            self.assertEqual(row["id"], "old-memory")
            self.assertEqual(row["content"], "You prefer concise examples when learning mathematics.")
            self.assertIsNone(row["project_id"])
            self.assertIsNone(row["conversation_id"])
        new = memories.insert_memories(["You use Python notebooks for your mathematics practice."],
                                       project_id="project", conversation_id="conversation")[0]
        self.assertIsNotNone(memories.create_link(new["id"], "old-memory", "elaborates"))

    def test_deleting_conversation_cascades_only_its_messages(self):
        first = conversations.create_conversation("First")
        second = conversations.create_conversation("Second")
        for row in (first, second):
            conversations.save_turn(row["id"], "Question", "Answer")
        self.assertTrue(conversations.delete_conversation(first["id"]))
        self.assertFalse(conversations.delete_conversation(first["id"]))
        self.assertEqual(conversations.get_messages(first["id"]), [])
        self.assertEqual(len(conversations.get_messages(second["id"])), 2)
        with closing(sqlite3.connect(self.profile / "conversations.db")) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0], 2)

    def test_memory_delete_cascades_incoming_and_outgoing_links(self):
        rows = memories.insert_memories([
            "You use Python notebooks for everyday mathematics practice.",
            "You study gradient descent using small numerical examples.",
            "You compare analytical derivatives with finite difference approximations.",
        ])
        first, middle, last = [row["id"] for row in rows]
        memories.create_link(first, middle)
        memories.create_link(middle, last)
        retained = memories.create_link(first, last)
        self.assertTrue(memories.delete_by_id(middle))
        self.assertEqual([row["id"] for row in memories.list_all_links()], [retained["id"]])
        with closing(sqlite3.connect(self.profile / "memories.db")) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
