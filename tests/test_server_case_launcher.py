import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server_case_launcher as launcher


class ServerLauncherTests(unittest.TestCase):
    def test_model_access_is_copied_but_production_data_access_is_not(self):
        env = launcher.model_environment([
            "ANTHROPIC_API_KEY=test-placeholder", "CLAUDE_MODEL=example-model",
            "CHROMA_HOST=production-db", "CHROMA_DB_PATH=/live-data",
            "CREDENTIALS_FILE=/live-google.json", "GOOGLE_SECRET=private",
            "RAG_SERVICE_URL=https://production-rag.invalid",
        ])
        self.assertEqual(env["ANTHROPIC_API_KEY"], "test-placeholder")
        self.assertEqual(env["CHROMA_HOST"], "127.0.0.1")
        self.assertEqual(env["CHROMA_DB_PATH"], "/replay-data")
        self.assertNotIn("GOOGLE_SECRET", env)
        self.assertEqual(env["RAG_SERVICE_URL"], "")
        self.assertNotEqual(env["CREDENTIALS_FILE"], "/live-google.json")

    def test_missing_key_and_envfile_injection_are_rejected(self):
        for env in ([], ["ANTHROPIC_API_KEY=test\nUNWANTED=value"]):
            with self.assertRaises(ValueError):
                launcher.model_environment(env)

    def test_test_container_has_no_published_port_or_production_mount(self):
        args = launcher.test_container_args("sha256:existing", "/test/code", "/test/db", "/test/out", "/test/model.env", "example", 1)
        self.assertNotIn("--publish", args)
        self.assertNotIn("-p", args)
        self.assertNotIn("--volumes-from", args)
        self.assertNotIn("--privileged", args)
        self.assertIn("--pull=never", args)
        self.assertIn("type=bind,source=/test/db,target=/replay-data", args)
        self.assertEqual(args[args.index("--network") + 1], "bridge")
        self.assertIn("--memory", args)
        self.assertNotIn("test-placeholder", " ".join(args))

    def test_bundle_hashes_detect_stale_or_changed_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = root / "example.py"
            code.write_text("original")
            manifest = {"sha256": {"example.py": hashlib.sha256(code.read_bytes()).hexdigest()}}
            (root / "bundle-manifest.json").write_text(json.dumps(manifest))
            launcher.verify_bundle(root)
            code.write_text("changed")
            with self.assertRaises(ValueError):
                launcher.verify_bundle(root)

    def test_bundle_path_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bundle-manifest.json").write_text(json.dumps({"sha256": {"../other.py": "bad"}}))
            with self.assertRaises(ValueError):
                launcher.verify_bundle(root)

    def test_changed_production_revision_stops_before_export_or_test_creation(self):
        inspect = [{"State": {"Running": True}, "Config": {"Image": "dave:changed"}}]
        with patch.object(launcher, "verify_bundle"), patch.object(launcher, "docker", return_value=json.dumps(inspect)) as docker:
            with self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
                launcher.launch(Path("/example"), "production", 1)
            self.assertEqual(docker.call_count, 1)
            self.assertEqual(docker.call_args.args[0], ["inspect", "production"])

    def test_launch_lifecycle_keeps_secret_local_and_only_reads_live_container(self):
        secret = "placeholder-never-print"
        production = [{"State": {"Running": True}, "Image": "sha256:existing",
                       "Config": {"Image": "dave:" + launcher.EXPECTED_PRODUCTION_REVISION,
                                  "Env": ["ANTHROPIC_API_KEY=" + secret]}}]
        corpus = {"ids": ["doc"], "documents": ["Reference"], "metadatas": [{"source": "source"}],
                  "embeddings": [[0.1]], "metadata": {}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "code"
            root.mkdir()
            work = Path(directory) / "work"
            work.mkdir()
            (root / "export_vet_corpus.py").write_text("# read-only export")
            (root / "bundle-manifest.json").write_text('{"sha256": {}}')
            output = io.StringIO()
            with patch.object(launcher.tempfile, "mkdtemp", return_value=str(work)), \
                 patch.object(launcher, "docker", side_effect=[json.dumps(production), json.dumps(corpus), "", "container-id"]) as docker, \
                 contextlib.redirect_stdout(output):
                launcher.launch(root, "live-dave", 1)
            self.assertFalse((work / "model.env").exists())
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, (work / "output/run-info.json").read_text())
            calls = [call.args[0] for call in docker.call_args_list]
            self.assertEqual(calls[1][:5], ["exec", "-i", "live-dave", "python3", "-"])
            self.assertNotIn("stop", [arg for call in calls for arg in call])
            self.assertNotIn("restart", [arg for call in calls for arg in call])
            self.assertEqual(calls[2][0], "run")
            self.assertEqual(calls[3][0], "run")
            self.assertNotIn("--volumes-from", calls[3])


if __name__ == "__main__":
    unittest.main()
