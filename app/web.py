"""
FastAPI 应用 - Task #4 版本
路由：规则 CRUD（htmx 局部 swap）+ 冲突检测 + 写入后刷新 matcher
       + 流量列表（SSE 实时推送）
"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app import flows, models
from app.db import init_db
from app.matcher import matcher
from app.proxy_addon import MockAddon

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _toast_response(msg: str, level: str = "error") -> HTMLResponse:
    """返回空 body + HX-Trigger 弹 toast。htmx 前端监听 showToast 事件。"""
    payload = json.dumps({"showToast": {"message": msg, "type": level}})
    return HTMLResponse(
        "",
        status_code=200,
        headers={"HX-Trigger": payload, "HX-Reswap": "none"},
    )


def build_app(addon: MockAddon) -> FastAPI:
    app = FastAPI(title="mock-server", docs_url="/api/docs")
    app.state.addon = addon
    init_db()
    matcher.reload_from_db()  # 启动时载入已有规则

    @app.get("/", response_class=HTMLResponse)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/rules", status_code=302)

    # ---------- 规则 CRUD ----------

    @app.get("/rules", response_class=HTMLResponse)
    async def rules_index(request: Request):
        return templates.TemplateResponse(
            request,
            "rules.html",
            {
                "rules": models.list_rules(),
                "allowed_methods": models.ALLOWED_METHODS,
                "active": "rules",
            },
        )

    @app.post("/rules", response_class=HTMLResponse)
    async def rules_create(
        request: Request,
        name: str = Form(...),
        url_pattern: str = Form(...),
        method: str = Form("*"),
        status_code: int = Form(200),
        response_headers: str = Form("{}"),
        response_body: str = Form(""),
        enabled: str = Form("1"),
    ):
        url_pattern = url_pattern.strip()
        is_enabled = (enabled == "1")
        # 启用时才检测冲突；停用规则可重复
        if is_enabled:
            conflict = matcher.find_conflict(url_pattern, method)
            if conflict:
                return _toast_response(
                    f"与规则 #{conflict.id}「{conflict.name}」冲突，请先关闭它"
                )
        models.create_rule(
            name=name.strip(),
            url_pattern=url_pattern,
            method=method,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            enabled=is_enabled,
        )
        matcher.reload_from_db()
        return _render_rule_list(request)

    @app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
    async def rules_edit_form(request: Request, rule_id: int):
        rule = models.get_rule(rule_id)
        if not rule:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request,
            "_rule_edit_row.html",
            {
                "rule": rule,
                "allowed_methods": models.ALLOWED_METHODS,
            },
        )

    @app.get("/rules/{rule_id}/cancel", response_class=HTMLResponse)
    async def rules_edit_cancel(request: Request, rule_id: int):
        rule = models.get_rule(rule_id)
        if not rule:
            return HTMLResponse("", status_code=404)
        return templates.TemplateResponse(
            request, "_rule_row.html", {"rule": rule}
        )

    @app.post("/rules/{rule_id}/edit", response_class=HTMLResponse)
    async def rules_update(
        request: Request,
        rule_id: int,
        name: str = Form(...),
        url_pattern: str = Form(...),
        method: str = Form("*"),
        status_code: int = Form(200),
        response_headers: str = Form("{}"),
        response_body: str = Form(""),
        enabled: str = Form("1"),
    ):
        if not models.get_rule(rule_id):
            return HTMLResponse("", status_code=404)
        url_pattern = url_pattern.strip()
        is_enabled = (enabled == "1")
        if is_enabled:
            conflict = matcher.find_conflict(url_pattern, method, exclude_id=rule_id)
            if conflict:
                return _toast_response(
                    f"与规则 #{conflict.id}「{conflict.name}」冲突，请先关闭它"
                )
        models.update_rule(
            rule_id,
            name=name.strip(),
            url_pattern=url_pattern,
            method=method,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            enabled=is_enabled,
        )
        matcher.reload_from_db()
        return _render_rule_list(request)

    @app.delete("/rules/{rule_id}", response_class=HTMLResponse)
    async def rules_delete(request: Request, rule_id: int):
        models.delete_rule(rule_id)
        matcher.reload_from_db()
        return _render_rule_list(request)

    @app.post("/rules/{rule_id}/toggle", response_class=HTMLResponse)
    async def rules_toggle(request: Request, rule_id: int):
        rule = models.get_rule(rule_id)
        if not rule:
            return HTMLResponse("", status_code=404)
        # 从「停用」切到「启用」时检测冲突
        if not rule.enabled:
            conflict = matcher.find_conflict(rule.url_pattern, rule.method, exclude_id=rule_id)
            if conflict:
                return _toast_response(
                    f"无法启用：与规则 #{conflict.id}「{conflict.name}」冲突"
                )
        models.toggle_rule(rule_id)
        matcher.reload_from_db()
        return _render_rule_list(request)

    # ---------- 内部辅助 ----------

    def _render_rule_list(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "_rule_list.html",
            {"rules": models.list_rules()},
        )

    # ---------- 流量 ----------

    @app.get("/flows", response_class=HTMLResponse)
    async def flows_index(request: Request):
        # 首屏给一个当前缓冲快照（倒序：新的在上）
        import time as _time
        recs = list(reversed(flows.buffer.snapshot()))
        # 给模板一个易读的时分秒
        rendered = [
            {
                "id": r.id,
                "time_str": _time.strftime("%H:%M:%S", _time.localtime(r.ts)),
                "method": r.method,
                "status": r.status,
                "url": r.url,
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "duration_ms": r.duration_ms,
            }
            for r in recs
        ]
        return templates.TemplateResponse(
            request,
            "flows.html",
            {"flows": rendered, "active": "flows"},
        )

    @app.get("/flows/stream")
    async def flows_stream(request: Request):
        """SSE：每来一条新流量推一条 event。"""
        queue = flows.buffer.subscribe()

        async def event_gen():
            try:
                # 连接打开时先发一个 hello，前端可以据此清掉「未连接」提示
                yield "event: hello\ndata: {}\n\n"
                heartbeat_counter = 0
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        rec = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        # 心跳：每 ~15 秒发一次，让代理/浏览器不要 idle close；
                        # 同时小 timeout 让协程能及时检查 is_disconnected，方便服务退出
                        heartbeat_counter += 1
                        if heartbeat_counter >= 15:
                            heartbeat_counter = 0
                            yield ": ping\n\n"
                        continue
                    yield f"data: {flows.record_to_json(rec)}\n\n"
            finally:
                flows.buffer.unsubscribe(queue)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁用反代缓冲
            },
        )

    @app.post("/flows/clear")
    async def flows_clear():
        flows.buffer.clear()
        return HTMLResponse("", status_code=204)

    @app.get("/flows/{flow_id}")
    async def flows_detail(flow_id: int):
        """返回一条流量的完整记录，用于「转规则」弹窗预填。"""
        from dataclasses import asdict
        from fastapi.responses import JSONResponse
        rec = flows.buffer.get(flow_id)
        if rec is None:
            return JSONResponse(
                {"error": "flow expired", "message": "这条流量已不在缓冲区"},
                status_code=404,
            )
        return JSONResponse(asdict(rec))

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app
