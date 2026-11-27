#!/usr/bin/env python3
"""Start the private DAVE replay from an authenticated encrypted transfer.

Only ciphertext is hosted publicly. The AES-GCM key is supplied through a
private environment variable, removed before launching subprocesses, and passed
to a disposable decryptor by stdin. No production credential is read.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile

AAD = "herdmate-dave-case-transfer-v1"

DECRYPT_CODE = r'''
import base64, json, subprocess, sys
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    # This runs in a disposable container, never in production. Use a binary
    # wheel only; fail cleanly rather than compile or modify the server image.
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-cache-dir",
                    "--only-binary=:all:", "cryptography"], check=True, stdout=sys.stderr)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
payload = json.load(sys.stdin)
envelope = payload["envelope"]
if envelope.get("algorithm") != "AES-256-GCM" or envelope.get("aad") != "herdmate-dave-case-transfer-v1":
    raise SystemExit("Unsupported encrypted package")
key = base64.b64decode(payload["key"], validate=True)
if len(key) != 32:
    raise SystemExit("Invalid transfer key")
plaintext = AESGCM(key).decrypt(base64.b64decode(envelope["nonce"], validate=True),
                               base64.b64decode(envelope["ciphertext"], validate=True),
                               envelope["aad"].encode())
sys.stdout.buffer.write(plaintext)
'''


def unpack(content, expected_hash):
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("Test package hash differs; stopped before extraction")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if any(Path(name).name != name for name in archive.namelist()):
            raise ValueError("Invalid archive path")
        if "start_test.py" not in archive.namelist():
            raise ValueError("The package is missing its launcher")
        root = Path(tempfile.mkdtemp(prefix="dave-test-code-", dir="/var/tmp"))
        archive.extractall(root)
    return root


def decrypt_command(image):
    return ["docker", "run", "--rm", "--interactive", "--pull=never",
            "--memory", "512m", "--cpus", "1", "--entrypoint", "python3",
            image, "-c", DECRYPT_CODE]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope-url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--production-container", required=True)
    args = parser.parse_args()
    key = os.environ.pop("DAVE_TRANSFER_KEY", "")
    if not key:
        raise SystemExit("The private transfer key is missing")
    # Inspect only the installed image identity. No production env/mounts/data
    # are passed into the decryptor. Google is not involved in the transfer.
    inspection = subprocess.run(["docker", "inspect", "--format", "{{.Image}}",
                                 args.production_container], capture_output=True,
                                text=True, check=True, timeout=20)
    image = inspection.stdout.strip()
    print("Fetching the encrypted test package directly to the server...", flush=True)
    with urlopen(args.envelope_url, timeout=60) as response:
        envelope = json.load(response)
    print("Unlocking it in a disposable container...", flush=True)
    result = subprocess.run(decrypt_command(image), input=json.dumps({
        "key": key, "envelope": envelope,
    }).encode(), capture_output=True, timeout=180)
    del key
    if result.returncode:
        print(result.stderr.decode(errors="replace")[-1600:], flush=True)
        raise SystemExit("The encrypted transfer could not be unlocked; test not started")
    root = unpack(result.stdout, args.sha256)
    print("Package verified. Starting the separate DAVE test...", flush=True)
    subprocess.run(["python3", str(root / "start_test.py"),
                    "--production-container", args.production_container], check=True)


if __name__ == "__main__":
    main()
