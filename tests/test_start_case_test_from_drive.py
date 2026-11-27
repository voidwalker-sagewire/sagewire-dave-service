import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import start_case_test_from_drive as bootstrap


class DriveBootstrapTests(unittest.TestCase):
    def archive(self, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return buffer.getvalue()

    def test_hash_is_checked_before_extraction(self):
        content = self.archive({"start_test.py": "print('example')"})
        with patch.object(bootstrap.tempfile, "mkdtemp") as create:
            with self.assertRaises(ValueError):
                bootstrap.unpack_verified_bundle(content, "0" * 64)
            create.assert_not_called()

    def test_unsafe_bundle_paths_are_rejected(self):
        content = self.archive({"../start_test.py": "bad"})
        with self.assertRaises(ValueError):
            bootstrap.unpack_verified_bundle(content, hashlib.sha256(content).hexdigest())

    def test_verified_bundle_is_extracted_to_private_working_directory(self):
        content = self.archive({"start_test.py": "# launcher", "suite.json": "{}"})
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bootstrap.tempfile, "mkdtemp", return_value=directory):
                root = bootstrap.unpack_verified_bundle(content, hashlib.sha256(content).hexdigest())
            self.assertEqual((root / "start_test.py").read_text(), "# launcher")
            self.assertEqual((root / "suite.json").read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
