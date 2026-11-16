import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import push_history


class PushHistoryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(push_history, "DB_PATH", Path(directory.name) / "push.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.payload = {"message": {"token": "device-token", "notification": {"title": "标题", "body": "正文"}, "data": {"source": "Incident", "sourceId": "pointer"}, "android": {"priority": "high"}}}

    def test_persists_across_connections_and_preserves_platform_options(self):
        entry_id = push_history.start(self.payload, "test-project")
        push_history.finish(entry_id, "accepted", "message-id")
        entry = push_history.get_entry(entry_id)
        self.assertEqual(entry["payload"], self.payload)
        self.assertEqual(entry["result"], "message-id")
        self.assertEqual(entry["status"], "accepted")
        self.assertNotIn("token", push_history.list_entries()[0])

    def test_interruptions_remain_unknown_and_failed_results_preserved(self):
        entry_id = push_history.start(self.payload, "test-project")
        self.assertEqual(push_history.get_entry(entry_id)["status"], "unknown")
        push_history.finish(entry_id, "failed", "FCM HTTP 403")
        self.assertEqual(push_history.get_entry(entry_id)["result"], "FCM HTTP 403")

    def test_retains_latest_100_records_and_never_exposes_full_token_in_summary(self):
        first = push_history.start(self.payload, "project")
        for _ in range(100):
            latest = push_history.start(self.payload, "project")
        entries = push_history.list_entries()
        self.assertEqual(len(entries), 100)
        self.assertEqual(entries[0]["id"], latest)
        self.assertIsNone(push_history.get_entry(first))
        self.assertNotIn("device-token", str(entries))


if __name__ == "__main__":
    unittest.main()
