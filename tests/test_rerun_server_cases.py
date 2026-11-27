import unittest
import rerun_server_cases as rerun


class RerunConfigurationTests(unittest.TestCase):
    def configuration(self):
        return {"Name": "/dave-case-replay-example", "State": {"Running": False},
                "Config": {"Env": ["CHROMA_HOST=127.0.0.1", "CHROMA_DB_PATH=/replay-data", "ANTHROPIC_API_KEY=test-placeholder"]},
                "Mounts": [{"Destination": destination, "Source": "/var/tmp/dave-case-replay-example/" + name}
                           for destination, name in (("/replay-code", "code"), ("/replay-data", "data"), ("/replay-output", "output"))]}

    def test_stopped_isolated_configuration_enables_private_diagnostics(self):
        config = self.configuration()
        mounts, environment = rerun.replay_configuration(config)
        self.assertEqual(environment["DAVE_REPLAY_DIAGNOSTICS_DIR"], "/replay-output")
        self.assertEqual(environment["CHROMA_HOST"], "127.0.0.1")
        self.assertEqual(mounts["/replay-data"], "/var/tmp/dave-case-replay-example/data")

    def test_production_running_or_external_db_configuration_is_rejected(self):
        for change in ("production", "running", "external_db", "unexpected_mount"):
            config = self.configuration()
            if change == "production":
                config["Name"] = "/live-dave"
            elif change == "running":
                config["State"]["Running"] = True
            elif change == "external_db":
                config["Config"]["Env"][0] = "CHROMA_HOST=live-db"
            else:
                config["Mounts"][0]["Source"] = "/production/data"
            with self.subTest(change=change), self.assertRaises(ValueError):
                rerun.replay_configuration(config)


if __name__ == "__main__":
    unittest.main()
