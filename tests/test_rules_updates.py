import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from app import db, models
from app.matcher import Matcher
from web import routes


def make_request(htmx=True, current_url="http://localhost/rules"):
    headers = []
    if htmx:
        headers = [
            (b"hx-request", b"true"),
            (b"hx-current-url", current_url.encode()),
        ]
    return Request({"type": "http", "headers": headers})


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

    def test_state_changes_are_exclusive_and_update_matcher(self):
        pattern = "*/FocusedCalendarEvent*"
        for state, status in (("nodata", 200), ("noauth", 403), ("error", 500), ("off", None)):
            with self.subTest(state=state):
                response = routes.rule_set_entity_state(make_request(), pattern, state)
                self.assertEqual(response.status_code, 303)
                enabled = [r for r in models.list_rules() if r.enabled]
                self.assertEqual([r.status_code for r in enabled], [] if status is None else [status])
                match = routes.matcher.match("https://example.com/FocusedCalendarEvent", "GET")
                self.assertEqual(match.status_code if match else None, status)

    def test_all_updates_preserve_filters_for_htmx(self):
        query = "q=Calendar&method=GET&enabled_only=on"
        request = make_request(current_url=f"http://localhost/rules?{query}")
        responses = [
            routes.rule_set_entity_state(request, "*/FocusedCalendarEvent*", "nodata"),
            routes.rules_master_toggle(request),
            routes.rules_domain_disable("calendar", request),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], f"/rules?{query}")
        self.assertFalse(routes.matcher.master_enabled)
        self.assertFalse(any(r.enabled for r in models.list_rules()))

    def test_regular_forms_keep_redirect_fallback(self):
        response = routes.rule_set_entity_state(
            make_request(htmx=False), "*/FocusedCalendarEvent*", "nodata",
        )
        self.assertEqual(response.headers["location"], "/rules")

    def test_current_url_cannot_redirect_to_an_external_host(self):
        response = routes._rules_redirect(
            make_request(current_url="https://example.com/elsewhere?q=Calendar"),
        )
        self.assertEqual(response.headers["location"], "/rules?q=Calendar")

    def test_rendered_controls_use_scoped_updates(self):
        routes.rule_set_entity_state(make_request(), "*/FocusedCalendarEvent*", "nodata")
        html = routes.rules_page().body.decode()
        self.assertEqual(html.count('id="rules-content"'), 1)
        self.assertIn('hx-select="#rules-content"', html)
        self.assertIn('hx-swap="outerHTML show:none"', html)
        self.assertIn('hx-disabled-elt="#rules-content button"', html)
        self.assertEqual(html.count('hx-post="/rules/entity/state"'), 8)
        self.assertIn('hx-post="/rules/master"', html)
        self.assertIn('hx-post="/rules/domain/calendar/disable"', html)
        self.assertIn('state-nodata', html)
        self.assertIn('2 个接口 · 启用 1', html)
        filtered = routes.rules_page(q="Focused", method="GET", enabled_only=True).body.decode()
        self.assertIn("FocusedCalendarEvent", filtered)
        self.assertNotIn('title="Cases"', filtered)
        self.assertIn('name="enabled_only" checked', filtered)


if __name__ == "__main__":
    unittest.main()
