#!/usr/bin/env python3
"""Fetch a private case bundle using DAVE's on-server Google service account.

No phone download, public sharing, copied key, or new local credential is needed.
The exact account must already have reader access to the exact Drive file.
"""
import argparse
import hashlib
import io
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


DOWNLOAD_CODE = r'''
import os, sys
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request

try:
    expected_email, file_id = sys.argv[1:3]
    credentials = Credentials.from_service_account_file(
        os.environ.get("CREDENTIALS_FILE", "/root/credentials.json"),
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    if credentials.service_account_email != expected_email:
        raise RuntimeError("DAVE's Google account differs from the verified account; stopped")
    credentials.refresh(Request())
    response = requests.get(
        "https://www.googleapis.com/drive/v3/files/" + file_id,
        params={"alt": "media"},
        headers={"Authorization": "Bearer " + credentials.token}, timeout=90
    )
    if not response.ok:
        raise RuntimeError("Drive returned HTTP " + str(response.status_code) + ": " + response.text[:1000])
    sys.stdout.buffer.write(response.content)
except Exception as error:
    print(type(error).__name__ + ": " + str(error)[:1200], file=sys.stderr)
    raise SystemExit(1)
'''


def unpack_verified_bundle(content, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid expected bundle hash")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Package verification failed; the test was not started")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if any(Path(name).name != name for name in archive.namelist()):
            raise ValueError("Invalid bundle path")
        if "start_test.py" not in archive.namelist():
            raise ValueError("The package has no test launcher")
        root = Path(tempfile.mkdtemp(prefix="dave-test-code-", dir="/var/tmp"))
        archive.extractall(root)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--production-container", required=True)
    parser.add_argument("--service-account-email", required=True)
    args = parser.parse_args()
    print("Fetching the private test package directly to the server...", flush=True)
    result = subprocess.run([
        "docker", "exec", "-i", args.production_container, "python3", "-",
        args.service_account_email, args.file_id,
    ], input=DOWNLOAD_CODE.encode(), capture_output=True, timeout=120)
    if result.returncode:
        print(result.stderr.decode(errors="replace")[-1800:], flush=True)
        raise SystemExit("The package could not be fetched; the test was not started")
    root = unpack_verified_bundle(result.stdout, args.sha256)
    print("Package verified. Starting the separate test run...", flush=True)
    subprocess.run(["python3", str(root / "start_test.py"),
                    "--production-container", args.production_container], check=True)


if __name__ == "__main__":
    main()
