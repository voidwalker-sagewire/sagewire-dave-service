"""Exercise request orchestration with model, Sheets, and vector DB stubbed.

No credentials, embedding downloads, network calls, or veterinary outcomes
are needed or claimed by this suite.
"""
import asyncio
import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_api():
    chroma = types.ModuleType("chromadb")
    client = Mock()
    client.get_or_create_collection.side_effect = lambda *a, **k: Mock()
    chroma.HttpClient = Mock(return_value=client)
    embeddings = types.ModuleType("chromadb.utils.embedding_functions")
    embeddings.SentenceTransformerEmbeddingFunction = Mock()
    utils = types.ModuleType("chromadb.utils")
    utils.embedding_functions = embeddings
    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = Mock()
    modules = {"chromadb": chroma, "chromadb.utils": utils,
               "chromadb.utils.embedding_functions": embeddings, "anthropic": anthropic}
    try:
        import requests
    except ImportError:
        modules["requests"] = types.ModuleType("requests")
    # Only stub the credential module if google-auth isn't installed locally.
    try:
        import google.oauth2.service_account
    except ImportError:
        for name in ("google", "google.oauth2", "google.oauth2.service_account",
                     "google.auth", "google.auth.transport", "google.auth.transport.requests"):
            modules[name] = types.ModuleType(name)
        modules["google.oauth2.service_account"].Credentials = Mock()
    with patch.dict(sys.modules, modules), patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"}):
        return importlib.import_module("herdmate_vet_api")


api = load_api()


class AskEndpointTests(unittest.TestCase):
    def run_ask(self, payload, model_payload):
        response = types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=json.dumps(model_payload))])
        with patch.object(api, "search_knowledge", return_value=[("Winter feed", {"source": "winter"}), ("Joint exam", {"source": "joint"})]) as search, \
             patch.object(api, "search_memory", return_value=[]) as memory, \
             patch.object(api, "save_to_memory") as save, \
             patch.object(api.claude.messages, "create", return_value=response) as model:
            result = asyncio.run(api.ask_vet(api.VetQuestion(**payload)))
            return result, search.call_args, memory.call_args, save, model.call_args

    def test_followup_search_uses_observations_and_never_reports_automatic_high_confidence(self):
        result, search, memory, save, model = self.run_ask({
            "question": "Warm in the middle.", "session_id": "chat-1",
            "user_id": "person", "operation": "Farm A",
            "conversation_history": [
                {"role": "user", "content": "A calf has a swollen knee."},
                {"role": "assistant", "content": "My earlier diagnosis"},
            ],
        }, {"answer": "An examination can clarify the affected tissue [V2].", "source_ids": ["V2"]})
        self.assertIn("swollen knee", search.args[0])
        self.assertNotIn("earlier diagnosis", search.args[0])
        self.assertEqual(result.sources, ["joint"])
        self.assertEqual(result.confidence, "unverified")
        self.assertEqual(result.evidence_status, "cited_references")
        self.assertEqual(memory.args[2], "chat-1")
        self.assertEqual(save.call_args.kwargs["metadata"]["session_id"], "chat-1")
        self.assertEqual(model.kwargs["messages"][-1], {"role": "user", "content": "Warm in the middle."})

    def test_retrieved_but_uncited_sources_are_not_returned(self):
        result, *_ = self.run_ask({"question": "Unfinished sentence"}, {
            "answer": "What were you going to add?", "source_ids": [],
        })
        self.assertEqual(result.sources, [])
        self.assertEqual(result.evidence_status, "no_supporting_reference")

    def test_invalid_citation_is_neither_displayed_nor_saved(self):
        with patch.object(api, "search_knowledge", return_value=[]), \
             patch.object(api, "search_memory", return_value=[]), \
             patch.object(api, "save_to_memory") as save, \
             patch.object(api.claude.messages, "create", return_value=types.SimpleNamespace(content=[
                 types.SimpleNamespace(type="text", text='{"answer":"Claim [V99]","source_ids":["V99"]}')
             ])):
            with self.assertRaises(api.HTTPException) as raised:
                asyncio.run(api.ask_vet(api.VetQuestion(question="Question")))
            self.assertEqual(raised.exception.status_code, 502)
            save.assert_not_called()

    def test_legacy_client_without_session_does_not_retrieve_or_save_shared_memory(self):
        with patch.object(api.memory_collection, "query") as query, patch.object(api.memory_collection, "add") as add:
            self.assertEqual(api.search_memory("Question", "scope", None, []), [])
            api.save_to_memory("Question", "Answer", {"user_id": "default"})
            query.assert_not_called()
            add.assert_not_called()

    def test_memory_query_excludes_current_session_at_database_boundary(self):
        collection = Mock()
        collection.count.return_value = 10
        collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
        with patch.object(api, "memory_collection", collection):
            self.assertEqual(api.search_memory("Question", "scope", "current", []), [])
        filters = collection.query.call_args.kwargs["where"]["$and"]
        self.assertIn({"session_id": {"$ne": "current"}}, filters)
        self.assertIn({"scope_id": {"$eq": "scope"}}, filters)


if __name__ == "__main__":
    unittest.main()
