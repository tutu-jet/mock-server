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
from app.rule_matching import request_matches
from web import routes


class RuleDefaultsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(directory.name) / "rules.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        self.ids = {}
        for status in (200, 403, 500):
            self.ids[status] = models.create_rule(
                f"[case] CaseListItem-My · {status}", "*/odata/mobile/CaseListItem*", "GET",
                status, '{"Content-Type":"application/json"}', '{"value":[]}',
                enabled=False, request_match="case-my",
            )
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.headers = {"HX-Current-URL": "http://localhost/rules?q=CaseListItem&method=GET"}

    def edit(self, rule_id, **changes):
        rule = models.get_rule(rule_id)
        data = {field: getattr(rule, field) for field in models.CONFIG_FIELDS}
        data.update(changes)
        return self.client.post(f"/rules/{rule_id}", data=data, headers=self.headers)

    def restore(self, rule_id):
        return self.client.post(f"/rules/{rule_id}/restore", headers=self.headers)

    def test_initial_snapshot_is_persistent_and_immutable(self):
        rule_id = self.ids[200]
        original = models.get_rule(rule_id)
        snapshot = json.loads(original.default_config)
        self.assertEqual(set(snapshot), set(models.CONFIG_FIELDS))
        self.assertNotIn("enabled", snapshot)
        self.edit(rule_id, response_body='{"custom":1}')
        self.edit(rule_id, response_body='{"custom":2}')
        with db.connect() as conn:
            db.migrate_rule_defaults(conn)
            db.migrate_rule_defaults(conn)
        self.assertEqual(models.get_rule(rule_id).default_config, original.default_config)
        self.restore(rule_id)
        self.assertEqual(models.get_rule(rule_id).response_body, original.response_body)

    def test_edit_restore_preserve_enabled_count_and_state_identity(self):
        rule_id = self.ids[403]
        self.headers["HX-Current-URL"] += "&domain=case&enabled_only=true"
        models.create_rule(
            "[calendar] FocusedCalendarEvent", "*/FocusedCalendarEvent*", "GET",
            200, "{}", "", enabled=False,
        )
        models.set_rule_enabled(rule_id, True)
        models.increment_match_count(rule_id)
        result = self.edit(rule_id, status_code=418, response_body="custom", enabled="")
        self.assertNotIn("HX-Redirect", result.headers)
        rule = models.get_rule(rule_id)
        self.assertEqual((rule.enabled, rule.match_count, rule.state_key), (True, 1, "noauth"))
        self.assertEqual(routes.matcher.match(
            "https://example.test/backend/odata/mobile/CaseListItem?%24filter=auth+eq+%27ME%27", "GET",
        ).status_code, 418)
        html = self.client.get("/rules").text
        self.assertEqual(html.count(f'hx-get="/rules/{rule_id}/edit"'), 2)
        self.assertIn('sb-noauth">418</span>', html)
        self.assertIn('value="noauth"', html)
        response = self.restore(rule_id)
        self.assertIn('id="rules-content" hx-swap-oob="outerHTML"', response.text)
        self.assertIn('id="page-toolbar" hx-swap-oob="outerHTML"', response.text)
        self.assertIn('id="domain-case"', response.text)
        self.assertIn('id="domain-calendar"', response.text)
        self.assertNotIn('name="enabled_only"', response.text)
        self.assertNotIn('name="q"', response.text)
        restored = models.get_rule(rule_id)
        self.assertEqual((restored.status_code, restored.enabled, restored.match_count), (403, True, 1))
        self.assertEqual(routes.matcher.match(
            "https://example.test/backend/odata/mobile/CaseListItem?%24filter=auth+eq+%27ME%27", "GET",
        ).status_code, 403)

    def test_disabled_all_states_have_details_edit_and_restore(self):
        html = self.client.get("/rules").text
        self.assertNotIn(">查看详情</button>", html)
        self.assertEqual(html.count('title="Edit rule" aria-label="编辑 '), 2)
        self.assertEqual(html.count('hx-select="unset"'), 2)
        self.assertLess(html.index('class="state-badge'), html.index('title="Edit rule" aria-label="编辑 '))
        self.assertNotIn("rule-actions", html)
        self.assertNotIn("rule-action-row", html)
        for rule_id in self.ids.values():
            detail = self.client.get(f"/rules/{rule_id}/detail").text
            edit = self.client.get(f"/rules/{rule_id}/edit").text
            for value in ("Response Headers", "Response Body", "CaseListItem", "auth = ME", "恢复默认"):
                self.assertIn(value, detail)
            self.assertNotIn("Request Headers", detail)
            self.assertNotIn("Request Body", detail)
            self.assertIn('aria-label="选择规则状态"', detail)
            for sibling_id in self.ids.values():
                self.assertIn(f'hx-get="/rules/{sibling_id}/detail"', detail)
                self.assertIn(f'hx-get="/rules/{sibling_id}/edit"', edit)
            self.assertIn('hx-confirm=', detail)
            self.edit(rule_id, response_body="edited", enabled="on")
            self.restore(rule_id)
            self.assertFalse(models.get_rule(rule_id).enabled)

    def test_restore_conflicts_do_not_touch_any_rules_or_snapshots(self):
        rule_id = self.ids[200]
        self.edit(rule_id, url_pattern="*/moved")
        duplicate = models.create_rule(
            "[case] duplicate", "*/odata/mobile/CaseListItem*", "GET",
            200, "{}", "duplicate", enabled=False, request_match="case-my",
        )
        before = models.list_rules()
        response = self.restore(rule_id)
        self.assertIn("冲突", json.loads(response.headers["HX-Trigger"])["toast"]["msg"])
        self.assertEqual(response.headers["HX-Reswap"], "none")
        self.assertEqual(models.list_rules(), before)
        models.delete_rule(duplicate)
        models.set_rule_enabled(rule_id, True)
        broad = models.create_rule(
            "[case] broad", "*/odata/mobile/CaseListItem*", "GET", 201, "{}", "",
            enabled=True,
        )
        before = models.list_rules()
        self.assertIn("冲突", json.loads(self.restore(rule_id).headers["HX-Trigger"])["toast"]["msg"])
        self.assertEqual(models.list_rules(), before)
        models.delete_rule(broad)
        self.restore(rule_id)
        self.assertTrue(models.get_rule(rule_id).enabled)

    def test_identity_edits_and_slot_conflicts_are_explicit(self):
        rule_id = self.ids[200]
        response = self.edit(rule_id, url_pattern="*/new", method="POST", request_match="")
        self.assertNotIn("HX-Redirect", response.headers)
        self.assertEqual(models.get_rule(rule_id).method, "POST")
        self.restore(rule_id)
        self.assertEqual(models.get_rule(rule_id).request_match, "case-my")
        other = models.create_rule("[case] another", "*/taken", "GET", 200, "{}", "", enabled=False)
        before = models.get_rule(rule_id)
        result = self.edit(rule_id, url_pattern="*/taken", request_match="")
        self.assertIn("冲突", json.loads(result.headers["HX-Trigger"])["toast"]["msg"])
        self.assertEqual(models.get_rule(rule_id), before)
        self.assertIsNotNone(models.get_rule(other))

    def test_existing_database_upgrade_has_backup_and_current_baseline(self):
        rule_id = self.ids[200]
        with db.connect() as conn:
            conn.execute("ALTER TABLE rules DROP COLUMN default_config")
            conn.execute("ALTER TABLE rules DROP COLUMN state_key")
            conn.execute("UPDATE rules SET response_body='current baseline' WHERE id=?", (rule_id,))
            db.migrate_rule_defaults(conn)
        rule = models.get_rule(rule_id)
        self.assertEqual(json.loads(rule.default_config)["response_body"], "current baseline")
        backup = db.DB_PATH.with_name(db.DB_PATH.name + ".bak-rule-defaults-v1")
        with sqlite3.connect(backup) as conn:
            self.assertNotIn("default_config", {r[1] for r in conn.execute("pragma table_info(rules)")})
        self.edit(rule_id, response_body="later edit")
        self.restore(rule_id)
        self.assertEqual(models.get_rule(rule_id).response_body, "current baseline")

    def test_missing_and_invalid_input_do_not_destroy_drawer_or_config(self):
        self.assertEqual(self.client.get("/rules/999/detail").status_code, 404)
        self.assertEqual(self.restore(999).status_code, 404)
        before = models.list_rules()
        for changes in ({"response_headers": "invalid"}, {"status_code": 999}, {"method": "BAD"}):
            result = self.edit(self.ids[200], **changes)
            self.assertEqual(result.headers["HX-Reswap"], "none")
        self.assertEqual(models.list_rules(), before)

    def test_card_explanations_are_optional_escaped_tips_not_middle_rows(self):
        models.create_rule("[calendar] Plain", "*/plain", "GET", 200, "{}", "", enabled=False)
        html = self.client.get("/rules").text
        self.assertEqual(html.count('class="icon-btn rule-tip"'), 2)
        self.assertEqual(html.count('data-popover-value="CaseListItem-My：auth = ME，无 pointer · 概览与列表共享状态"'), 2)
        self.assertNotIn('<div class="muted">CaseListItem-My', html)
        self.assertNotIn("rule-actions", html)
        card = routes._group_rules_by_domain(models.list_rules())[0]["entities"][0]
        card["selector_label"] = '"><img src=x onerror=alert(1)>'
        rendered = routes.render("_entity_card.html", e=card).body.decode()
        self.assertIn("&lt;img", rendered)
        self.assertNotIn("<img", rendered)

    def test_lazy_capture_and_failed_edit_are_transactional(self):
        rule_id = self.ids[200]
        with db.connect() as conn:
            conn.execute("UPDATE rules SET default_config='',state_key='' WHERE id=?", (rule_id,))
            conn.execute(
                "CREATE TRIGGER reject_config_update BEFORE UPDATE OF response_body ON rules "
                "WHEN NEW.response_body='reject' "
                "BEGIN SELECT RAISE(ABORT, 'test rejection'); END"
            )
        current = models.get_rule(rule_id)
        config = {field: getattr(current, field) for field in models.CONFIG_FIELDS}
        config["response_body"] = "reject"
        with self.assertRaises(sqlite3.IntegrityError):
            models.update_rule(rule_id, **config, enabled=current.enabled)
        self.assertEqual(models.get_rule(rule_id), current)
        self.edit(rule_id, response_body="saved")
        captured = models.get_rule(rule_id)
        self.assertEqual(json.loads(captured.default_config)["response_body"], current.response_body)
        self.assertEqual(captured.state_key, "nodata")

    def test_case_migration_created_rules_receive_initial_defaults(self):
        from app.case_rules import LEGACY_PATTERN

        with db.connect() as conn:
            conn.execute("DELETE FROM rules")
        for status in (200, 403, 500):
            models.create_rule("[case] CaseListItem", LEGACY_PATTERN, "GET", status,
                               "{}", '{"value":[]}', enabled=False)
        with db.connect() as conn:
            db.migrate_rules(conn)
            db.migrate_rule_defaults(conn)
        self.assertEqual(len(models.list_rules()), 18)
        for rule in models.list_rules():
            self.assertTrue(rule.default_config)
            self.assertTrue(rule.state_key)

    def test_system_detail_groups_only_confirmed_entities_without_mutating_rules(self):
        for entity in ("CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle", "BundleSystem"):
            for status in (200, 403, 500):
                models.create_rule(
                    f"[system] {entity}", f"*/odata/mobile/{entity}*", "GET",
                    status, "{}", '{"value":[]}', enabled=False,
                )
        before = models.list_rules()
        groups = routes._group_rules_by_domain(before)
        system = next(group for group in groups if group["key"] == "system")
        pages = {page["key"]: page["entities"] for page in system["pages"]}
        self.assertEqual(set(pages), {"detail", "bundle-detail"})
        self.assertEqual({card["name"] for card in pages["detail"]},
                         {"CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle"})
        self.assertEqual({card["name"] for card in pages["bundle-detail"]}, {"BundleSystem"})
        self.assertEqual((system["total"], system["enabled_count"]), (4, 0))
        html = self.client.get("/rules").text
        self.assertIn('id="page-system-detail"', html)
        self.assertNotIn('id="page-system-overview"', html)
        self.assertNotIn('id="page-system-list"', html)
        self.assertIn("5 个接口 · 启用 0", html)
        self.assertNotIn("rule-actions", html)
        self.assertNotIn(">查看详情</button>", html)
        self.assertEqual(models.list_rules(), before)
        for entity in ("CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle"):
            rule = next(r for r in before if r.name == f"[system] {entity}" and r.status_code == 200)
            for suffix in ("('synthetic-system-a')", "('synthetic-system-b')", "?$filter=key+eq+%27a%27&$top=1", "?$top=1&$filter=key%20eq%20%27b%27"):
                self.assertTrue(request_matches(
                    rule, f"https://example.test/backend/odata/mobile/{entity}{suffix}", "GET",
                ))
            self.assertFalse(request_matches(
                rule, f"https://example.test/backend/odata/mobile/{entity}", "POST",
            ))
        self.assertEqual(routes._card_pages({
            "domain": "system", "pattern": "*/odata/mobile/CACCloudAvaStatus('synthetic-key')*",
        }), ("detail",))

    def test_calendar_list_uses_exact_service_paths_and_keeps_unknowns_unclassified(self):
        endpoints = (
            ("TimelineEvents", "calendar"), ("EventType", "mobile"), ("CalendarSettings", "mobile"),
        )
        for entity, service in endpoints:
            for status in (200, 403, 500):
                models.create_rule(f"[calendar] {entity}", f"*/odata/{service}/{entity}*", "GET",
                                   status, "{}", '{"value":[]}', enabled=False)
        for entity in ("FocusedCalendarEvent", "TimelineEvents"):
            models.create_rule(f"[calendar] {entity}", f"*/odata/mobile/{entity}*", "GET",
                               200, "{}", "", enabled=False)
        before = models.list_rules()
        calendar = next(g for g in routes._group_rules_by_domain(before) if g["key"] == "calendar")
        pages = {p["key"]: p["entities"] for p in calendar["pages"]}
        self.assertEqual(set(pages), {"list", "unclassified"})
        self.assertEqual({e["name"] for e in pages["list"]},
                         {"TimelineEvents", "EventType", "CalendarSettings"})
        self.assertEqual({e["pattern"] for e in pages["unclassified"]},
                         {"*/odata/mobile/FocusedCalendarEvent*", "*/odata/mobile/TimelineEvents*"})
        self.assertEqual((calendar["total"], calendar["enabled_count"]), (5, 0))
        html = self.client.get("/rules").text
        self.assertIn(">calendar list</h4>", html)
        self.assertNotIn(">calendar detail</h4>", html)
        self.assertIn("6 个接口 · 启用 0", html)
        self.assertNotIn("rule-actions", html)
        self.assertEqual(models.list_rules(), before)
        correct = next(r for r in before if r.url_pattern == "*/odata/calendar/TimelineEvents*")
        self.assertTrue(request_matches(correct, "https://example.test/backend/odata/calendar/TimelineEvents?$top=1", "GET"))
        self.assertFalse(request_matches(correct, "https://example.test/backend/odata/mobile/TimelineEvents?$top=1", "GET"))
        self.assertEqual(routes._card_pages({
            "domain": "calendar", "pattern": "*/odata/mobile/EventTypes*", "request_match": "",
        }), ("unclassified",))


if __name__ == "__main__":
    unittest.main()
