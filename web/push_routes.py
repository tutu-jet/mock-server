"""UI and local-only endpoint for test pushes."""
import asyncio
import ipaddress
import json
import os
import sqlite3
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from app import push, push_history
from web.routes import render

router = APIRouter()
EXAMPLE = json.dumps({"message": {
    "token": "<设备 FCM Token>",
    "notification": {"title": "案例通知测试", "body": "点击查看案例详情"},
    "data": {"source": "Incident", "sourceId": "<真实案例 pointer>"},
}}, ensure_ascii=False, indent=2)


def require_local_request(request: Request, require_origin: bool = True):
    # The existing UI is exposed to LAN devices; sending credentials stay local.
    try:
        local = request.client and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "请在运行 mock-server 的电脑上，通过 localhost 打开发通知页面。")
    origin = request.headers.get("origin")
    if not origin and require_origin:
        raise HTTPException(403, "发送请求缺少 Origin，请通过本地发通知页面操作。")
    if not origin:
        return
    parsed = urlsplit(origin)
    if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
        raise HTTPException(403, "不允许跨站发送通知。")


@router.get("/push")
def push_page():
    try:
        project = push.project_id()
    except push.PushError:
        project = "FCM_PROJECT_ID 配置无效"
    credentials_set = bool(os.environ.get("FCM_ACCESS_TOKEN") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    return render("push.html", active_tab="push", project=project, example=EXAMPLE, credentials_set=credentials_set)


@router.get("/push/history")
def history_list(request: Request):
    require_local_request(request, require_origin=False)
    return {"entries": push_history.list_entries(), "limit": push_history.LIMIT}


@router.get("/push/history/{entry_id}")
def history_detail(request: Request, entry_id: int):
    require_local_request(request, require_origin=False)
    entry = push_history.get_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "记录不存在或已超出保留范围。")
    return entry


@router.post("/push/send")
async def send_push(request: Request, payload: str = Form(...)):
    require_local_request(request)
    entry_id = None
    try:
        parsed = push.parse_payload(payload)
        project = push.project_id()
        try:
            entry_id = await asyncio.to_thread(push_history.start, parsed, project)
        except (sqlite3.Error, OSError):
            return JSONResponse({"ok": False, "error": "无法保存发送历史，本次未发送；请检查 data 目录权限。"}, status_code=503)
        result = await asyncio.to_thread(push.send_notification, parsed)
    except push.PushError as error:
        history_saved = await save_outcome(entry_id, error.outcome, str(error))
        return JSONResponse({"ok": False, "error": str(error), "history_id": entry_id, "history_saved": history_saved, "outcome": error.outcome}, status_code=error.status_code)
    history_saved = await save_outcome(entry_id, "accepted", result["name"])
    return {"ok": True, **result, "history_id": entry_id, "history_saved": history_saved}


async def save_outcome(entry_id, status, result) -> bool:
    if entry_id is None:
        return False
    try:
        await asyncio.to_thread(push_history.finish, entry_id, status, result)
        return True
    except (sqlite3.Error, OSError):
        # FCM may already have accepted the message; never report this as unsent.
        return False
