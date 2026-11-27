import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import diagnose_server_reply as diagnostic


class ReplyDiagnosticTests(unittest.TestCase):
    def test_rejected_raw_reply_and_stop_reason_are_captured_without_accepting_it(self):
        class APIError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        fake = types.SimpleNamespace()
        fake.HTTPException = APIError
        fake.VetQuestion = lambda **kwargs: types.SimpleNamespace(**kwargs)
        fake.claude = types.SimpleNamespace(messages=types.SimpleNamespace(create=Mock(return_value=types.SimpleNamespace(
            stop_reason="end_turn", model="example-model", content=[types.SimpleNamespace(type="text")]
        ))))
        fake.parse_grounded_answer = Mock(side_effect=ValueError("citation mismatch"))
        async def ask(question):
            fake.claude.messages.create()
            try:
                fake.parse_grounded_answer('Rejected raw reply', {"V1": "example-source"})
            except ValueError:
                raise APIError(502, "Rejected")
        fake.ask_vet = ask
        suite = {"cases": [{"id": "example", "turns": [{"question": "An example observation"}]}]}
        output = io.StringIO()
        with patch.dict(sys.modules, {"herdmate_vet_api": fake}), \
             patch.object(sys, "path", list(sys.path)), \
             patch.object(Path, "read_text", return_value=json.dumps(suite)), \
             patch.object(Path, "write_text") as write, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            exec(diagnostic.PROBE_CODE, {})
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], 502)
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["raw_answer"], "Rejected raw reply")
        self.assertEqual(result["validation_error"], "ValueError: citation mismatch")
        self.assertEqual(result["format_validation"], "rejected")
        self.assertEqual(result["clinical_status"], "not_reviewed")
        write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
