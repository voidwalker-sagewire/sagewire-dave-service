import json
import io
import unittest
from unittest.mock import patch

import field_case_replay as replay


class FieldReplayTests(unittest.TestCase):
    def test_markdown_export_preserves_exact_questions_and_separates_old_answers(self):
        export = '''[8:00 AM] ① question: "A calf has swelling"
[8:01 AM] ① question: "for one week"
⚠️ Field Reference Only
A calf has swelling
Old answer one.
📚 Sources: https://example.org/one
📋 Similar case
Old history.
for one week
Old answer two.
📚 Sources: https://example.org/two
'''
        turns = replay.markdown_turns(export)
        self.assertEqual([t["question"] for t in turns], ["A calf has swelling", "for one week"])
        self.assertEqual(turns[0]["recorded_answer"], "Old answer one.")
        self.assertEqual(turns[1]["recorded_answer"], "Old answer two.")

    def test_debug_only_export_is_not_fabricated_into_old_answers(self):
        turns = replay.markdown_turns('[8:00 AM] ① question: "Incomplete"\n')
        self.assertIsNone(turns[0]["recorded_answer"])

    def test_snapshot_parser_excludes_source_and_history_cards_from_answer(self):
        parser = replay.BubbleParser()
        parser.feed('<div class="msg user"><div class="bubble">Question</div></div>'
                    '<div class="msg assistant"><div class="bubble">Reply<br>Next</div>'
                    '<div class="sources">not answer text</div></div>')
        self.assertEqual(parser.messages, [{"role": "user", "content": "Question"},
                                           {"role": "assistant", "content": "Reply\nNext"}])

    def suite(self):
        return {"limitations": ["Text only"], "cases": [{"id": "example", "review_criteria": ["Do not invent facts"], "turns": [
            {"question": "Initial question", "recorded_answer": "Unsafe old answer"},
            {"question": "Follow-up", "recorded_answer": "Another old answer"},
        ]}]}

    def test_production_and_unrevised_api_are_rejected_before_questions(self):
        with patch.object(replay, "request_json") as request:
            with self.assertRaises(ValueError):
                replay.replay(self.suite(), "https://dave.sagewire.dev")
            request.assert_not_called()
        with patch.object(replay, "request_json", return_value={"status": "online"}) as request:
            with self.assertRaises(ValueError):
                replay.replay(self.suite(), "http://127.0.0.1:5006")
            self.assertEqual(request.call_count, 1)

    def test_replay_uses_revised_answers_not_recorded_answers_and_no_real_identity(self):
        calls = []
        def request(url, payload=None):
            if payload is None:
                return {"conversation_policy_revision": replay.POLICY_REVISION}
            calls.append(json.loads(json.dumps(payload)))
            return {"answer": "Revised answer", "sources": [], "conversation_policy_revision": replay.POLICY_REVISION}
        with patch.object(replay, "request_json", side_effect=request):
            report = replay.replay(self.suite(), "http://127.0.0.1:5006", repeats=2)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["conversation_history"], [])
        self.assertEqual(calls[1]["conversation_history"][-1]["content"], "Revised answer")
        self.assertNotIn("Unsafe old answer", json.dumps(calls))
        self.assertEqual(calls[0]["user_id"], "default")
        self.assertNotIn("herdmate_sheet_id", calls[0])
        self.assertNotEqual(calls[0]["session_id"], calls[2]["session_id"])
        self.assertEqual(report["summary"]["clinical_passes"], 0)
        self.assertEqual(report["turns"][0]["status"], "manual_review_required")

    def test_error_stops_case_instead_of_replaying_followups_without_missing_context(self):
        with patch.object(replay, "request_json", side_effect=[
            {"conversation_policy_revision": replay.POLICY_REVISION}, OSError("unavailable"),
        ]) as request:
            report = replay.replay(self.suite(), "http://127.0.0.1:5006")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(report["summary"]["errors"], 1)

    def test_audit_is_explicitly_not_a_new_model_result(self):
        report = replay.audit(self.suite())
        self.assertFalse(report["new_model_tested"])
        self.assertEqual(report["summary"]["clinical_passes"], 0)

    def test_missing_exported_image_does_not_invalidate_original_photo_observation(self):
        self.assertEqual(replay.screen("From the photo, I see swelling.", recorded=True), [])
        self.assertIn("asserted_unseen_photo", replay.screen("From the photo, I see swelling."))

    def test_http_error_body_survives_instead_of_generic_bad_gateway(self):
        error = replay.HTTPError("http://example.invalid", 502, "Bad Gateway", {},
                                 io.BytesIO(b'{"detail":{"reason":"citation mismatch"}}'))
        with patch.object(replay, "urlopen", side_effect=error):
            with self.assertRaisesRegex(ValueError, 'HTTP 502:.*citation mismatch'):
                replay.request_json("http://example.invalid", {"question": "example"})


if __name__ == "__main__":
    unittest.main()
