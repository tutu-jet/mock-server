import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import load_settings


class ConfigTest(unittest.TestCase):
    def test_loads_env_and_resolves_credential_path_from_project(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            (root / ".env").write_text('FCM_PROJECT_ID=test-project\nGOOGLE_APPLICATION_CREDENTIALS="./credentials/test.json"\n')
            load_settings(root)
            self.assertEqual(os.environ["FCM_PROJECT_ID"], "test-project")
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], str((root / "credentials/test.json").resolve()))

    def test_process_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"FCM_PROJECT_ID": "override-project", "FCM_ACCESS_TOKEN": "temporary"}, clear=True):
            root = Path(directory)
            (root / ".env").write_text('FCM_PROJECT_ID=file-project\nFCM_ACCESS_TOKEN=file-token\n')
            load_settings(root)
            self.assertEqual(os.environ["FCM_PROJECT_ID"], "override-project")
            self.assertEqual(os.environ["FCM_ACCESS_TOKEN"], "temporary")

    def test_missing_env_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            load_settings(Path(directory))
            self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", os.environ)

    def test_absolute_credential_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/example.json"}, clear=True):
            load_settings(Path(directory))
            self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], str(Path('/tmp/example.json').resolve()))


if __name__ == "__main__":
    unittest.main()
