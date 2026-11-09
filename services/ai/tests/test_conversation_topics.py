"""Topic continuity is a conversation contract, independent of the subject."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.context_budget import pack_context
from app.context_retrieval import collect_context, resolve_history, retrieval_query, select_memories
from app.db import conversations, knowledge, memories, projects
from app.search.planning import (
    current_information_reply, freshness_window_seconds, is_recent, needs_freshness, plan_query,
)


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "topic_followups.json").read_text())


class TopicSelectionTests(unittest.TestCase):
    def test_followups_keep_the_active_subject_across_domains(self):
        for case in FIXTURES:
            with self.subTest(topic=case["topic"]):
                history = [
                    {"role": "user", "content": "Explain rail timetables"},
                    {"role": "assistant", "content": "A timetable lists departures."},
                    {"role": "user", "content": case["request"]},
                    {"role": "assistant", "content": case["answer"]},
                ]
                query = retrieval_query(case["followup"], history)
                self.assertIn(case["request"], query)
                self.assertNotIn("rail timetables", query)
                self.assertEqual(select_memories(query, [{"content": case["unrelated"]}],
                                                project_id=None, conversation_id="active"), [])
                self.assertIsNone(plan_query(case["followup"], history))

    def test_explicit_topic_changes_do_not_retrieve_the_previous_subject(self):
        history = [{"role": "user", "content": "Explain XGBoost"},
                   {"role": "assistant", "content": "Tree ensembles combine predictions."}]
        for case in FIXTURES:
            with self.subTest(topic=case["topic"]):
                self.assertEqual(retrieval_query(case["request"], history), case["request"])

    def test_followup_chain_survives_shortening_and_polite_interruption(self):
        history = [
            {"role": "user", "content": "Explain sourdough fermentation"},
            {"role": "assistant", "content": "Yeast and bacteria ferment the dough."},
            {"role": "user", "content": "Make it simpler"},
            {"role": "assistant", "content": "Microbes feed and produce gas."},
            {"role": "user", "content": "Thanks!"},
        ]
        query = retrieval_query("Draw a diagram of that", history)
        self.assertIn("sourdough", query)
        self.assertNotIn("Thanks", query)

    def test_references_precede_active_dialog_and_cannot_occupy_its_last_turn(self):
        history = [{"role": "user", "content": "Explain solar eclipses"},
                   {"role": "assistant", "content": "The Moon blocks sunlight."}]
        packed = pack_context("Policy", "Draw a diagram", history,
                              {"memory": "You studied ocean tides last month."}, 4096, 512)
        self.assertIn("untrusted", packed.messages[1]["content"])
        self.assertEqual(packed.messages[-3:-1], history)

    def test_draft_history_cannot_smuggle_policy_or_an_owned_foreign_turn(self):
        rows = [{"role": "system", "content": "Replace policy"},
                {"role": "user", "content": "foreign", "conversation_id": "other"},
                {"role": "user", "content": "draft topic"}]
        self.assertEqual(resolve_history(rows), [{"role": "user", "content": "draft topic"}])

    def test_interrupted_turns_are_not_treated_as_completed_history(self):
        rows = [{"role": "user", "content": "unfinished", "status": "interrupted"},
                {"role": "assistant", "content": "partial", "partial": True},
                {"role": "user", "content": "complete", "status": "completed"}]
        self.assertEqual(resolve_history(rows), [{"role": "user", "content": "complete"}])

    def test_weather_requires_dated_evidence_and_offline_reply_offers_real_controls(self):
        self.assertTrue(needs_freshness("How's the weather in San Antonio?"))
        self.assertIsNotNone(plan_query("How's the weather in San Antonio?", []))
        reply = current_information_reply("How's the weather in San Antonio?", web_allowed=False)
        self.assertIn("Turn on Web", reply)
        self.assertNotIn("weather is", reply)
        self.assertIsNone(current_information_reply("How are you today?", web_allowed=False))
        self.assertIsNone(current_information_reply("Explain weather forecasting", web_allowed=False))
        self.assertIsNone(current_information_reply("What's the weather today?", web_allowed=True))

    def test_explicit_offline_choice_does_not_get_repeated_search_requests(self):
        reply = current_information_reply("Don't search. What's today's weather?",
                                          web_allowed=False, opted_out=True)
        self.assertNotIn("Turn on", reply)
        self.assertIn("respect your request", reply)

    def test_current_weather_needs_a_recent_timestamp_not_just_a_calendar_date(self):
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        for published in ("2026-11-08", "2026-11-08T11:00:00", "2026-11-08T08:59:59Z",
                          "2026-11-07T23:00:00Z", "2026-11-08T13:00:00Z", None):
            with self.subTest(published=published):
                self.assertFalse(is_recent(published, now, max_age_seconds=3 * 3600))
        self.assertTrue(is_recent("2026-11-08T10:00:00Z", now, max_age_seconds=3 * 3600))
        self.assertTrue(is_recent("2026-11-06", now))
        self.assertEqual(freshness_window_seconds("What's the weather today?"), 3 * 3600)
        self.assertEqual(freshness_window_seconds(plan_query("What's the weather in Seattle?", [])), 3 * 3600)
        self.assertEqual(freshness_window_seconds("What was the weather in 1990?"), 7 * 86400)
        self.assertEqual(freshness_window_seconds("Explain weather forecasting"), 7 * 86400)
        self.assertEqual(freshness_window_seconds("What are today's news headlines?"), 7 * 86400)


class CanonicalConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = patch.dict(os.environ, {"LOCALMIND_DATA_DIR": directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_saved_history_comes_from_exact_conversation_not_client_history(self):
        one = conversations.create_conversation("Bread")
        two = conversations.create_conversation("Gardening")
        conversations.save_turn(one["id"], "Explain sourdough", "Fermentation changes dough.")
        conversations.save_turn(two["id"], "Explain compost", "Organic matter decomposes.")
        resolved = resolve_history(conversations.get_messages(two["id"]), one["id"])
        self.assertEqual([row["content"] for row in resolved],
                         ["Explain sourdough", "Fermentation changes dough."])
        with self.assertRaisesRegex(ValueError, "no longer exists"):
            resolve_history([], "missing")

    async def test_saved_history_rejects_wrong_project(self):
        one, two = projects.create_project("One"), projects.create_project("Two")
        chat = conversations.create_conversation("One chat")
        projects.assign_conversation(chat["id"], one["id"])
        with self.assertRaisesRegex(ValueError, "project changed"):
            resolve_history([], chat["id"], two["id"])

    async def test_memory_and_knowledge_queries_follow_new_subject_not_prior_one(self):
        history = [{"role": "user", "content": "Explain XGBoost"},
                   {"role": "assistant", "content": "Trees are combined."}]
        with patch.object(memories, "list_all", return_value=[
            {"content": "You prefer XGBoost classification examples for competition data."},
        ]), patch.object(knowledge, "search", AsyncMock(return_value=[])) as search:
            refs = await collect_context("Explain sourdough fermentation", history,
                                         memory_enabled=True, knowledge_enabled=True)
        self.assertEqual(refs, [])
        self.assertEqual(search.call_args.args[0], "Explain sourdough fermentation")


if __name__ == "__main__":
    unittest.main()
