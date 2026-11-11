import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from app.flows import FlowRecord
from web import routes


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


class UITest(unittest.TestCase):
    def assert_single_header(self, html):
        header, content = html.split("</header>", 1)
        self.assertEqual(header.count('id="page-toolbar"'), 1)
        self.assertNotIn('id="page-toolbar"', content)
        self.assertNotIn("<main", header)

    def test_help_preserves_network_addresses_and_initial_setup_steps(self):
        addresses = ["192.0.2.10", "192.0.2.11"]
        with patch.object(routes, "_detect_lan_ips", return_value=addresses):
            html = routes.help_page().body.decode()
        self.assert_single_header(html)
        elements = Elements(html).items
        links = [attrs["href"] for tag, attrs in elements if tag == "a"]
        for address in addresses:
            self.assertIn(f"http://{address}:9088", links)
        details = [attrs for tag, attrs in elements if tag == "details"]
        steps = {attrs["id"]: attrs for attrs in details if "id" in attrs}
        self.assertIn("open", steps["help-network"])
        for step in ("help-proxy", "help-certificate", "help-app-https"):
            self.assertNotIn("open", steps[step])
        self.assertIn("network_security_config.xml", html)
        self.assertIn("mitmproxy-ca-cert.cer", html)
        self.assertIn("/system/etc/security/cacerts/", html)

    def test_flow_actions_are_keyboard_accessible_and_escape_the_url(self):
        record = FlowRecord(
            id=42, ts=0, method="GET",
            url="https://example.test/items?name=<demo>&other=1",
            status=200, duration_ms=15, rule_id=7, rule_name="Example rule",
        )
        with patch.object(routes, "_load_blocked_hosts", return_value=("", [])), \
                patch.object(routes.flows.buffer, "snapshot", return_value=[record]):
            html = routes.flows_page().body.decode()
        self.assert_single_header(html)
        self.assertIn('popovertarget="host-filter-popover"', html)
        self.assertIn('hx-post="/flows/blocked-hosts"', html)
        buttons = [attrs for tag, attrs in Elements(html).items if tag == "button"]
        for endpoint in ("/flows/42", "/flows/42/save-as-rule/edit"):
            button = next(attrs for attrs in buttons if attrs.get("hx-get") == endpoint)
            self.assertEqual(button["type"], "button")
            self.assertIn(record.url, button["aria-label"])
            self.assertEqual(button["hx-trigger"], "click consume")
        self.assertIn('sse-swap="flow"', html)
        self.assertIn("name=&lt;demo&gt;&amp;other=1", html)
        self.assertNotIn("<demo>", html)


if __name__ == "__main__":
    unittest.main()
