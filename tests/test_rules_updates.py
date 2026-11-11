import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, models
from app.matcher import Matcher
from web import routes


class RulesUpdatesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path_patch = patch.object(db, "DB_PATH", Path(directory.name) / "rules.db")
        path_patch.start()
        self.addCleanup(path_patch.stop)
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
        for domain, entity in (("calendar", "FocusedCalendarEvent"), ("case", "Cases")):
            for status in (200, 403, 500):
                models.create_rule(
                    f"[{domain}] {entity}", f"*/{entity}*", "GET",
                    status, "{}", "", enabled=False,
                )
        matcher_patch = patch.object(routes, "matcher", Matcher())
        matcher_patch.start()
        self.addCleanup(matcher_patch.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)

    def set_state(self, pattern, state, **headers):
        return self.client.post(
            "/rules/entity/state", data={"pattern": pattern, "state": state},
            headers={"HX-Request": "true", **headers}, follow_redirects=False,
        )

    def test_state_changes_are_exclusive_and_update_matcher(self):
        pattern = "*/FocusedCalendarEvent*"
        for state, status in (("nodata", 200), ("noauth", 403), ("error", 500), ("off", None)):
            with self.subTest(state=state):
                response = self.set_state(pattern, state)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("location", response.headers)
                self.assertNotIn('id="rules-content"', response.text)
                self.assertNotIn('id="page-toolbar"', response.text)
                self.assertNotIn('title="Cases"', response.text)
                enabled = [r for r in models.list_rules() if r.enabled]
                self.assertEqual([r.status_code for r in enabled], [] if status is None else [status])
                match = routes.matcher.match("https://example.com/FocusedCalendarEvent", "GET")
                self.assertEqual(match.status_code if match else None, status)

    def test_all_updates_discard_removed_filters_for_htmx(self):
        headers = {
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost/rules?q=Calendar&method=GET&enabled_only=on&domain=calendar",
        }
        response = self.set_state("*/FocusedCalendarEvent*", "nodata", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)
        response = self.client.post("/rules/master", headers=headers, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/rules")
        self.assertFalse(routes.matcher.master_enabled)
        self.assertEqual(sum(r.enabled for r in models.list_rules()), 1)

    def test_domain_bulk_disable_is_removed(self):
        self.set_state("*/FocusedCalendarEvent*", "nodata")
        before = models.list_rules()
        response = self.client.post("/rules/domain/calendar/disable")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(models.list_rules(), before)
        html = routes.rules_page().body.decode()
        self.assertNotIn("一键全关", html)
        self.assertNotIn("/rules/domain/", html)

    def test_regular_forms_keep_redirect_fallback(self):
        response = self.client.post(
            "/rules/entity/state", data={"pattern": "*/FocusedCalendarEvent*", "state": "nodata"},
            follow_redirects=False,
        )
        self.assertEqual(response.headers["location"], "/rules")

    def test_current_url_cannot_redirect_to_an_external_host(self):
        response = self.client.post(
            "/rules/master",
            headers={"HX-Request": "true", "HX-Current-URL": "https://example.com/elsewhere?q=Calendar"},
            follow_redirects=False,
        )
        self.assertEqual(response.headers["location"], "/rules")

    def test_rendered_controls_use_scoped_updates(self):
        self.set_state("*/FocusedCalendarEvent*", "nodata")
        html = routes.rules_page().body.decode()
        self.assertEqual(html.count('id="rules-content"'), 1)
        self.assertIn('hx-select="#rules-content"', html)
        self.assertIn('hx-swap="outerHTML show:none"', html)
        self.assertIn('hx-disabled-elt="#rules-content button"', html)
        self.assertEqual(html.count('hx-post="/rules/entity/state"'), 8)
        self.assertIn('hx-post="/rules/master"', html)
        self.assertNotIn('class="page-header"', html)
        self.assertNotIn("匹配命中的请求会被改写返回", html)
        toolbar = html.split('<div id="page-toolbar" class="toolbar rules-toolbar"', 1)[1]
        self.assertLess(html.index('id="page-toolbar"'), html.index("<main"))
        self.assertIn('hx-select-oob="#page-toolbar:outerHTML"', html)
        self.assertIn('hx-post="/rules/master"', toolbar)
        self.assertIn('aria-label="关闭整体 Mock"', toolbar)
        self.assertIn('btn btn-ghost master-toggle', toolbar)
        self.assertNotIn('class="master-bar', html)
        self.assertIn('<details class="domain-group" id="domain-calendar">', html)
        self.assertIn('state-nodata', html)
        self.assertIn('2 个接口 · 启用 1', html)

    def test_old_backend_context_keeps_safe_list_target_and_unique_card_ids(self):
        context = routes._rules_context()
        context.pop("entity_state_partial")
        html = routes.env.get_template("rules.html").render(**context)
        self.assertNotIn('hx-target="closest .entity-card"', html)
        self.assertNotIn('id="entity--', html)
        self.assertIn('hx-select="#rules-content"', html)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))

    def test_rules_page_shows_all_interfaces_without_filter_controls(self):
        response = self.client.get(
            "/rules?q=missing&method=POST&enabled_only=true&domain=calendar",
        )
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("FocusedCalendarEvent", html)
        self.assertIn("Cases", html)
        header = html.split("</header>", 1)[0]
        for name in ("q", "method", "enabled_only", "domain"):
            self.assertNotIn(f'name="{name}"', header)
        self.assertNotIn("category-filter-popover", html)
        self.assertNotIn("筛选", html)
        self.assertNotIn("data-filtered", html)

    def test_domain_headings_use_existing_english_labels_only(self):
        labels = {
            "case": "Case", "servicerequest": "Service Request", "system": "System",
            "calendar": "Calendar", "consumption": "Consumption",
            "provisioning": "Provisioning", "notification": "Notification", "other": "Other",
        }
        self.assertEqual(routes.DOMAIN_LABELS, labels)
        for domain in labels:
            models.create_rule(f"[{domain}] 示例", f"*/{domain}", "GET", 200, "{}", "", enabled=False)
        before = models.list_rules()
        html = routes.rules_page().body.decode()
        for label in labels.values():
            self.assertIn(f'<h3 class="domain-title">{label}</h3>', html)
        self.assertEqual(models.list_rules(), before)


if __name__ == "__main__":
    unittest.main()
