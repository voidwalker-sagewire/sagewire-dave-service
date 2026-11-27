#!/usr/bin/env python3
"""Copy a stopped private replay, overlay pinned revised code, and rerun it."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
from urllib.request import urlopen

OVERLAYS = {
    "herdmate_vet_api.py": "herdmate_vet_api.py",
    "dave_conversation.py": "dave_conversation.py",
    "tests/field_case_replay.py": "field_case_replay.py",
    "tests/server_case_worker.py": "server_case_worker.py",
}


def replay_configuration(previous):
    if not previous.get("Name", "").lstrip("/").startswith("dave-case-replay-"):
        raise ValueError("Select a replay container, not production")
    if previous["State"].get("Running"):
        raise ValueError("Wait for the previous replay to stop")
    mounts = {mount["Destination"]: mount["Source"] for mount in previous["Mounts"]}
    if set(mounts) != {"/replay-code", "/replay-data", "/replay-output"}:
        raise ValueError("Unexpected previous replay mounts")
    if any(not source.startswith("/var/tmp/dave-") for source in mounts.values()):
        raise ValueError("Unexpected replay data locations")
    environment = dict(item.split("=", 1) for item in previous["Config"]["Env"] if "=" in item)
    if environment.get("CHROMA_HOST") != "127.0.0.1" or environment.get("CHROMA_DB_PATH") != "/replay-data":
        raise ValueError("The prior instance is not using an isolated reference DB")
    environment["DAVE_REPLAY_DIAGNOSTICS_DIR"] = "/replay-output"
    if any("\n" in value or "\r" in value for value in environment.values()):
        raise ValueError("Unsupported multiline environment value")
    return mounts, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-container", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.revision):
        raise SystemExit("A pinned 40-character source revision is required")
    old = json.loads(subprocess.check_output(["docker", "inspect", args.previous_container], text=True))[0]
    mounts, environment = replay_configuration(old)
    work = Path(tempfile.mkdtemp(prefix="dave-case-replay-", dir="/var/tmp"))
    if shutil.disk_usage(work).free < 300 * 1024 * 1024:
        raise SystemExit("Less than 300 MB disk space is available; stopped")
    code, data, output = work / "code", work / "data", work / "output"
    print("Copying the existing private test cases and separate reference DB...", flush=True)
    shutil.copytree(mounts["/replay-code"], code)
    shutil.copytree(mounts["/replay-data"], data)
    output.mkdir(mode=0o700)
    base = "https://raw.githubusercontent.com/voidwalker-sagewire/sagewire-dave-service/" + args.revision + "/"
    hashes = {}
    for source, destination in OVERLAYS.items():
        with urlopen(base + source, timeout=60) as response:
            content = response.read()
        (code / destination).write_bytes(content)
        hashes[destination] = hashlib.sha256(content).hexdigest()
    (output / "run-info.json").write_text(json.dumps({
        "source_revision": args.revision, "previous_container": args.previous_container,
        "source_sha256": hashes, "model": environment.get("CLAUDE_MODEL"),
        "format_preflight": True, "clinical_review": "pending",
    }, indent=2))
    name = "dave-case-replay-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    envfile = work / "model.env"
    descriptor = os.open(envfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in environment.items()))
    command = ["docker", "run", "--detach", "--pull=never", "--name", name,
               "--user", "0:0", "--network", "bridge", "--memory", "2g", "--cpus", "1",
               "--env-file", str(envfile)]
    for source, destination, readonly in ((code, "/replay-code", True), (data, "/replay-data", False), (output, "/replay-output", False)):
        command.extend(["--mount", f"type=bind,source={source},target={destination}" + (",readonly" if readonly else "")])
    command.extend(["--entrypoint", "python3", old["Image"], "/replay-code/server_case_worker.py"])
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    finally:
        envfile.unlink(missing_ok=True)
    print("Updated test started: " + name)
    print("Progress command: docker logs --tail 25 " + name)
    print("Results folder: " + str(output))
    print("It checks one real reply first and continues through the cases only if that format check succeeds.")


if __name__ == "__main__":
    main()
