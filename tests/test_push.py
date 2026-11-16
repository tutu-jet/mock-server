import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import push, push_history
from web.push_routes import router


def payload():
    return {"message": {"token": "device-token", "notification": {"title": "测试"},
                        "data": {"source": "Incident", "sourceId": "case-pointer"}}}


class PushTest(unittest.TestCase):
    def test_preserves_arbitrary_platform_options_and_data(self):
        value = payload()
        value["message"]["android"] = {"priority": "high"}
        value["message"]["data"]["custom"] = "value"
        self.assertEqual(push.parse_payload(json.dumps(value)), value)

    def test_rejects_invalid_payload_before_sending(self):
        values = ["invalid", "[]", "{}", '{"message": []}', '{"message": {}, "validate_only": true}']
        for token in ("", "<设备 FCM Token>", " token", "token token", None):
            value = payload()
            value["message"]["token"] = token
            values.append(json.dumps(value))
        for key, value in (("topic", "all"), ("condition", "all"), ("data", {"count": 1}),
                           ("notification", {}), ("notification", {"title": 123})):
            item = payload()
            item["message"][key] = value
            values.append(json.dumps(item))
        values.append(" " * 65537)
        for text in values:
            with self.subTest(text=text[:80]), self.assertRaises(push.PushError):
                push.parse_payload(text)

    def test_invalid_project_cannot_change_destination(self):
        with patch.dict("os.environ", {"FCM_PROJECT_ID": "../../evil"}), self.assertRaises(push.PushError):
            push.project_id()

    def test_environment_access_token_supported(self):
        with patch.dict("os.environ", {"FCM_ACCESS_TOKEN": "temporary-token"}):
            self.assertEqual(push.access_token(), "temporary-token")

    def send_response(self, response):
        client = MagicMock()
        client.post.return_value = response
        context = MagicMock()
        context.__enter__.return_value = client
        return client, context

    def test_success_uses_fixed_endpoint_and_preserves_payload(self):
        value = payload()
        client, context = self.send_response(httpx.Response(200, json={"name": "projects/test/messages/123"}))
        with patch.object(push, "access_token", return_value="secret"), patch("httpx.Client", return_value=context) as factory:
            self.assertEqual(push.send_notification(value)["name"], "projects/test/messages/123")
        factory.assert_called_once_with(timeout=30, trust_env=False)
        client.post.assert_called_once_with(
            "https://fcm.googleapis.com/v1/projects/sap-for-me-android-internal/messages:send",
            headers={"Authorization": "Bearer secret"}, json=value)

    def test_fcm_failure_redacts_tokens_and_does_not_retry(self):
        client, context = self.send_response(httpx.Response(403, json={"error": {"message": "secret device-token"}}))
        with patch.object(push, "access_token", return_value="secret"), patch("httpx.Client", return_value=context):
            with self.assertRaises(push.PushError) as error:
                push.send_notification(payload())
        self.assertIn("403", str(error.exception))
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("device-token", str(error.exception))
        client.post.assert_called_once()

    def test_non_json_error_and_malformed_success(self):
        for response in (httpx.Response(500, text="failure"), httpx.Response(200, json={})):
            _, context = self.send_response(response)
            with patch.object(push, "access_token", return_value="secret"), patch("httpx.Client", return_value=context):
                with self.assertRaises(push.PushError):
                    push.send_notification(payload())

    def test_timeout_reports_uncertain_delivery(self):
        client, context = self.send_response(None)
        client.post.side_effect = httpx.ReadTimeout("timeout")
        with patch.object(push, "access_token", return_value="secret"), patch("httpx.Client", return_value=context):
            with self.assertRaises(push.PushError) as error:
                push.send_notification(payload())
        self.assertIn("结果未知", str(error.exception))
        client.post.assert_called_once()


class PushRouteTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher = patch.object(push_history, "DB_PATH", Path(directory.name) / "push.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(router)
        self.app = app
        self.client = TestClient(app, base_url="http://localhost:9088", client=("127.0.0.1", 1234))

    def test_page_has_editor_navigation_and_configuration_help(self):
        response = self.client.get("/push")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/push"', response.text)
        self.assertIn('id="push-json"', response.text)
        self.assertIn("GOOGLE_APPLICATION_CREDENTIALS", response.text)
        self.assertIn("sourceId", response.text)

    def test_local_send_returns_message_id(self):
        with patch.object(push, "send_notification", return_value={"name": "message-id", "project": "test"}) as send:
            response = self.client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": "http://localhost:9088"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "message-id")
        send.assert_called_once_with(payload())

    def test_rejects_lan_cross_origin_and_dns_rebinding(self):
        clients = [
            (self.client, "https://evil.test"),
            (self.client, None),
            (TestClient(self.app, base_url="http://evil.test", client=("127.0.0.1", 1234)), "http://evil.test"),
            (TestClient(self.app, base_url="http://localhost:9088", client=("192.168.1.10", 1234)), "http://localhost:9088"),
        ]
        with patch.object(push, "send_notification") as send:
            for client, origin in clients:
                response = client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": origin} if origin else {})
                self.assertEqual(response.status_code, 403)
            send.assert_not_called()

    def test_validation_and_credentials_errors_display_without_sending(self):
        with patch.object(push, "send_notification") as send:
            response = self.client.post("/push/send", data={"payload": "bad"}, headers={"Origin": "http://localhost:9088"})
            self.assertEqual(response.status_code, 400)
            send.assert_not_called()
        with patch.object(push, "send_notification", side_effect=push.PushError("缺少凭据", 503)):
            response = self.client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": "http://localhost:9088"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"], "缺少凭据")

    def test_history_preserves_payload_but_list_masks_token(self):
        value = payload()
        with patch.object(push, "send_notification", return_value={"name": "message-id", "project": "test"}):
            self.client.post("/push/send", data={"payload": json.dumps(value)}, headers={"Origin": "http://localhost:9088"})
        response = self.client.get("/push/history")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("device-token", response.text)
        entry = response.json()["entries"][0]
        self.assertEqual(entry["status"], "accepted")
        self.assertEqual(self.client.get('/push/history/' + str(entry["id"])).json()["payload"], value)
        self.assertEqual(self.client.get("/push/history/9999").status_code, 404)

    def test_history_rejects_nonlocal_reads(self):
        client = TestClient(self.app, base_url="http://localhost:9088", client=("192.168.1.10", 1234))
        for url in ("/push/history", "/push/history/1"):
            self.assertEqual(client.get(url).status_code, 403)

    def test_invalid_json_not_recorded_and_timeout_recorded_as_unknown(self):
        self.client.post("/push/send", data={"payload": "invalid"}, headers={"Origin": "http://localhost:9088"})
        self.assertEqual(self.client.get("/push/history").json()["entries"], [])
        with patch.object(push, "send_notification", side_effect=push.PushError("timeout", 502, "unknown")):
            response = self.client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": "http://localhost:9088"})
        self.assertEqual(response.json()["outcome"], "unknown")
        self.assertEqual(self.client.get("/push/history").json()["entries"][0]["status"], "unknown")

    def test_cannot_send_when_history_start_fails(self):
        with patch.object(push_history, "start", side_effect=OSError("readonly")), patch.object(push, "send_notification") as send:
            response = self.client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": "http://localhost:9088"})
            self.assertEqual(response.status_code, 503)
            send.assert_not_called()

    def test_history_write_failure_does_not_report_accepted_message_as_failed(self):
        with patch.object(push_history, "finish", side_effect=OSError("readonly")), patch.object(push, "send_notification", return_value={"name": "message-id", "project": "test"}):
            response = self.client.post("/push/send", data={"payload": json.dumps(payload())}, headers={"Origin": "http://localhost:9088"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(response.json()["history_saved"])


if __name__ == "__main__":
    unittest.main()
