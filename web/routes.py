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
- GET  /help                  -> 整页（帮助）
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlparse

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
env.filters["strftime"] = lambda ts, fmt="%H:%M:%S": time.strftime(fmt, time.localtime(float(ts)))
env.filters["human_bytes"] = flows.human_bytes
env.globals["MAX_BODY_BYTES"] = flows.MAX_BODY_BYTES


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
        return render("_flow_expired.html")
    return render("_flow_detail.html", flow=rec)


# content-type -> 下载文件扩展名
_EXT_MAP = [
    ("application/json", "json"),
    ("application/xml", "xml"),
    ("text/xml", "xml"),
    ("text/html", "html"),
    ("text/css", "css"),
    ("text/javascript", "js"),
    ("application/javascript", "js"),
    ("text/csv", "csv"),
    ("text/plain", "txt"),
    ("application/x-www-form-urlencoded", "txt"),
]


def _guess_ext(content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    for prefix, ext in _EXT_MAP:
        if ct == prefix:
            return ext
    if ct.endswith("+json"):
        return "json"
    if ct.endswith("+xml"):
        return "xml"
    if ct.startswith("text/"):
        return "txt"
    return "bin"


@router.get("/flows/{flow_id}/body/{kind}")
def flow_body(flow_id: int, kind: str, inline: int = 0):
    """下载或获取流量原始 body。
    kind: 'req' | 'resp'
    inline=1: 返回 text/plain 给前端复制用（不触发下载对话框）
    inline=0: 触发浏览器下载，filename=flow_<id>_<kind>.<ext>
    """
    if kind not in ("req", "resp"):
        raise HTTPException(400, "kind 必须是 req 或 resp")
    row = models.get_flow_body(flow_id, kind)
    if not row:
        raise HTTPException(404, "body 不存在或已被清空")
    content, content_type, _size = row
    if inline:
        # 复制场景：统一 text/plain，浏览器 fetch 后 navigator.clipboard.writeText
        return Response(content=content, media_type="text/plain; charset=utf-8")
    ext = _guess_ext(content_type)
    filename = f"flow_{flow_id}_{kind}.{ext}"
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/flows", response_class=Response)
def flows_clear():
    flows.buffer.clear()
    # 同步清空数据库里的原始 body
    try:
        models.clear_flow_bodies()
    except Exception as e:
        # 清不掉只是占点磁盘，不影响功能
        import logging
        logging.getLogger("mock-server").warning(f"clear_flow_bodies 失败: {e}")
    return Response(status_code=200)


# ---------- 从流量保存为规则 ----------

def _build_rule_from_flow(rec) -> dict:
    """从 FlowRecord 构造规则字段（用内存预览版 body，截断时由 UI 提示用户自己改）。"""
    parsed = urlparse(rec.url)
    path = parsed.path or "/"
    # url_pattern 用完整 URL（scheme + host + path + query），片段(#fragment)丢弃
    if parsed.scheme and parsed.netloc:
        full_url = f"{parsed.scheme}://{parsed.netloc}{path}"
        if parsed.query:
            full_url += f"?{parsed.query}"
    else:
        full_url = rec.url.split("#", 1)[0] or path
    name = f"{rec.method} {path}"[:80]
    # headers 白名单：只保留 content-type，丢弃 length/encoding/cookie 等
    ct = ""
    for k, v in (rec.resp_headers or {}).items():
        if k.lower() == "content-type":
            ct = v
            break
    headers_obj = {"Content-Type": ct} if ct else {}
    return {
        "name": name,
        "url_pattern": full_url,
        "method": rec.method,
        "status_code": rec.status or 200,
        "response_headers": json.dumps(headers_obj, ensure_ascii=False),
        "response_body": rec.resp_body or "",
        "content_type": ct,
    }


def _is_binary_resp(rec) -> bool:
    """根据 resp content-type 判断是否二进制。空 body 不算二进制。"""
    if not rec.resp_body_size:
        return False
    ct = ""
    for k, v in (rec.resp_headers or {}).items():
        if k.lower() == "content-type":
            ct = v
            break
    if not ct:
        return False
    return not flows._TEXTUAL_RE.match(ct.strip())


@router.post("/flows/{flow_id}/save-as-rule", response_class=HTMLResponse)
def flow_save_as_rule(flow_id: int):
    """一键把流量保存为规则。截断的 body 仍保存（用内存预览版），并在 toast 里提示用户去详情页拿完整版。"""
    rec = flows.buffer.get(flow_id)
    if not rec:
        return _toast_resp("流量已被清空或挤出缓冲", "error")
    if _is_binary_resp(rec):
        return _toast_resp("二进制响应不支持一键保存，请用「编辑后保存」或手动新建规则", "error")

    fields = _build_rule_from_flow(rec)
    conflict = matcher.find_conflict(fields["url_pattern"], fields["method"], exclude_id=None)
    new_id = models.create_rule(
        name=fields["name"],
        url_pattern=fields["url_pattern"],
        method=fields["method"],
        status_code=fields["status_code"],
        response_headers=fields["response_headers"],
        response_body=fields["response_body"],
        enabled=True,
    )
    matcher.reload_from_db()

    # 组合 toast：截断 + 冲突可能同时存在
    parts = [f"已保存为规则 #{new_id}"]
    kind = "success"
    if rec.resp_body_truncated:
        full = flows.human_bytes(rec.resp_body_size)
        parts.append(f"⚠️ body 被截断（原始 {full}），请到流量 #{flow_id} 详情页下载/复制完整 body 后编辑规则")
        kind = "info"
    if conflict:
        parts.append(f"⚠️ 与规则 #{conflict.id}「{conflict.name}」冲突，请到规则页调整")
        kind = "info"
    return _toast_resp("；".join(parts), kind)


@router.get("/flows/{flow_id}/save-as-rule/edit", response_class=HTMLResponse)
def flow_save_as_rule_edit(flow_id: int):
    """打开抽屉表单，预填字段，用户编辑后走 POST /rules 创建。"""
    rec = flows.buffer.get(flow_id)
    if not rec:
        return render("_flow_expired.html")
    binary = _is_binary_resp(rec)
    fields = _build_rule_from_flow(rec)
    if binary:
        # 二进制：body 留空，由 banner 提示用户自己处理
        fields["response_body"] = ""
    # 模板用 rule.xxx 属性访问，封装成 SimpleNamespace；id=None 让模板走"新建"分支
    prefilled = SimpleNamespace(
        id=None,
        name=fields["name"],
        url_pattern=fields["url_pattern"],
        method=fields["method"],
        status_code=fields["status_code"],
        response_headers=fields["response_headers"],
        response_body=fields["response_body"],
        enabled=True,
    )
    return render(
        "_rule_form.html",
        rule=prefilled,
        methods=METHODS,
        from_flow=rec,
        from_flow_truncated=rec.resp_body_truncated,
        from_flow_binary=binary,
    )


# ---------- 帮助页 ----------

def _detect_lan_ip() -> str:
    """获取本机在局域网中的 IP（用 UDP 探测，不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@router.get("/help", response_class=HTMLResponse)
def help_page():
    return render(
        "help.html",
        active_tab="help",
        proxy_port=9077,
        ui_port=9088,
        lan_ip=_detect_lan_ip(),
    )


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
