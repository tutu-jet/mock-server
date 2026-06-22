"""FastAPI 路由 + Jinja2 模板渲染。

路由风格：
- GET /                       -> 重定向 /rules
- GET /rules                  -> 整页
- POST /rules/{id}/toggle     -> 返回单张卡片 HTML
- DELETE /rules/{id}          -> 移除节点
- POST /rules                 -> 创建（form）
- POST /rules/{id}            -> 更新（form）
- GET  /rules/new             -> 抽屉表单（创建）
- GET  /rules/{id}/edit       -> 抽屉表单（编辑）
- GET  /flows                 -> 整页
- GET  /flows/stream          -> SSE
- GET  /flows/{id}            -> 详情面板片段
- DELETE /flows               -> 清空
- GET  /settings              -> 整页
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import flows, models
from app.matcher import matcher

TEMPLATES_DIR = Path(__file__).parent / "templates"
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def render(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**ctx))


router = APIRouter()

METHODS = ["*", "GET", "POST", "PUT", "DELETE", "PATCH"]


# ---------- 首页 ----------

@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/rules", status_code=302)


# ---------- 规则页 ----------

@router.get("/rules", response_class=HTMLResponse)
def rules_page(q: str = "", method: str = "ALL", enabled_only: bool = False):
    rules = models.list_rules()
    filtered = _filter_rules(rules, q, method, enabled_only)
    return render(
        "rules.html",
        active_tab="rules",
        rules=filtered,
        total=len(rules),
        enabled_count=sum(1 for r in rules if r.enabled),
        q=q,
        method_filter=method,
        enabled_only=enabled_only,
        methods=["ALL", "GET", "POST", "PUT", "DELETE", "PATCH", "*"],
    )


@router.post("/rules/{rule_id}/toggle", response_class=HTMLResponse)
def rule_toggle(rule_id: int):
    r = models.get_rule(rule_id)
    if not r:
        raise HTTPException(404)
    target = not r.enabled
    if target:
        conflict = matcher.find_conflict(r.url_pattern, r.method, exclude_id=rule_id)
        if conflict:
            resp = render("_rule_card.html", rule=r)
            resp.headers["HX-Trigger"] = json.dumps({
                "toast": {"kind": "error", "msg": f"无法启用：与 #{conflict.id}「{conflict.name}」冲突"}
            })
            return resp
    models.set_rule_enabled(rule_id, target)
    matcher.reload_from_db()
    return render("_rule_card.html", rule=models.get_rule(rule_id))


@router.delete("/rules/{rule_id}", response_class=Response)
def rule_delete(rule_id: int):
    models.delete_rule(rule_id)
    matcher.reload_from_db()
    return Response(status_code=200)


@router.get("/rules/new", response_class=HTMLResponse)
def rule_new_form():
    return render("_rule_form.html", rule=None, methods=METHODS)


@router.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
def rule_edit_form(rule_id: int):
    r = models.get_rule(rule_id)
    if not r:
        raise HTTPException(404)
    return render("_rule_form.html", rule=r, methods=METHODS)


@router.post("/rules", response_class=HTMLResponse)
def rule_create(
    name: str = Form(...),
    url_pattern: str = Form(...),
    method: str = Form("*"),
    status_code: int = Form(200),
    response_headers: str = Form("{}"),
    response_body: str = Form(""),
    enabled: Optional[str] = Form(None),
):
    return _save_rule(None, name, url_pattern, method, status_code,
                      response_headers, response_body, enabled == "on")


@router.post("/rules/{rule_id}", response_class=HTMLResponse)
def rule_update(
    rule_id: int,
    name: str = Form(...),
    url_pattern: str = Form(...),
    method: str = Form("*"),
    status_code: int = Form(200),
    response_headers: str = Form("{}"),
    response_body: str = Form(""),
    enabled: Optional[str] = Form(None),
):
    if not models.get_rule(rule_id):
        raise HTTPException(404)
    return _save_rule(rule_id, name, url_pattern, method, status_code,
                      response_headers, response_body, enabled == "on")


# ---------- 流量页 ----------

@router.get("/flows", response_class=HTMLResponse)
def flows_page():
    snap = list(reversed(flows.buffer.snapshot()))
    return render(
        "flows.html",
        active_tab="flows",
        flows=snap,
        total=len(snap),
        hit_count=sum(1 for f in snap if f.rule_id is not None),
    )


@router.get("/flows/stream")
async def flows_stream(request: Request):
    """SSE：每条新流量推一行 HTML 片段。"""
    queue = flows.buffer.subscribe()
    tpl = env.get_template("_flow_row.html")

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    rec = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                html = tpl.render(flow=rec).replace("\n", " ")
                yield f"event: flow\ndata: {html}\n\n"
        finally:
            flows.buffer.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/flows/{flow_id}", response_class=HTMLResponse)
def flow_detail(flow_id: int):
    rec = flows.buffer.get(flow_id)
    if not rec:
        return HTMLResponse('<div class="empty">流量已过期</div>')
    return render("_flow_detail.html", flow=rec)


@router.delete("/flows", response_class=Response)
def flows_clear():
    flows.buffer.clear()
    return Response(status_code=200)


# ---------- 设置页 ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page():
    return render("settings.html", active_tab="settings", proxy_port=9077, ui_port=9088)


# ---------- 内部工具 ----------

def _filter_rules(rules, q: str, method: str, enabled_only: bool):
    out = rules
    if method != "ALL":
        out = [r for r in out if r.method == method or r.method == "*"]
    if enabled_only:
        out = [r for r in out if r.enabled]
    if q.strip():
        kw = q.strip().lower()
        out = [r for r in out if kw in r.name.lower() or kw in r.url_pattern.lower()]
    return out


def _save_rule(rule_id, name, url_pattern, method, status_code,
               response_headers, response_body, enabled):
    name = name.strip()
    url_pattern = url_pattern.strip()
    if not name or not url_pattern:
        return _toast_resp("名称和 URL pattern 不能为空", "error")
    try:
        obj = json.loads((response_headers or "{}").strip() or "{}")
        if not isinstance(obj, dict):
            raise ValueError
    except Exception:
        return _toast_resp("Headers 必须是合法 JSON 对象", "error")
    if enabled:
        conflict = matcher.find_conflict(url_pattern, method, exclude_id=rule_id)
        if conflict:
            return _toast_resp(
                f"与规则 #{conflict.id}「{conflict.name}」冲突，请先关闭它", "error"
            )
    if rule_id:
        models.update_rule(
            rule_id, name=name, url_pattern=url_pattern, method=method,
            status_code=status_code, response_headers=response_headers,
            response_body=response_body, enabled=enabled,
        )
    else:
        models.create_rule(
            name=name, url_pattern=url_pattern, method=method,
            status_code=status_code, response_headers=response_headers,
            response_body=response_body, enabled=enabled,
        )
    matcher.reload_from_db()
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/rules"
    return resp


def _toast_resp(msg: str, kind: str = "info") -> HTMLResponse:
    resp = HTMLResponse("", status_code=200)
    resp.headers["HX-Trigger"] = json.dumps({"toast": {"kind": kind, "msg": msg}})
    return resp
