"""Run inside a disposable container with revised code and a separate Chroma DB."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, "/replay-code")
import field_case_replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    output = Path("/replay-output")
    suite = json.loads(Path("/replay-code/suite.json").read_text())
    total = sum(len(case["turns"]) for case in suite["cases"]) * args.repeats
    server_log = (output / "server.log").open("w")
    process = subprocess.Popen([sys.executable, "/replay-code/herdmate_vet_api.py"],
                               cwd="/replay-code", stdout=server_log, stderr=subprocess.STDOUT)
    try:
        print("Starting the isolated revised DAVE service...", flush=True)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("The revised service stopped during startup; server.log contains diagnostics")
            try:
                status = field_case_replay.request_json("http://127.0.0.1:5005/vet/status")
                if status.get("conversation_policy_revision") == field_case_replay.POLICY_REVISION:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError("The isolated service did not become ready within four minutes")
        original_request = field_case_replay.request_json
        completed = 0
        def progress_request(url, payload=None):
            nonlocal completed
            response = original_request(url, payload)
            if payload is not None:
                completed += 1
                print(f"Received reply {completed}/{total}; clinical review pending", flush=True)
            return response
        field_case_replay.request_json = progress_request
        report = field_case_replay.replay(suite, "http://127.0.0.1:5005", args.repeats)
        report["run_info"] = json.loads((output / "run-info.json").read_text())
        (output / "revised-results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print("REPLAY FINISHED: " + json.dumps(report["summary"]), flush=True)
        print("Answers and references saved in revised-results.json; clinical review pending.", flush=True)
    except Exception as error:
        (output / "setup-error.json").write_text(json.dumps({"error": str(error), "new_model_tested": False}))
        print("TEST STOPPED: " + str(error), flush=True)
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        server_log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
