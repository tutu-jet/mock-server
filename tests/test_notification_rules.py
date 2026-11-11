import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, models
from app.notification_rules import MIGRATION_KEY
from app.matcher import Matcher
from app.rule_matching import request_matches
from web import routes


class NotificationRulesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(directory.name) / "rules.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def migrate(self):
        with db.connect() as conn:
            return db.migrate_notification_rules(conn)

    def test_presets_seeded_disabled_backed_up_and_idempotent(self):
        self.assertTrue(self.migrate())
        self.assertFalse(self.migrate())  # 幂等：第二次不插入
        additions = [r for r in models.list_rules() if r.name.startswith("[notification] ")]
        self.assertEqual(len(additions), 6)  # 2 实体 × 3 态
        entities = {r.name.split("] ", 1)[1].split(" ·", 1)[0] for r in additions}
        self.assertEqual(entities, {"CommunityNotification", "PushNotification"})
        for rule in additions:
            self.assertFalse(rule.enabled)                       # 默认透传
            self.assertEqual(rule.method, "GET")
            self.assertEqual(rule.request_match, "")             # 不按变体拆分
            self.assertEqual(json.loads(rule.default_config),
                             {key: getattr(rule, key) for key in models.CONFIG_FIELDS})
            self.assertEqual(rule.state_key,
                             {200: "nodata", 403: "noauth", 500: "error"}[rule.status_code])
            if rule.status_code == 200:
                entity = rule.name.split("] ", 1)[1].split(" ·", 1)[0]
                self.assertEqual(json.loads(rule.response_body),
                                 {"@odata.context": f"$metadata#{entity}/$entity", "value": []})
            else:
                self.assertEqual(rule.response_body, "")
        backup = db.DB_PATH.with_name(db.DB_PATH.name + ".bak-notification-list-v1")
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rules").fetchone()[0], 0)

    def test_patterns_match_real_paths_and_own_group_page(self):
        self.migrate()
        for entity in ("CommunityNotification", "PushNotification"):
            rule = next(r for r in models.list_rules()
                        if r.name.startswith(f"[notification] {entity} ") and r.status_code == 200)
            for prefix in ("", "/backend", "/another/backend"):
                url = f"https://example.test{prefix}/odata/mobile/{entity}"
                self.assertTrue(request_matches(rule, url, "GET"))
                self.assertTrue(request_matches(rule, url + "?$top=1&$skip=0", "GET"))
                self.assertFalse(request_matches(rule, url, "POST"))
        groups = routes._group_rules_by_domain(models.list_rules())
        notification = next(g for g in groups if g["key"] == "notification")
        pages = {p["key"]: p["entities"] for p in notification["pages"]}
        self.assertEqual({e["name"] for e in pages["list"]},
                         {"CommunityNotification", "PushNotification"})
        self.assertNotIn("unclassified", pages)
        self.assertEqual((notification["total"], notification["enabled_count"]), (2, 0))
        html = self.client.get("/rules").text
        self.assertIn("Notification", html)

    def test_existing_custom_rule_is_not_overwritten(self):
        existing = models.create_rule(
            "[notification] PushNotification custom", "*/odata/mobile/PushNotification*",
            "GET", 201, "{}", "custom", enabled=True)
        before = models.list_rules()
        with self.assertRaisesRegex(RuntimeError, "自定义规则"):
            self.migrate()
        self.assertEqual(models.list_rules(), before)
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone())
        models.delete_rule(existing)


if __name__ == "__main__":
    unittest.main()
