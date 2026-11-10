import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, models
from app.calendar_rules import MIGRATION_KEY
from app.matcher import Matcher
from app.rule_matching import request_matches
from web import routes


class CalendarRulesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(directory.name) / "rules.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        for entity, service in (("TimelineEvents", "calendar"), ("FocusedCalendarEvent", "mobile")):
            for status in (200, 403, 500):
                models.create_rule(f"[calendar] {entity}", f"*/odata/{service}/{entity}*", "GET",
                                   status, "{}", '{"value":[]}', enabled=False)
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def migrate(self):
        with db.connect() as conn:
            return db.migrate_calendar_rules(conn)

    def test_collection_presets_are_disabled_backed_up_and_idempotent(self):
        original = models.list_rules()
        self.assertTrue(self.migrate())
        self.assertFalse(self.migrate())
        all_rules = models.list_rules()
        self.assertEqual(len(all_rules), 12)
        for old in original:
            self.assertEqual(models.get_rule(old.id), old)
        additions = [r for r in all_rules if r.request_match == "calendar-list"]
        self.assertEqual(len(additions), 6)
        for rule in additions:
            self.assertFalse(rule.enabled)
            self.assertEqual(json.loads(rule.default_config),
                             {key: getattr(rule, key) for key in models.CONFIG_FIELDS})
            self.assertEqual(rule.state_key, {200: "nodata", 403: "noauth", 500: "error"}[rule.status_code])
            if rule.status_code == 200:
                entity = rule.name.split("] ", 1)[1].split(" ·", 1)[0]
                self.assertEqual(json.loads(rule.response_body),
                                 {"@odata.context": f"$metadata#{entity}/$entity", "value": []})
        backup = db.DB_PATH.with_name(db.DB_PATH.name + ".bak-calendar-list-v1")
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rules").fetchone()[0], 6)

    def test_precise_paths_and_page_membership(self):
        self.migrate()
        for entity in ("EventType", "CalendarSettings"):
            rule = next(r for r in models.list_rules()
                        if r.name.startswith(f"[calendar] {entity} ") and r.status_code == 200)
            for prefix in ("", "/backend", "/another/backend"):
                url = f"https://example.test{prefix}/odata/mobile/{entity}"
                self.assertTrue(request_matches(rule, url, "GET"))
                self.assertTrue(request_matches(rule, url + "?$top=1&$skip=0", "GET"))
                self.assertFalse(request_matches(rule, url + "s", "GET"))
                self.assertFalse(request_matches(rule, url + "('key')", "GET"))
                self.assertFalse(request_matches(rule, url, "POST"))
            self.assertFalse(request_matches(rule, f"https://example.test/odata/calendar/{entity}", "GET"))
        groups = routes._group_rules_by_domain(models.list_rules())
        calendar = next(g for g in groups if g["key"] == "calendar")
        pages = {p["key"]: p["entities"] for p in calendar["pages"]}
        self.assertEqual({e["name"] for e in pages["list"]},
                         {"TimelineEvents", "EventType", "CalendarSettings"})
        self.assertEqual({e["name"] for e in pages["unclassified"]}, {"FocusedCalendarEvent"})
        self.assertEqual((calendar["total"], calendar["enabled_count"]), (4, 0))
        html = self.client.get("/rules").text
        self.assertIn(">calendar list</h4>", html)
        self.assertIn("4 个接口 · 启用 0", html)
        self.assertNotIn("rule-actions", html)

    def test_initial_defaults_restore_without_changing_enabled_or_counts(self):
        self.migrate()
        rule = next(r for r in models.list_rules() if r.request_match == "calendar-list" and r.status_code == 200)
        models.set_rule_enabled(rule.id, True)
        models.increment_match_count(rule.id)
        config = {key: getattr(rule, key) for key in models.CONFIG_FIELDS}
        config.update(response_body="edited", status_code=201)
        self.client.post(f"/rules/{rule.id}", data=config)
        self.client.post(f"/rules/{rule.id}/restore")
        restored = models.get_rule(rule.id)
        self.assertEqual((restored.enabled, restored.match_count, restored.status_code), (True, 1, 200))
        self.assertEqual(restored.response_body, rule.response_body)
        self.assertEqual(restored.default_config, rule.default_config)

    def test_existing_custom_rule_and_failed_insert_are_not_overwritten(self):
        existing = models.create_rule("[calendar] EventType custom", "*/odata/mobile/EventType*", "GET",
                                      201, "{}", "custom", enabled=True)
        before = models.list_rules()
        with self.assertRaisesRegex(RuntimeError, "自定义规则"):
            self.migrate()
        self.assertEqual(models.list_rules(), before)
        models.delete_rule(existing)
        before = models.list_rules()
        with db.connect() as conn:
            conn.execute(
                "CREATE TRIGGER reject_calendar_settings BEFORE INSERT ON rules "
                "WHEN NEW.name LIKE '%CalendarSettings%' "
                "BEGIN SELECT RAISE(ABORT, 'test rejection'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.migrate()
        self.assertEqual(models.list_rules(), before)
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone())


if __name__ == "__main__":
    unittest.main()
