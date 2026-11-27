import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
import start_case_test_from_encrypted as transfer


class EncryptedTransferTests(unittest.TestCase):
    def payload(self, plaintext=b"private example"):
        key, nonce = bytes(range(32)), bytes(range(12))
        cipher = AESGCM(key).encrypt(nonce, plaintext, transfer.AAD.encode())
        return {"key": base64.b64encode(key).decode(), "envelope": {
            "algorithm": "AES-256-GCM", "aad": transfer.AAD,
            "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(cipher).decode(),
        }}

    def run_decryptor(self, payload):
        return subprocess.run([sys.executable, "-c", transfer.DECRYPT_CODE],
                              input=json.dumps(payload).encode(), capture_output=True, timeout=20)

    def test_exact_payload_round_trip(self):
        result = self.run_decryptor(self.payload())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"private example")

    def test_wrong_key_and_tampering_release_no_plaintext(self):
        payload = self.payload()
        payload["key"] = base64.b64encode(b"x" * 32).decode()
        result = self.run_decryptor(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        payload = self.payload()
        cipher = bytearray(base64.b64decode(payload["envelope"]["ciphertext"]))
        cipher[0] ^= 1
        payload["envelope"]["ciphertext"] = base64.b64encode(cipher).decode()
        result = self.run_decryptor(payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_decryptor_does_not_inherit_production_mounts_or_environment(self):
        command = transfer.decrypt_command("sha256:installed")
        for forbidden in ("--mount", "--volumes-from", "--env-file", "--privileged"):
            self.assertNotIn(forbidden, command)
        self.assertIn("--rm", command)
        self.assertIn("--pull=never", command)

    def test_archive_hash_is_verified_before_extracting(self):
        with patch.object(transfer.tempfile, "mkdtemp") as create:
            with self.assertRaises(ValueError):
                transfer.unpack(b"not the package", "0" * 64)
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
