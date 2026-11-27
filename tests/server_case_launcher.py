#!/usr/bin/env python3
"""Launch the private replay bundle beside an existing Docker DAVE service.

Run from an extracted, verified bundle. Production is inspected and its vet
collection read; no production container/volume/config is changed. Secrets
remain on the same host and are never printed. The test has its own DB and no
published ports. A real model replay incurs the normal API usage of that model.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

EXPECTED_PRODUCTION_REVISION = "6304a6467801b0b4e2501277849c2160773b0984"


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True,
                          text=True, timeout=120, **kwargs).stdout


def model_environment(raw_environment):
    current = dict(item.split("=", 1) for item in raw_environment if "=" in item)
    key = current.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("The selected DAVE container has no model credential")
    # Only model access crosses into the test container, not Sheets credentials
    # or production Chroma/RAG addresses. These values are never printed.
    env = {"ANTHROPIC_API_KEY": key,
           "CLAUDE_MODEL": current.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
           "CHROMA_HOST": "127.0.0.1", "CHROMA_PORT": "9",
           "CHROMA_DB_PATH": "/replay-data", "PORT": "5005",
           "CREDENTIALS_FILE": "/no-production-credentials.json",
           "RAG_SERVICE_URL": "", "ANONYMIZED_TELEMETRY": "False"}
    if current.get("ANTHROPIC_BASE_URL"):
        env["ANTHROPIC_BASE_URL"] = current["ANTHROPIC_BASE_URL"]
    if any("\n" in value or "\r" in value or "\x00" in value for value in env.values()):
        raise ValueError("The runtime has an environment value that cannot be copied safely")
    return env


def verify_bundle(root):
    manifest = json.loads((root / "bundle-manifest.json").read_text())
    for name, expected_hash in manifest["sha256"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Bundle manifest includes an unsafe path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Bundle verification failed: {name}")
    return manifest


def test_container_args(image, code, data, output, envfile, name, repeats):
    # No --volumes-from, production networks, Docker socket, host networking,
    # proxy labels, restart policy, or host port mappings are inherited.
    return ["run", "--detach", "--pull=never", "--name", name,
            "--memory", "2g", "--cpus", "1",
            "--user", "0:0", "--network", "bridge", "--env-file", str(envfile),
            "--mount", f"type=bind,source={code},target=/replay-code,readonly",
            "--mount", f"type=bind,source={data},target=/replay-data",
            "--mount", f"type=bind,source={output},target=/replay-output",
            "--entrypoint", "python3", image,
            "/replay-code/server_case_worker.py", "--repeats", str(repeats)]


def launch(root, production_container, repeats):
    if repeats not in (1, 2, 3):
        raise ValueError("Choose 1, 2 or 3 runs per conversation")
    verify_bundle(root)
    production = json.loads(docker(["inspect", production_container]))[0]
    if not production.get("State", {}).get("Running"):
        raise ValueError("The selected production DAVE container is not running")
    image_tag = production["Config"].get("Image", "")
    if image_tag.rsplit(":", 1)[-1] != EXPECTED_PRODUCTION_REVISION:
        raise ValueError("DAVE's deployed revision changed; inspect it before reusing this test launcher")
    env = model_environment(production["Config"].get("Env", []))
    image = production["Image"]  # Exact installed image ID; never pull/build.
    work = Path(tempfile.mkdtemp(prefix="dave-case-replay-", dir="/var/tmp"))
    work.chmod(0o700)
    if __import__("shutil").disk_usage(work).free < 300 * 1024 * 1024:
        raise ValueError("The server has less than 300 MB free; no test was started")
    data, output = work / "data", work / "output"
    data.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    envfile = work / "model.env"
    descriptor = os.open(envfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in env.items()))
    name = "dave-case-replay-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    try:
        print("Reading DAVE's veterinary reference collection...", flush=True)
        snapshot = docker(["exec", "-i", production_container, "python3", "-"],
                          input=(root / "export_vet_corpus.py").read_text())
        corpus = json.loads(snapshot)
        if not corpus["ids"]:
            raise ValueError("The reference export is empty; no test was started")
        (output / "corpus.json").write_text(snapshot)
        (output / "corpus.json").chmod(0o600)
        print(f"Exported {len(corpus['ids'])} reference passages. Creating the separate test DB...", flush=True)
        docker(["run", "--rm", "--pull=never", "--user", "0:0", "--network", "none",
                "--memory", "2g", "--cpus", "1",
                "--mount", f"type=bind,source={root},target=/replay-code,readonly",
                "--mount", f"type=bind,source={data},target=/replay-data",
                "--mount", f"type=bind,source={output},target=/replay-output,readonly",
                "--entrypoint", "python3", image, "/replay-code/seed_vet_corpus.py"])
        (output / "run-info.json").write_text(json.dumps({
            "container": name, "production_revision": EXPECTED_PRODUCTION_REVISION,
            "model": env["CLAUDE_MODEL"], "reference_passages": len(corpus["ids"]),
            "repeats": repeats, "bundle": json.loads((root / "bundle-manifest.json").read_text()),
        }, indent=2))
        docker(test_container_args(image, root, data, output, envfile, name, repeats))
    finally:
        envfile.unlink(missing_ok=True)
    print("\nTest started in the background: " + name)
    print("Progress command: docker logs --tail 15 " + name)
    print("Results folder: " + str(output))
    print("When finished, share revised-results.json from that folder for answer-by-answer review.")
    print("The test publishes no ports and makes no changes to the live DAVE container.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-container", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    try:
        launch(Path(__file__).resolve().parent, args.production_container, args.repeats)
    except Exception as error:
        # Docker error argv contains only paths/IDs, never inline credentials.
        # Do not echo captured stderr or the inspected environment.
        print("Test setup stopped: " + type(error).__name__ + ": " + str(error), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
