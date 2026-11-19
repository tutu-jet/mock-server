import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, models
from app.matcher import Matcher
from app.system_rules import LEGACY_PATTERN, MIGRATION_KEY
from web import routes


class SystemRulesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(directory.name) / "rules.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        self.original_ids = []
        for status in (200, 403, 500):
            self.original_ids.append(models.create_rule(
                f"[system] CACCloudAvaStatus · {status}", LEGACY_PATTERN, "GET", status,
                '{"Content-Type":"application/json"}', '{"baseline":true}', enabled=False,
            ))
        for entity in ("CACSystemDetailHeader", "SystemBundle", "BundleSystem"):
            models.create_rule(f"[system] {entity}", f"*/odata/mobile/{entity}*", "GET",
                               200, "{}", '{"value":[]}', enabled=False)
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def migrate(self):
        with db.connect() as conn:
            return db.migrate_system_rules(conn)

    def select(self, scope, state):
        return self.client.post("/rules/entity/state", data={
            "pattern": LEGACY_PATTERN, "method": "GET", "request_match": scope, "state": state,
        })

    def test_collection_and_keyed_paths_have_independent_states_and_groups(self):
        self.migrate()
        self.assertEqual(self.select("system-list", "nodata").status_code, 200)
        self.assertEqual(self.select("system-detail", "noauth").status_code, 200)
        for prefix in ("", "/backend", "/other/backend"):
            for host in ("one.example.test", "two.example.test"):
                base = f"https://{host}{prefix}/odata/mobile/CACCloudAvaStatus"
                for query in ("", "?$filter=field+eq+%27a%27&$top=1", "?$top=1&$filter=field%20eq%20%27b%27"):
                    self.assertEqual(routes.matcher.match(base + query, "GET").status_code, 200)
                for key in ("('synthetic-a')", "('synthetic-b')", "('escaped''quote')",
                            "(%27encoded-key%27)", "%28%27encoded-parens%27%29"):
                    match = routes.matcher.match(base + key + "?irrelevant=1", "GET")
                    self.assertEqual((match.request_match, match.status_code), ("system-detail", 403))
        groups = routes._group_rules_by_domain(models.list_rules())
        system = next(g for g in groups if g["key"] == "system")
        pages = {p["key"]: p["entities"] for p in system["pages"]}
        self.assertEqual((system["total"], system["enabled_count"]), (5, 2))
        self.assertEqual(len(pages["list"]), 1)
        self.assertEqual({e["name"] for e in pages["detail"]},
                         {"CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle"})
        self.assertEqual({e["name"] for e in pages["bundle-detail"]}, {"BundleSystem"})
        html = self.client.get("/rules").text
        self.assertIn(">system list</h4>", html)
        self.assertIn(">system detail</h4>", html)
        self.assertIn("5 个接口 · 启用 2", html)
        self.assertNotIn("rule-actions", html)
        self.select("system-list", "off")
        self.assertIsNone(routes.matcher.match(
            "https://example.test/backend/odata/mobile/CACCloudAvaStatus?$top=1", "GET",
        ))
        self.assertEqual(routes.matcher.match(
            "https://example.test/backend/odata/mobile/CACCloudAvaStatus('still-on')", "GET",
        ).status_code, 403)

    def test_negative_paths_cannot_hit_either_scope(self):
        self.migrate()
        self.select("system-list", "nodata")
        self.select("system-detail", "error")
        base = "https://example.test/backend/odata/mobile/CACCloudAvaStatus"
        for suffix in ("('')", "()", "('unterminated)", "Other", "/navigation", "('key')/navigation",
                       "?key=value/odata/mobile/NotARealPath"):
            # A query containing an unrelated path still is a collection request.
            if suffix.startswith("?"):
                self.assertEqual(routes.matcher.match(base + suffix, "GET").request_match, "system-list")
            else:
                self.assertIsNone(routes.matcher.match(base + suffix, "GET"), suffix)
        self.assertIsNone(routes.matcher.match(base, "POST"))
        self.assertIsNone(routes.matcher.match(
            "https://example.test/not-odata/CACCloudAvaStatus('key')", "GET",
        ))

    def test_bundle_pages_keep_system_bundle_separate_and_preserve_saved_rules(self):
        for entity in ("BundleStatuses", "BundleSubscriptions", "BundleDetailHeader"):
            for status in (200, 403, 500):
                models.create_rule(f"[system] {entity}", f"*/odata/mobile/{entity}*", "GET",
                                   status, "{}", '{"value":[]}', enabled=False)
        models.create_rule("[system] AffectedBundle", "*/odata/mobile/AffectedBundle*", "GET",
                           200, "{}", "", enabled=False)
        self.migrate()
        before = models.list_rules()
        system = next(g for g in routes._group_rules_by_domain(before) if g["key"] == "system")
        pages = {p["key"]: p for p in system["pages"]}
        self.assertEqual({e["name"] for e in pages["bundle-list"]["entities"]}, {"BundleStatuses"})
        self.assertEqual({e["name"] for e in pages["bundle-detail"]["entities"]},
                         {"BundleSubscriptions", "BundleDetailHeader", "BundleSystem"})
        self.assertEqual({e["name"] for e in pages["detail"]["entities"]},
                         {"CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle"})
        self.assertEqual({e["name"] for e in pages["unclassified"]["entities"]}, {"AffectedBundle"})
        self.assertEqual((system["total"], system["enabled_count"]), (9, 0))
        html = self.client.get("/rules").text
        self.assertIn(">bundle list</h4>", html)
        self.assertIn(">bundle detail</h4>", html)
        self.assertIn("9 个接口 · 启用 0", html)
        self.assertNotIn("rule-actions", html)
        self.assertEqual(models.list_rules(), before)

    def test_snapshot_and_original_runtime_fields_survive_idempotent_split(self):
        models.set_rule_enabled(self.original_ids[2], True)
        models.increment_match_count(self.original_ids[2])
        before = {r.id: r for r in models.list_rules()}
        self.assertTrue(self.migrate())
        self.assertFalse(self.migrate())
        self.assertEqual(len(models.list_rules()), 9)
        for rule_id in self.original_ids:
            old, current = before[rule_id], models.get_rule(rule_id)
            self.assertEqual(current.request_match, "system-list")
            for field in ("name", "response_body", "response_headers", "status_code", "enabled",
                          "default_config", "state_key", "match_count", "created_at", "updated_at"):
                self.assertEqual(getattr(current, field), getattr(old, field), field)
            clone = next(r for r in models.list_rules()
                         if r.request_match == "system-detail" and r.state_key == old.state_key)
            self.assertEqual((clone.default_config, clone.enabled, clone.match_count),
                             (old.default_config, old.enabled, 0))
        backup = db.DB_PATH.with_name(db.DB_PATH.name + ".bak-system-paths-v1")
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rules").fetchone()[0], 6)
            self.assertEqual(conn.execute("SELECT sum(enabled) FROM rules").fetchone()[0], 1)

    def test_legacy_defaults_restore_scopes_without_rewriting_snapshot(self):
        self.migrate()
        targets = [r for r in models.list_rules()
                   if r.request_match in ("system-list", "system-detail") and r.state_key == "nodata"]
        for rule in targets:
            snapshot = rule.default_config
            data = {field: getattr(rule, field) for field in models.CONFIG_FIELDS}
            data.update(response_body="edited", request_match="")
            response = self.client.post(f"/rules/{rule.id}", data=data)
            self.assertIn("规则已保存", json.loads(response.headers["HX-Trigger"])["toast"]["msg"])
            response = self.client.post(f"/rules/{rule.id}/restore")
            self.assertIn("已恢复", json.loads(response.headers["HX-Trigger"])["toast"]["msg"])
            restored = models.get_rule(rule.id)
            self.assertEqual(restored.request_match, rule.request_match)
            self.assertEqual(restored.default_config, snapshot)
            self.assertEqual(restored.response_body, '{"baseline":true}')
            self.assertFalse(restored.enabled)
        self.select("system-list", "nodata")
        self.assertIsNone(routes.matcher.match(
            "https://example.test/backend/odata/mobile/CACCloudAvaStatus('off-detail')", "GET",
        ))

    def test_restore_conflict_does_not_change_config_snapshot_or_flags(self):
        self.migrate()
        rule = models.get_rule(self.original_ids[0])
        data = {field: getattr(rule, field) for field in models.CONFIG_FIELDS}
        data["url_pattern"] = "*/temporarily-moved"
        self.client.post(f"/rules/{rule.id}", data=data)
        models.create_rule("[system] custom", LEGACY_PATTERN, "GET", 200,
                           "{}", "custom", enabled=False, request_match="system-list")
        before = models.list_rules()
        response = self.client.post(f"/rules/{rule.id}/restore")
        self.assertIn("冲突", json.loads(response.headers["HX-Trigger"])["toast"]["msg"])
        self.assertEqual(models.list_rules(), before)

    def test_existing_custom_content_and_older_default_are_both_preserved(self):
        rule = models.get_rule(self.original_ids[0])
        config = {field: getattr(rule, field) for field in models.CONFIG_FIELDS}
        config.update(status_code=201, response_body="current custom content")
        models.update_rule(rule.id, **config, enabled=False)
        self.migrate()
        collection = models.get_rule(rule.id)
        detail = next(r for r in models.list_rules()
                      if r.request_match == "system-detail" and r.state_key == "nodata")
        for migrated in (collection, detail):
            self.assertEqual((migrated.status_code, migrated.response_body),
                             (201, "current custom content"))
            self.assertEqual(migrated.default_config, rule.default_config)
        self.client.post(f"/rules/{detail.id}/restore")
        restored = models.get_rule(detail.id)
        self.assertEqual((restored.status_code, restored.response_body, restored.request_match),
                         (200, '{"baseline":true}', "system-detail"))
        self.assertEqual(models.get_rule(rule.id), collection)

    def test_conflicts_and_failed_insert_roll_back_all_rule_changes(self):
        models.set_rule_enabled(self.original_ids[0], True)
        models.set_rule_enabled(self.original_ids[1], True)
        before = models.list_rules()
        with self.assertRaisesRegex(RuntimeError, "冲突"):
            self.migrate()
        self.assertEqual(models.list_rules(), before)
        models.set_rule_enabled(self.original_ids[1], False)
        before = models.list_rules()
        with db.connect() as conn:
            conn.execute(
                "CREATE TRIGGER reject_system_split BEFORE INSERT ON rules "
                "WHEN NEW.request_match='system-detail' "
                "BEGIN SELECT RAISE(ABORT, 'test rejection'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.migrate()
        self.assertEqual(models.list_rules(), before)
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone())


if __name__ == "__main__":
    unittest.main()
