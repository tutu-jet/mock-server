"""Send device or topic test notifications through FCM HTTP v1."""
from __future__ import annotations

import json
import os
import re

DEFAULT_PROJECT = "sap-for-me-android-internal"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class PushError(Exception):
    def __init__(self, message: str, status_code: int = 400, outcome: str = "failed"):
        super().__init__(message)
        self.status_code = status_code
        self.outcome = outcome


def project_id() -> str:
    project = os.environ.get("FCM_PROJECT_ID", DEFAULT_PROJECT).strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise PushError("FCM_PROJECT_ID 配置无效。")
    return project


def parse_payload(text: str) -> dict:
    if len(text.encode("utf-8")) > 65536:
        raise PushError("通知 JSON 不能超过 64 KiB。")
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise PushError("JSON 格式错误，请检查引号、逗号和括号。") from error
    if not isinstance(payload, dict) or set(payload) != {"message"}:
        raise PushError('最外层必须仅包含 "message" 对象。')
    message = payload["message"]
    if not isinstance(message, dict):
        raise PushError("message 必须是对象。")
    if "condition" in message or sum(key in message for key in ("token", "topic")) != 1:
        raise PushError("发送目标必须且只能填写 token 或 topic，不支持 condition。")
    if "token" in message:
        token = message["token"]
        if not isinstance(token, str) or not token.strip() or "<" in token or ">" in token or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
            raise PushError("请填写真实设备 FCM Token，不能包含占位符或空白。")
    else:
        topic = message["topic"]
        if not isinstance(topic, str) or not re.fullmatch(r"[a-zA-Z0-9_.~%\-]{1,900}", topic):
            raise PushError("请填写有效 Topic（1–900 个字母、数字或 -_.~%），不要填写 /topics/ 前缀。")
    notification = message.get("notification")
    if not isinstance(notification, dict) or not any(notification.get(key) for key in ("title", "body")):
        raise PushError("请填写 notification.title 或 notification.body，供 Android 展示通知。")
    if any(key in notification and not isinstance(notification[key], str) for key in ("title", "body", "image")):
        raise PushError("notification 的 title、body、image 必须是字符串。")
    data = message.get("data", {})
    if not isinstance(data, dict) or any(not isinstance(value, str) for value in data.values()):
        raise PushError("data 必须是对象，所有值必须是字符串。")
    return payload


def access_token() -> str:
    token = os.environ.get("FCM_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as error:
        raise PushError("请在项目目录执行 uv sync 安装依赖，或配置 FCM_ACCESS_TOKEN。", 503) from error
    try:
        credentials, _ = google.auth.default(scopes=[FCM_SCOPE])
        transport = Request()
        def auth_request(*args, **kwargs):
            kwargs.setdefault("timeout", 30)
            return transport(*args, **kwargs)
        credentials.refresh(auth_request)
    except Exception as error:
        raise PushError("无法取得 Google 发送凭据。请在服务器配置 GOOGLE_APPLICATION_CREDENTIALS，或使用有效的 FCM_ACCESS_TOKEN。", 503) from error
    if not credentials.token:
        raise PushError("Google 凭据未返回访问令牌。", 503)
    return credentials.token


def send_notification(payload: dict) -> dict:
    """No retries: an uncertain response must not duplicate a notification."""
    project = project_id()
    token = access_token()
    try:
        import httpx
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.post(
                f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
    except ImportError as error:
        raise PushError("请在项目目录执行 uv sync 安装 httpx 依赖。", 503) from error
    except httpx.HTTPError as error:
        raise PushError("FCM 网络请求失败或超时，发送结果未知；请先检查设备是否收到，再决定是否重发。", 502, "unknown") from error
    if not response.is_success:
        detail = ""
        try:
            error = response.json().get("error", {})
            detail = error.get("message", "") if isinstance(error, dict) else ""
        except (ValueError, AttributeError):
            pass
        if not isinstance(detail, str):
            detail = ""
        detail = detail.replace(token, "[redacted]")
        device_token = payload["message"].get("token")
        if device_token:
            detail = detail.replace(device_token, "[device token]")
        detail = detail[:1500]
        hint = {
            401: "Google 访问令牌无效或已过期。",
            403: "发送身份缺少 cloudmessaging.messages.create 权限，或发送目标与项目不匹配。",
            404: "项目或发送目标不存在；设备 Token 可能已失效。",
        }.get(response.status_code, "请检查消息内容、发送目标和 Firebase 项目。")
        raise PushError(f"FCM HTTP {response.status_code}：{hint} {detail}".strip(), 502)
    try:
        name = response.json()["name"]
        if not isinstance(name, str) or not name:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as error:
        raise PushError("FCM 返回了无法识别的成功响应，请先检查设备是否收到。", 502, "unknown") from error
    return {"name": name, "project": project}
