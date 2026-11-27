#!/usr/bin/env python3
"""Capture one rejected model reply using only a stopped replay's test data."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

PROBE_CODE = r'''
import asyncio, contextlib, json, sys
from pathlib import Path
sys.path.insert(0, "/replay-code")
with contextlib.redirect_stdout(sys.stderr):
    import herdmate_vet_api as api

suite = json.loads(Path("/replay-code/suite.json").read_text())
case = suite["cases"][0]
question = case["turns"][0]["question"]
diagnostic = {"case": case["id"], "question": question, "clinical_status": "not_reviewed"}
real_create = api.claude.messages.create
real_parse = api.parse_grounded_answer

def capture_create(*args, **kwargs):
    response = real_create(*args, **kwargs)
    diagnostic["stop_reason"] = getattr(response, "stop_reason", None)
    diagnostic["model"] = getattr(response, "model", None)
    diagnostic["block_types"] = [getattr(block, "type", None) for block in response.content]
    return response

def capture_parse(raw, references):
    diagnostic["raw_answer"] = raw
    diagnostic["available_reference_ids"] = list(references)
    try:
        result = real_parse(raw, references)
        diagnostic["format_validation"] = "accepted"
        return result
    except Exception as error:
        diagnostic["format_validation"] = "rejected"
        diagnostic["validation_error"] = type(error).__name__ + ": " + str(error)
        raise

api.claude.messages.create = capture_create
api.parse_grounded_answer = capture_parse
print("Generating one diagnostic reply from the isolated test data...", file=sys.stderr, flush=True)
try:
    with contextlib.redirect_stdout(sys.stderr):
        answer = asyncio.run(api.ask_vet(api.VetQuestion(
            question=question, user_id="default", operation="RegressionReplay",
            session_id="diagnostic-only", conversation_history=[]
        )))
    diagnostic["status"] = 200
except api.HTTPException as error:
    diagnostic["status"] = error.status_code
    diagnostic["detail"] = error.detail
except Exception as error:
    diagnostic["exception"] = type(error).__name__ + ": " + str(error)

Path("/replay-output/one-reply-debug.json").write_text(json.dumps(diagnostic, indent=2, ensure_ascii=False))
print(json.dumps(diagnostic, indent=2, ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-container", required=True)
    args = parser.parse_args()
    if not args.test_container.startswith("dave-case-replay-"):
        raise SystemExit("Select the isolated replay container, not production")
    info = json.loads(subprocess.check_output(["docker", "inspect", args.test_container], text=True))[0]
    if info["State"].get("Running"):
        raise SystemExit("Wait for the replay to stop before using its copied DB")
    mounts = {mount["Destination"]: mount for mount in info["Mounts"]}
    needed = {"/replay-code", "/replay-data", "/replay-output"}
    if set(mounts) != needed or any(not mounts[path]["Source"].startswith("/var/tmp/dave-") for path in needed):
        raise SystemExit("Unexpected replay mounts; stopped")
    name = "dave-reply-check-" + secrets.token_hex(5)
    with tempfile.TemporaryDirectory(prefix="dave-reply-check-") as directory:
        envfile = Path(directory) / "model.env"
        descriptor = os.open(envfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for item in info["Config"]["Env"]:
                if "\n" in item or "\r" in item:
                    raise SystemExit("Unexpected multiline environment value")
                stream.write(item + "\n")
        command = ["docker", "run", "--rm", "--interactive", "--pull=never", "--name", name,
                   "--memory", "2g", "--cpus", "1", "--env-file", str(envfile)]
        for destination in sorted(needed):
            mount = "type=bind,source=" + mounts[destination]["Source"] + ",target=" + destination
            if destination == "/replay-code":
                mount += ",readonly"
            command.extend(["--mount", mount])
        command.extend(["--entrypoint", "python3", info["Image"], "-c", PROBE_CODE])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            print(result.stdout, end="")
            if result.returncode:
                print(result.stderr[-3000:])
                raise SystemExit(result.returncode)
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True)


if __name__ == "__main__":
    main()
