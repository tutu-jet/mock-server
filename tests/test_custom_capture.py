import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mitmproxy import http

from app import db, flows, models, proxy_addon
from app.custom_capture import CaptureManager, CAPTURE_TIMEOUT, MAX_CAPTURES, MAX_CAPTURE_BYTES
from app.matcher import Matcher
from web import routes


URL = "https://example.test/backend/odata/mobile/CaseListItem?%24filter=auth+eq+%27ME%27"


class CustomCaptureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for attribute, filename, schema in (
            ("DB_PATH", "rules.db", db.SCHEMA),
            ("FLOWS_DB_PATH", "flows.db", db.FLOWS_SCHEMA),
        ):
            patcher = patch.object(db, attribute, Path(directory.name) / filename)
            patcher.start()
            self.addCleanup(patcher.stop)
            with (db.connect() if attribute == "DB_PATH" else db.connect_flows()) as conn:
                conn.executescript(schema)
        self.ids = [
            models.create_rule(
                f"[case] CaseListItem-My · {status}", "*/odata/mobile/CaseListItem*",
                "GET", status, '{"Content-Type":"application/json"}', '{"value":[]}',
                enabled=status == 403, request_match="case-my",
            )
            for status in (200, 403, 500)
        ]
        self.source = models.get_rule(self.ids[0])
        self.captures = CaptureManager()
        self.matcher = Matcher()
        for module, attribute, value in (
            (routes, "captures", self.captures), (proxy_addon, "captures", self.captures),
            (routes, "matcher", self.matcher), (proxy_addon, "matcher", self.matcher),
            (flows, "buffer", flows.FlowBuffer()),
        ):
            patcher = patch.object(module, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addon = proxy_addon.MockAddon()

    def flow(self, url=URL, method="GET"):
        return SimpleNamespace(
            request=http.Request.make(method, url), response=None, metadata={}, error=None,
        )

    def start(self, rule_id=None):
        response = self.client.post(f"/rules/{rule_id or self.source.id}/custom/capture")
        self.assertEqual(response.status_code, 200)
        marker = 'data-capture-token="'
        return response.text.split(marker, 1)[1].split('"', 1)[0]

    def poll(self, token):
        return self.client.get(f"/rules/custom/capture/{token}")

    def receive(self, token, body=b'{"value":[1]}', headers=None, status=201):
        flow = self.flow()
        self.addon.request(flow)
        self.assertEqual(flow.metadata["custom_capture_token"], token)
        flow.response = http.Response.make(
            status, body, headers or {"Content-Type": "application/json"},
        )
        if headers and "Content-Encoding" in headers:
            flow.response.raw_content = body
        self.addon.response(flow)
        return flow

    def test_next_matching_request_bypasses_mock_and_full_response_is_editable_before_save(self):
        before = models.list_rules()
        token = self.start()
        self.assertEqual(self.poll(token).status_code, 204)
        unrelated = self.flow(URL.replace("%27ME%27", "%27ALL%27"))
        self.addon.request(unrelated)
        self.assertNotIn("custom_capture_token", unrelated.metadata)
        body = json.dumps({"data": "x" * (flows.MAX_BODY_BYTES + 1)})
        self.receive(token, body.encode())
        self.assertEqual(models.list_rules(), before)
        later = self.flow()
        self.addon.request(later)
        self.assertNotIn("custom_capture_token", later.metadata)
        self.assertEqual(later.response.status_code, 403)
        captured = self.captures.get(token)
        self.assertEqual(captured.body, body)
        self.assertEqual(captured.status_code, 201)
        flows.buffer.clear()
        response = self.poll(token)
        self.assertIn("已填入真实响应", response.text)
        self.assertIn("x" * (flows.MAX_BODY_BYTES + 1), response.text)
        self.assertNotIn("data-capture-token", response.text)
        self.assertIsNone(self.captures.get(token))
        self.assertFalse(any(r.state_key == "custom" for r in models.list_rules()))
        self.assertTrue(models.get_rule(self.ids[1]).enabled)
        payload = {field: getattr(self.source, field) for field in models.CONFIG_FIELDS}
        payload.update(
            name="[case] CaseListItem-My · 自定义", status_code=201,
            response_headers=captured.headers, response_body=body,
        )
        self.client.post(f"/rules/{self.source.id}/custom", data=payload)
        custom = next(r for r in models.list_rules() if r.state_key == "custom")
        self.assertEqual(custom.response_body, body)
        self.assertFalse(custom.enabled)
        self.client.post("/rules/entity/state", data={
            "pattern": custom.url_pattern, "method": custom.method,
            "request_match": custom.request_match, "state": "custom",
        })
        self.assertEqual(self.matcher.match(URL, "GET").response_body, body)

    def test_capture_does_not_replace_existing_custom_until_explicit_save(self):
        custom_id = models.create_rule(
            "[case] CaseListItem-My · 自定义", self.source.url_pattern, "GET", 200, "{}",
            '{"saved":true}', enabled=False, request_match="case-my", state_key="custom",
        )
        original = models.get_rule(custom_id)
        edit = self.client.get(f"/rules/{custom_id}/edit").text
        self.assertIn(f'hx-post="/rules/{custom_id}/custom/capture"', edit)
        self.assertIn("已保存内容不会被覆盖", edit)
        token = self.start(custom_id)
        self.receive(token, b'{"new":true}')
        response = self.poll(token)
        self.assertIn(f'hx-post="/rules/{custom_id}"', response.text)
        self.assertIn("{&#34;new&#34;:true}", response.text)
        self.assertEqual(models.get_rule(custom_id), original)

    def test_cancel_timeout_and_network_failure_never_change_rule_state(self):
        before = models.list_rules()
        token = self.start()
        self.client.post(f"/rules/custom/capture/{token}/cancel")
        self.assertIsNone(self.captures.get(token))
        self.assertIsNone(self.captures.claim(URL, "GET"))
        token = self.start()
        expires = self.captures.get(token).expires
        with patch("app.custom_capture.monotonic", return_value=expires + 1):
            self.assertIsNone(self.captures.claim(URL, "GET"))
            self.assertIn("超时", self.poll(token).text)
        token = self.start()
        flow = self.flow()
        self.addon.request(flow)
        flow.error = SimpleNamespace(msg="connection refused")
        with self.assertLogs("mock-server.addon", level="WARNING"):
            self.addon.error(flow)
        self.assertIn("connection refused", self.poll(token).text)
        self.assertEqual(models.list_rules(), before)

    def test_cancel_or_timeout_inflight_ignores_late_response(self):
        before = models.list_rules()
        for cancel in (True, False):
            token = self.start()
            flow = self.flow()
            self.addon.request(flow)
            flow.response = http.Response.make(200, b"late", {"Content-Type": "text/plain"})
            if cancel:
                self.captures.cancel(token)
                self.addon.response(flow)
                self.assertIsNone(self.captures.get(token))
            else:
                expires = self.captures.get(token).expires
                with patch("app.custom_capture.monotonic", return_value=expires + 1):
                    self.addon.response(flow)
                    self.assertEqual(self.captures.get(token).state, "error")
                    self.assertIn("超时", self.poll(token).text)
        self.assertEqual(models.list_rules(), before)

    def test_binary_invalid_encoding_and_oversized_responses_are_explicit_errors(self):
        before = models.list_rules()
        for body, headers in (
            (b"\x00\x01", {"Content-Type": "application/octet-stream"}),
            (b"invalid gzip", {"Content-Type": "application/json", "Content-Encoding": "gzip"}),
            (b"x" * (MAX_CAPTURE_BYTES + 1), {"Content-Type": "text/plain"}),
            (
                b"\xe9" * (MAX_CAPTURE_BYTES // 2 + 1),
                {"Content-Type": "text/plain; charset=iso-8859-1"},
            ),
        ):
            token = self.start()
            with self.assertLogs("mock-server.addon", level="WARNING"):
                self.receive(token, body, headers)
            capture = self.captures.get(token)
            self.assertEqual(capture.state, "error")
            self.assertEqual(capture.body, "")
            response = self.poll(token)
            self.assertIn('role="alert"', response.text)
            self.assertNotIn("已填入真实响应", response.text)
        token = self.start()
        self.receive(token, b"x" * MAX_CAPTURE_BYTES, {"Content-Type": "text/plain"})
        self.assertEqual(len(self.captures.get(token).body), MAX_CAPTURE_BYTES)
        self.assertEqual(models.list_rules(), before)

    def test_compressed_non_utf8_response_is_decoded_and_headers_normalized(self):
        token = self.start()
        flow = self.flow()
        self.addon.request(flow)
        flow.response = http.Response.make(200, b"caf\xe9", {
            "Content-Type": "text/plain; charset=iso-8859-1",
            "Connection": "X-Hop", "X-Hop": "remove", "X-Application": "keep",
        })
        flow.response.encode("gzip")
        self.addon.response(flow)
        result = self.captures.get(token)
        self.assertEqual(result.body, "café")
        headers = {k.lower(): v for k, v in json.loads(result.headers).items()}
        self.assertEqual(headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(headers["x-application"], "keep")
        for key in ("content-length", "content-encoding", "connection", "x-hop"):
            self.assertNotIn(key, headers)

    def test_source_change_and_missing_session_stop_polling(self):
        token = self.start()
        self.receive(token)
        models.delete_rule(self.source.id)
        response = self.poll(token)
        self.assertIn("已删除", response.text)
        self.assertNotIn('hx-trigger="every', response.text)
        response = self.poll("missing")
        self.assertIn("已取消或过期", response.text)
        self.assertNotIn('hx-trigger="every', response.text)

    def test_claim_is_one_shot_and_registry_is_bounded(self):
        with patch("app.custom_capture.monotonic", return_value=100):
            capture = self.captures.start(self.source)
            self.assertEqual(capture.expires, 100 + CAPTURE_TIMEOUT)
            with self.assertRaisesRegex(ValueError, "已有抓取"):
                self.captures.start(self.source)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self.captures.claim(URL, "GET"), range(16)))
            self.assertEqual(results.count(capture.token), 1)
            for index in range(MAX_CAPTURES - 1):
                source = models.get_rule(self.source.id)
                source.url_pattern = f"*/other-{index}"
                self.captures.start(source)
            extra = models.get_rule(self.ids[1])
            extra.url_pattern = "*/over-limit"
            with self.assertRaisesRegex(ValueError, "上限"):
                self.captures.start(extra)
        with patch("app.custom_capture.monotonic", return_value=100 + CAPTURE_TIMEOUT + 1):
            self.assertEqual(self.captures.get(capture.token).state, "error")
        with patch("app.custom_capture.monotonic", return_value=1000):
            self.assertIsNone(self.captures.get(capture.token))
            self.captures.start(self.source)
