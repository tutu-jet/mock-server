import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, models
from app.case_rules import LEGACY_PATTERN, MIGRATION_KEY
from app.matcher import Matcher
from web import routes


def case_url(expression, *, spaces_as_percent=False):
    kwargs = {"quote_via": quote} if spaces_as_percent else {}
    return "https://example.test/backend/odata/mobile/CaseListItem?" + urlencode(
        {"$top": "20", "$filter": expression, "$skip": "0"}, **kwargs,
    )


def seed_legacy():
    for status in (403, 500, 200):
        models.create_rule(
            f"[case] CaseListItem · {status}", LEGACY_PATTERN, "GET", status,
            '{"Content-Type":"application/json"}', '{"value":[]}' if status == 200 else "",
            enabled=False,
        )


class CasePagesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path_patch = patch.object(db, "DB_PATH", Path(temp.name) / "rules.db")
        path_patch.start()
        self.addCleanup(path_patch.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        seed_legacy()
        for name, pattern in (
            ("[case] IncidentHeader", "*/odata/mobile/IncidentHeader*"),
            ("[case] CaseDetails", "*/odata/mobile/CaseDetails*"),
            ("[calendar] FocusedCalendarEvent", "*/FocusedCalendarEvent*"),
        ):
            for status in (403, 500, 200):
                models.create_rule(name, pattern, "GET", status, "{}", "", enabled=False)
        with db.connect() as conn:
            self.assertTrue(db.migrate_rules(conn))
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def select(self, scope, state="nodata"):
        return self.client.post(
            "/rules/entity/state",
            data={"pattern": LEGACY_PATTERN, "method": "GET",
                  "request_match": scope, "state": state},
            headers={"HX-Request": "true", "HX-Current-URL": "http://localhost/rules"},
        )

    def test_decoded_query_order_status_and_pointer_are_not_hardcoded(self):
        for scope in ("case-my", "case-all", "case-detail"):
            self.assertEqual(self.select(scope).status_code, 200)
        cases = (
            ("statusTxtInput eq 'CLOSED' and auth eq 'ME' and source eq 'ALL'", "case-my"),
            ("source eq 'OTHER' and auth eq 'ALL' and statusTxtInput eq 'OPEN'", "case-all"),
            ("pointer eq 'arbitrary-case-1'", "case-detail"),
            ("pointer eq 'arbitrary-case-2' and requestUser eq 'someone'", "case-detail"),
            ("pointer eq 'case-3' and auth eq 'ME'", "case-detail"),
        )
        for expression, scope in cases:
            for encoded_spaces in (True, False):
                with self.subTest(expression=expression, percent=encoded_spaces):
                    match = routes.matcher.match(
                        case_url(expression, spaces_as_percent=encoded_spaces), "GET",
                    )
                    self.assertIsNotNone(match)
                    self.assertEqual(match.request_match, scope)
        reordered = "https://example.test/backend/odata/mobile/CaseListItem?" + urlencode(
            {"$filter": "auth eq 'ME'", "$skip": "50", "$top": "10"},
        )
        self.assertEqual(routes.matcher.match(reordered, "GET").request_match, "case-my")

    def test_ambiguous_or_unrelated_requests_do_not_match(self):
        for scope in ("case-my", "case-all", "case-detail"):
            self.select(scope)
        for expression in (
            "auth eq 'SOMEONE'", "pointer eq ''", "source eq 'ALL'",
            "auth eq 'ME' or auth eq 'ALL'", "auth eq 'ME' and auth eq 'ALL'",
            "comment eq 'auth eq ''ME'''", "auth eq 'ME' and pointer eq ''",
        ):
            self.assertIsNone(routes.matcher.match(case_url(expression), "GET"), expression)
        duplicate = case_url("auth eq 'ME'") + "&" + urlencode({"$filter": "auth eq 'ALL'"})
        self.assertIsNone(routes.matcher.match(duplicate, "GET"))
        self.assertIsNone(routes.matcher.match(case_url("auth eq 'ME'"), "POST"))
        self.assertIsNone(routes.matcher.match(
            case_url("auth eq 'ME'").replace("/CaseListItem?", "/CaseListItemOther?"), "GET",
        ))

    def test_shared_pages_state_and_unique_counts(self):
        self.select("case-my", "error")
        groups = routes._group_rules_by_domain(models.list_rules())
        case = next(g for g in groups if g["key"] == "case")
        pages = {p["key"]: p["entities"] for p in case["pages"]}
        self.assertEqual([e["name"] for e in pages["overview"]],
                         ["CaseListItem-All", "CaseListItem-My"])
        self.assertEqual({e["name"] for e in pages["list"]},
                         {"CaseListItem-My", "CaseListItem-All", "CaseSystem"})
        self.assertEqual({e["name"] for e in pages["detail"]},
                         {"CaseListItem-Detail", "CaseCloseButtonStatus",
                          "IncidentHeader", "IncidentMessageAuth"})
        self.assertEqual({e["name"] for e in pages["unclassified"]}, {"CaseDetails"})
        my_overview = next(e for e in pages["overview"] if e["request_match"] == "case-my")
        my_list = next(e for e in pages["list"] if e["request_match"] == "case-my")
        self.assertIs(my_overview, my_list)
        self.assertEqual(my_list["active"], "error")
        self.assertEqual((case["total"], case["enabled_count"]), (8, 1))
        html = self.client.get("/rules").text
        self.assertIn("9 个接口 · 启用 1", html)
        self.assertEqual(html.count('title="CaseListItem-My"'), 2)
        self.assertEqual(html.count('id="rules-content"'), 1)
        self.assertIn('hx-swap="outerHTML show:none"', html)
        filtered = self.client.get("/rules?enabled_only=on").text
        self.assertEqual(filtered.count('title="CaseListItem-My"'), 2)
        self.assertNotIn('title="CaseSystem"', filtered)
        self.assertNotIn('id="page-case-detail"', filtered)
        self.client.post("/rules/domain/case/disable")
        self.assertFalse(any(r.enabled for r in models.list_rules()))

    def test_state_changes_are_isolated_and_keep_filter_redirect(self):
        self.select("case-my", "noauth")
        self.select("case-all", "error")
        self.select("case-detail", "nodata")
        active = {r.request_match: r.status_code for r in models.list_rules() if r.enabled}
        self.assertEqual(active, {"case-my": 403, "case-all": 500, "case-detail": 200})
        response = self.client.post(
            "/rules/entity/state",
            data={"pattern": LEGACY_PATTERN, "method": "GET",
                  "request_match": "case-my", "state": "off"},
            headers={"HX-Request": "true", "HX-Current-URL": "http://local/rules?q=Case&method=GET"},
            follow_redirects=False,
        )
        self.assertEqual(response.headers["location"], "/rules?q=Case&method=GET")
        self.assertEqual(sum(r.enabled for r in models.list_rules()), 2)
        before = [(r.id, r.enabled) for r in models.list_rules()]
        self.assertEqual(self.select("case-all", "typo").status_code, 422)
        self.assertEqual(self.select("", "nodata").status_code, 404)
        self.assertEqual(before, [(r.id, r.enabled) for r in models.list_rules()])

    def test_editing_preserves_selector_and_conflicts_are_scope_aware(self):
        my = next(r for r in models.list_rules()
                  if r.request_match == "case-my" and r.status_code == 403)
        self.select("case-all", "error")
        html = self.client.get(f"/rules/{my.id}/edit").text
        self.assertIn('value="case-my" selected', html)
        payload = dict(name=my.name, url_pattern=my.url_pattern, method=my.method,
                       status_code=my.status_code, response_headers=my.response_headers,
                       response_body=my.response_body, enabled="on")
        self.client.post(f"/rules/{my.id}", data=payload)
        updated = models.get_rule(my.id)
        self.assertEqual(updated.request_match, "case-my")
        self.assertFalse(updated.enabled)
        self.assertIsNone(routes.matcher.find_conflict(
            LEGACY_PATTERN, "GET", request_match="case-detail",
        ))
        self.assertIsNotNone(routes.matcher.find_conflict(LEGACY_PATTERN, "GET"))
        result = self.client.post("/rules", data={**payload, "request_match": "bogus"})
        self.assertIn("未知请求分类", json.loads(result.headers["HX-Trigger"])["toast"]["msg"])

    def test_detail_auxiliary_rules_and_legacy_url_matching(self):
        for entity in ("CaseCloseButtonStatus", "IncidentHeader", "IncidentMessageAuth", "CaseSystem"):
            rule = next(r for r in models.list_rules()
                        if r.name.startswith(f"[case] {entity}") and r.status_code == 200)
            models.set_rule_enabled(rule.id, True)
        routes.matcher.reload_from_db()
        for path in (
            "CaseCloseButtonStatus?" + urlencode({"$filter": "pointer eq 'new-id' and requestUser eq 'new-user'"}),
            "IncidentHeader?" + urlencode({"$filter": "pointer eq 'other-id'"}),
            "IncidentMessageAuth(action='SEND2SAP',object='POINTER',value='another-id')",
            "CaseSystem",
        ):
            self.assertIsNotNone(routes.matcher.match(
                "https://example.test/backend/odata/mobile/" + path, "GET",
            ))
        self.assertIsNone(routes.matcher.match("https://example.test/elsewhere", "GET"))

    def test_different_methods_remain_separate_cards(self):
        models.create_rule("[calendar] FocusedCalendarEvent", "*/FocusedCalendarEvent*",
                           "POST", 200, "{}", "", enabled=False)
        groups = routes._group_rules_by_domain(models.list_rules())
        calendar = next(g for g in groups if g["key"] == "calendar")
        self.assertEqual(calendar["total"], 2)
        self.assertEqual({c["method"] for c in calendar["entities"]}, {"GET", "POST"})


class CaseMigrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(temp.name) / "rules.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        seed_legacy()

    def snapshot(self):
        with db.connect() as conn:
            return [tuple(r) for r in conn.execute("SELECT * FROM rules ORDER BY id")]

    def test_migration_backs_up_preserves_active_state_and_is_idempotent(self):
        original = models.list_rules()
        active = next(r for r in original if r.status_code == 500)
        models.set_rule_enabled(active.id, True)
        with db.connect() as conn:
            self.assertTrue(db.migrate_rules(conn))
            self.assertFalse(db.migrate_rules(conn))
        migrated = models.list_rules()
        self.assertEqual(len(migrated), 18)
        self.assertEqual(sum(r.enabled for r in migrated), 3)
        self.assertEqual({r.request_match for r in migrated if r.enabled},
                         {"case-my", "case-all", "case-detail"})
        for old in original:
            new = models.get_rule(old.id)
            self.assertEqual(new.request_match, "case-my")
            self.assertEqual((new.response_headers, new.response_body, new.created_at),
                             (old.response_headers, old.response_body, old.created_at))
        backup = db.DB_PATH.with_name(db.DB_PATH.name + ".bak-case-pages-v1")
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM rules").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT sum(enabled) FROM rules").fetchone()[0], 1)

    def test_existing_rules_without_selector_column_upgrade(self):
        with db.connect() as conn:
            conn.execute("ALTER TABLE rules DROP COLUMN request_match")
            self.assertTrue(db.migrate_rules(conn))
        self.assertEqual(len(models.list_rules()), 18)

    def test_conflicting_enabled_states_abort_without_rule_changes(self):
        for rule in models.list_rules()[:2]:
            models.set_rule_enabled(rule.id, True)
        before = self.snapshot()
        with db.connect() as conn, self.assertRaisesRegex(RuntimeError, "冲突"):
            db.migrate_rules(conn)
        self.assertEqual(before, self.snapshot())

    def test_partial_write_failure_rolls_back_all_rule_changes(self):
        before = self.snapshot()
        with db.connect() as conn:
            conn.execute(
                "CREATE TRIGGER reject_case_system BEFORE INSERT ON rules "
                "WHEN NEW.name LIKE '%CaseSystem%' "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.migrate_rules(conn)
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,),
            ).fetchone())
        self.assertEqual(before, self.snapshot())

    def test_custom_rule_conflict_is_not_overwritten(self):
        models.create_rule("[case] CaseSystem custom", "*/odata/mobile/CaseSystem*",
                           "GET", 201, "{}", "custom", enabled=True)
        before = self.snapshot()
        with db.connect() as conn, self.assertRaisesRegex(RuntimeError, "冲突"):
            db.migrate_rules(conn)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
