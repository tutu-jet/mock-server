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
from typing import Annotated, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import flows, models
from app.matcher import matcher, MASTER_SWITCH_KEY
from app.rule_matching import REQUEST_MATCHES, rule_identity
from app.system_rules import MIGRATION_KEY as SYSTEM_PATH_MIGRATION_KEY, scoped_default

TEMPLATES_DIR = Path(__file__).parent / "templates"
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
env.filters["strftime"] = lambda ts, fmt="%H:%M:%S": time.strftime(fmt, time.localtime(float(ts)))
env.filters["human_bytes"] = flows.human_bytes


def _pretty_json(text: str) -> Optional[str]:
    """把一行压缩的 JSON 格式化成带缩进的多行字符串。
    不是合法 JSON（如 HTML、纯文本、被截断的片段）时返回 None，模板回退到原文。"""
    s = (text or "").strip()
    if not s or s[0] not in "{[":
        return None
    try:
        return json.dumps(json.loads(s), ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        return None


env.filters["pretty_json"] = _pretty_json
env.globals["MAX_BODY_BYTES"] = flows.MAX_BODY_BYTES
env.globals["REQUEST_MATCHES"] = REQUEST_MATCHES


def _static_version() -> str:
    """用 app.css 的 mtime 做版本号，改样式后浏览器自动重新拉取，无需手动强刷。"""
    try:
        return str(int((Path(__file__).parent / "static" / "app.css").stat().st_mtime))
    except OSError:
        return "0"


env.globals["STATIC_VERSION"] = _static_version()


def render(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**ctx))


router = APIRouter()

METHODS = ["*", "GET", "POST", "PUT", "DELETE", "PATCH"]


# ---------- 首页 ----------

@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/rules", status_code=302)


# ---------- 规则页 ----------

# 规则名以 "[域] ..." 开头，这里把域提取出来用于分组。域顺序固定，未知归到"其他"。
DOMAIN_ORDER = ["case", "servicerequest", "system", "calendar", "consumption", "provisioning", "notification"]
DOMAIN_LABELS = {
    "case": "Case",
    "servicerequest": "Service Request",
    "system": "System",
    "calendar": "Calendar",
    "consumption": "Consumption",
    "provisioning": "Provisioning",
    "notification": "Notification",
    "other": "Other",
}


def _rule_domain(rule) -> str:
    """从规则名 '[域] ...' 提取域；取不到归 'other'。"""
    name = rule.name or ""
    if name.startswith("[") and "]" in name:
        return name[1:name.index("]")].strip()
    return "other"


def _rule_state(rule) -> str:
    """预设身份不随编辑返回码改变；兼容尚未迁移的普通规则。"""
    if rule.state_key:
        return rule.state_key
    if rule.status_code == 403:
        return "noauth"
    if rule.status_code == 500:
        return "error"
    if rule.status_code == 200:
        return "nodata"
    return "other"


def _entity_name(rule) -> str:
    """从规则名 '[域] EntitySet · 态' 提取 EntitySet。"""
    name = rule.name or ""
    if "]" in name:
        rest = name[name.index("]") + 1:].strip()
        if "·" in rest:
            return rest.split("·")[0].strip()
        return rest
    return name


def _card_pages(card) -> tuple[str, ...]:
    entity = card["pattern"].removesuffix("*").rsplit("/", 1)[-1].split("(", 1)[0].split("?", 1)[0]
    if card["domain"] == "calendar":
        service = {"TimelineEvents": "calendar", "EventType": "mobile", "CalendarSettings": "mobile"}.get(entity)
        if service and f"/odata/{service}/{entity}" in card["pattern"]:
            return ("list",)
        return ("unclassified",)
    if card["domain"] == "notification":
        return ("list",)
    if card["domain"] == "system":
        if card.get("request_match") == "system-list":
            return ("list",)
        if card.get("request_match") == "system-detail":
            return ("detail",)
        if entity in ("CACCloudAvaStatus", "CACSystemDetailHeader", "SystemBundle"):
            return ("detail",)
        return {
            "BundleStatuses": ("bundle-list",),
            "BundleSubscriptions": ("bundle-detail",),
            "BundleDetailHeader": ("bundle-detail",),
            "BundleSystem": ("bundle-detail",),
        }.get(entity, ("unclassified",))
    if card["domain"] != "case":
        return ("unclassified",)
    if card["request_match"] in ("case-my", "case-all"):
        return ("overview", "list")
    if card["request_match"] == "case-detail":
        return ("detail",)
    return {
        "CaseSystem": ("list",),
        "CaseCloseButtonStatus": ("detail",),
        "IncidentHeader": ("detail",),
        "IncidentMessageAuth": ("detail",),
    }.get(entity, ("unclassified",))


def _group_rules_by_domain(rules):
    """域 -> 页面 -> 卡片；跨页卡片引用相同规则，域计数按匹配身份去重。"""
    # 先按域、再按 (entity url_pattern) 聚合
    by_domain: dict[str, dict[tuple[str, str, str], dict]] = {}
    for r in rules:
        dom = _rule_domain(r)
        ent_key = rule_identity(r)
        ent_map = by_domain.setdefault(dom, {})
        card = ent_map.setdefault(ent_key, {
            "name": _entity_name(r),
            "pattern": r.url_pattern,
            "method": r.method,
            "request_match": r.request_match,
            "selector_label": REQUEST_MATCHES.get(r.request_match, r.request_match),
            "domain": dom,
            "shared": r.request_match in ("case-my", "case-all"),
            "states": {},       # state -> rule
            "active": None,     # 当前启用的 state
        })
        card["states"][_rule_state(r)] = r
        if r.enabled:
            card["active"] = _rule_state(r)

    ordered_keys = [d for d in DOMAIN_ORDER if d in by_domain]
    ordered_keys += [d for d in by_domain if d not in DOMAIN_ORDER]
    groups = []
    for key in ordered_keys:
        cards = list(by_domain[key].values())
        cards.sort(key=lambda c: c["name"])
        enabled = sum(1 for c in cards if c["active"])
        pages = []
        for page, label in (
            ("overview", "概览 Overview"), ("list", "列表 List"),
            ("detail", "详情 Detail"),
            ("bundle-list", "bundle list"),
            ("bundle-detail", "bundle detail"),
            ("unclassified", "未分类"),
        ):
            members = [c for c in cards if page in _card_pages(c)]
            if members:
                page_label = f"{key} {page}" if key in ("system", "calendar") and page in ("list", "detail") else label
                pages.append({"key": page, "label": page_label, "entities": members})
        groups.append({
            "key": key,
            "label": DOMAIN_LABELS.get(key, key),
            "entities": cards,
            "enabled_count": enabled,
            "total": len(cards),
            "pages": pages,
        })
    return groups


@router.get("/rules", response_class=HTMLResponse)
def rules_page(q: str = "", method: str = "ALL", enabled_only: bool = False):
    return render("rules.html", **_rules_context(q, method, enabled_only))


def _rules_context(q="", method="ALL", enabled_only=False):
    rules = models.list_rules()
    # q/method 过滤仍按单条规则；enabled_only 在聚合后按 entity 过滤
    filtered = _filter_rules(rules, q, method, False)
    filtered_sorted = sorted(filtered, key=lambda r: r.id)
    groups = _group_rules_by_domain(filtered_sorted)
    if enabled_only:
        for g in groups:
            g["entities"] = [e for e in g["entities"] if e["active"]]
            for page in g["pages"]:
                page["entities"] = [e for e in page["entities"] if e["active"]]
            g["pages"] = [page for page in g["pages"] if page["entities"]]
        groups = [g for g in groups if g["entities"]]
    return dict(
        active_tab="rules",
        groups=groups,
        master_enabled=matcher.master_enabled,
        total=len({rule_identity(r) for r in rules}),
        enabled_count=len({rule_identity(r) for r in rules if r.enabled}),
        q=q,
        method_filter=method,
        enabled_only=enabled_only,
        methods=["ALL", "GET", "POST", "PUT", "DELETE", "PATCH", "*"],
    )


def _rule_changed(request: Request, rule_id: int, message: str):
    query = parse_qs(urlparse(request.headers.get("HX-Current-URL", "")).query)
    ctx = _rules_context(
        query.get("q", [""])[0], query.get("method", ["ALL"])[0],
        query.get("enabled_only", [""])[0].lower() in ("on", "true", "1"),
    )
    template = env.get_template("rules.html")
    content = "".join(template.blocks["content"](template.new_context(ctx)))
    content = content.replace('id="rules-content"', 'id="rules-content" hx-swap-oob="outerHTML"', 1)
    response = HTMLResponse(rule_detail(rule_id).body + content.encode())
    response.headers["HX-Trigger"] = json.dumps({"toast": {"kind": "success", "msg": message}})
    return response


def _configuration_conflict(rule_id, config, enabled):
    current = models.get_rule(rule_id)
    for rule in models.list_rules():
        if rule.id == rule_id:
            continue
        if rule_identity(rule) == (config["url_pattern"], config["method"], config["request_match"]):
            if _rule_state(rule) == _rule_state(current):
                return rule
    if enabled:
        return matcher.find_conflict(
            config["url_pattern"], config["method"], exclude_id=rule_id,
            request_match=config["request_match"],
        )
    return None


@router.post("/rules/{rule_id}/toggle", response_class=HTMLResponse)
def rule_toggle(rule_id: int):
    r = models.get_rule(rule_id)
    if not r:
        raise HTTPException(404)
    target = not r.enabled
    if target:
        conflict = matcher.find_conflict(
            r.url_pattern, r.method, exclude_id=rule_id, request_match=r.request_match,
        )
        if conflict:
            resp = render("_rule_card.html", rule=r)
            resp.headers["HX-Trigger"] = json.dumps({
                "toast": {"kind": "error", "msg": f"无法启用：与 #{conflict.id}「{conflict.name}」冲突"}
            })
            return resp
    models.set_rule_enabled(rule_id, target)
    matcher.reload_from_db()
    return render("_rule_card.html", rule=models.get_rule(rule_id))


def _rules_redirect(request: Request) -> RedirectResponse:
    """HTMX 局部更新保留当前筛选；普通表单仍回到规则页。"""
    query = ""
    if request.headers.get("HX-Request") == "true":
        query = urlparse(request.headers.get("HX-Current-URL", "")).query
    return RedirectResponse("/rules" + ("?" + query if query else ""), status_code=303)


@router.post("/rules/entity/state")
def rule_set_entity_state(
    request: Request, pattern: str = Form(...), state: str = Form(...),
    method: Annotated[str, Form()] = "GET",
    request_match: Annotated[str, Form()] = "",
):
    """切换同一 pattern + method + 请求分类下的状态，跨页面共享。"""
    if state not in ("noauth", "error", "nodata", "off"):
        raise HTTPException(422, "未知规则状态")
    rules = models.list_rules()
    members = [r for r in rules if rule_identity(r) == (pattern, method, request_match)]
    if not members:
        raise HTTPException(404, "规则不存在，请刷新页面")
    if state != "off":
        if sum(_rule_state(r) == state for r in members) != 1:
            raise HTTPException(409, "该状态不存在或有重复规则，请先编辑规则")
        for r in rules:
            if not r.enabled or r in members or r.url_pattern != pattern:
                continue
            if r.method in ("*", method) or method == "*":
                if not r.request_match or not request_match or r.request_match == request_match:
                    raise HTTPException(409, "与已有启用规则冲突，请先关闭它")
    for r in members:
        want = (state != "off" and _rule_state(r) == state)
        if r.enabled != want:
            models.set_rule_enabled(r.id, want)
    matcher.reload_from_db()
    return _rules_redirect(request)


@router.delete("/rules/{rule_id}", response_class=Response)
def rule_delete(rule_id: int):
    models.delete_rule(rule_id)
    matcher.reload_from_db()
    return Response(status_code=200)


@router.post("/rules/master")
def rules_master_toggle(request: Request):
    """整体 mock 总开关：关闭时所有请求透传，忽略全部规则。切换后回规则页。"""
    current = models.get_setting(MASTER_SWITCH_KEY, "1") != "0"
    models.set_setting(MASTER_SWITCH_KEY, "0" if current else "1")
    matcher.reload_from_db()
    return _rules_redirect(request)


@router.post("/rules/domain/{domain}/disable")
def rules_domain_disable(domain: str, request: Request):
    """一键关闭某个域下的所有规则（恢复该域透传）。操作后回规则页。"""
    for r in models.list_rules():
        if _rule_domain(r) == domain and r.enabled:
            models.set_rule_enabled(r.id, False)
    matcher.reload_from_db()
    return _rules_redirect(request)
@router.get("/rules/new", response_class=HTMLResponse)
def rule_new_form():
    return render("_rule_form.html", rule=None, methods=METHODS)


@router.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
def rule_edit_form(rule_id: int):
    r = models.get_rule(rule_id)
    if not r:
        raise HTTPException(404)
    variants = [item for item in models.list_rules() if rule_identity(item) == rule_identity(r)]
    return render("_rule_form.html", rule=r, methods=METHODS, variants=variants, rule_state=_rule_state)


@router.get("/rules/{rule_id}/detail", response_class=HTMLResponse)
def rule_detail(rule_id: int):
    rule = models.get_rule(rule_id)
    if not rule:
        raise HTTPException(404)
    variants = [r for r in models.list_rules() if rule_identity(r) == rule_identity(rule)]
    return render("_rule_detail.html", rule=rule, variants=variants, rule_state=_rule_state)


@router.post("/rules/{rule_id}/restore", response_class=HTMLResponse)
def rule_restore(rule_id: int, request: Request):
    rule = models.get_rule(rule_id)
    if not rule:
        raise HTTPException(404)
    if not rule.default_config:
        return _toast_resp("默认快照尚未建立，请重启新版服务", "error")
    config = scoped_default(
        rule_id, json.loads(rule.default_config),
        models.get_setting(SYSTEM_PATH_MIGRATION_KEY, "{}"),
    )
    conflict = _configuration_conflict(rule_id, config, rule.enabled)
    if conflict:
        return _toast_resp(f"恢复与规则 #{conflict.id} 冲突，请先调整该规则", "error")
    models.update_rule(rule_id, **config, enabled=rule.enabled)
    matcher.reload_from_db()
    return _rule_changed(request, rule_id, "已恢复默认配置，启用状态保持不变")


@router.post("/rules", response_class=HTMLResponse)
def rule_create(
    name: str = Form(...),
    url_pattern: str = Form(...),
    method: str = Form("*"),
    status_code: int = Form(200),
    response_headers: str = Form("{}"),
    response_body: str = Form(""),
    enabled: Optional[str] = Form(None),
    request_match: str = Form(""),
):
    return _save_rule(None, name, url_pattern, method, status_code,
                      response_headers, response_body, enabled == "on", request_match)


@router.post("/rules/{rule_id}", response_class=HTMLResponse)
async def rule_update(
    rule_id: int,
    request: Request,
    name: str = Form(...),
    url_pattern: str = Form(...),
    method: str = Form("*"),
    status_code: int = Form(200),
    response_headers: str = Form("{}"),
    response_body: str = Form(""),
    enabled: Optional[str] = Form(None),
    request_match: Optional[str] = Form(None),
):
    existing = models.get_rule(rule_id)
    if not existing:
        raise HTTPException(404)
    form = await request.form()
    request_match = str(form["request_match"]) if "request_match" in form else existing.request_match
    return _save_rule(rule_id, name, url_pattern, method, status_code,
                      response_headers, response_body, existing.enabled, request_match, request)


# ---------- 流量页 ----------

BLOCKED_HOSTS_KEY = "flow_blocked_hosts"


def _load_blocked_hosts() -> tuple[str, list[str]]:
    """返回 (raw_text, parsed_list)。DB 中不存在则用默认值并写入一次。"""
    raw = models.get_setting(BLOCKED_HOSTS_KEY, default="__unset__")
    if raw == "__unset__":
        raw = ",".join(flows.DEFAULT_BLOCKED_HOSTS)
        models.set_setting(BLOCKED_HOSTS_KEY, raw)
    return raw, flows.parse_blocked_hosts(raw)


@router.get("/flows", response_class=HTMLResponse)
def flows_page():
    raw, blocked_list = _load_blocked_hosts()
    all_snap = list(reversed(flows.buffer.snapshot()))
    if blocked_list:
        snap = [f for f in all_snap if not flows.host_blocked(f.url, blocked_list)]
    else:
        snap = all_snap
    filtered_out = len(all_snap) - len(snap)
    return render(
        "flows.html",
        active_tab="flows",
        flows=snap,
        total=len(snap),
        hit_count=sum(1 for f in snap if f.rule_id is not None),
        filtered_out=filtered_out,
        blocked_hosts_raw=raw,
        default_blocked_hosts=",".join(flows.DEFAULT_BLOCKED_HOSTS),
    )


@router.post("/flows/blocked-hosts", response_class=HTMLResponse)
def flows_set_blocked_hosts(value: str = Form("")):
    """保存 host 黑名单到 DB；返回 HX-Refresh 让页面整页刷新。"""
    models.set_setting(BLOCKED_HOSTS_KEY, value.strip())
    resp = HTMLResponse("", status_code=200)
    resp.headers["HX-Refresh"] = "true"
    return resp


@router.get("/flows/stream")
async def flows_stream(request: Request):
    """SSE：每条新流量推一行 HTML 片段。建立连接时读一次黑名单缓存到闭包。
    用户改了黑名单后前端会触发整页刷新，自动重连 SSE，新值生效。"""
    _, blocked_list = _load_blocked_hosts()
    queue = flows.buffer.subscribe()
    tpl = env.get_template("_flow_row.html")
    shutting_down = request.app.state.shutting_down

    async def gen():
        try:
            while True:
                if await request.is_disconnected() or shutting_down.is_set():
                    break
                # 同时等「新流量」和「服务关停」，任一先到就醒。
                # 否则 shutdown 时会一直卡在 queue.get()，被 graceful
                # 超时强制 cancel，刷出一条 CancelledError traceback。
                get_task = asyncio.ensure_future(queue.get())
                stop_task = asyncio.ensure_future(shutting_down.wait())
                done, pending = await asyncio.wait(
                    {get_task, stop_task},
                    timeout=15.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                if stop_task in done:
                    break
                if get_task not in done:  # 超时，发心跳保活
                    yield ": ping\n\n"
                    continue
                rec = get_task.result()
                if blocked_list and flows.host_blocked(rec.url, blocked_list):
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
        request_match="",
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
    # 找一下用此预填 url_pattern + method 保存时会冲突的已启用规则（完全相同 pattern）
    existing_rule = matcher.find_conflict(fields["url_pattern"], fields["method"])
    # 兜底两种"非 enabled+完全相同"的情况：
    #   - existing_disabled: 完全相同 pattern + method 但 disabled
    #   - existing_wildcard: fnmatch 通配命中 flow 实际 URL 的已启用规则（pattern 不完全相同）
    existing_disabled = None
    existing_wildcard = None
    if not existing_rule:
        existing_disabled = _find_same_pattern_disabled(
            fields["url_pattern"], fields["method"]
        )
        if not existing_disabled:
            hit = matcher.match(rec.url, rec.method)
            # match 可能命中"完全相同 pattern 且启用"那条（理论上 find_conflict 已经覆盖），
            # 这里只在 pattern 不完全相同时才当作通配兜底
            if hit and hit.url_pattern != fields["url_pattern"]:
                existing_wildcard = hit
    return render(
        "_rule_form.html",
        rule=prefilled,
        methods=METHODS,
        from_flow=rec,
        from_flow_truncated=rec.resp_body_truncated,
        from_flow_binary=binary,
        existing_rule=existing_rule,
        existing_disabled=existing_disabled,
        existing_wildcard=existing_wildcard,
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


def _detect_lan_ips() -> list[str]:
    """列出本机所有候选局域网 IPv4，默认网卡的那个排在最前。

    多网卡（公司网 + 热点 + 虚拟网卡）时，UDP 探测只能给一个，手机未必连得上。
    这里枚举全部网卡地址，去掉回环，让用户按「哪个连得上用哪个」自己选。
    """
    primary = _detect_lan_ip()
    ips: list[str] = []
    try:
        # getaddrinfo 枚举本机主机名解析到的所有地址
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    # 默认网卡探测结果置顶；回环仅在没有任何候选时保留
    ordered = [primary] if primary != "127.0.0.1" else []
    for ip in ips:
        if ip != "127.0.0.1" and ip not in ordered:
            ordered.append(ip)
    if not ordered:
        ordered = [primary]
    return ordered


@router.get("/help", response_class=HTMLResponse)
def help_page():
    lan_ips = _detect_lan_ips()
    return render(
        "help.html",
        active_tab="help",
        proxy_port=9077,
        ui_port=9088,
        lan_ip=lan_ips[0],
        lan_ips=lan_ips,
    )


# ---------- 内部工具 ----------

def _find_same_pattern_disabled(url_pattern: str, method: str):
    """找一条已禁用、url_pattern 完全相同、method 兼容（同名或一方是 *）的规则。"""
    for rule in models.list_rules():
        if rule.enabled:
            continue
        if rule.url_pattern != url_pattern or rule.request_match:
            continue
        if rule.method == method or rule.method == "*" or method == "*":
            return rule
    return None


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
               response_headers, response_body, enabled, request_match="", request=None):
    name = name.strip()
    url_pattern = url_pattern.strip()
    if not name or not url_pattern:
        return _toast_resp("名称和 URL pattern 不能为空", "error")
    if request_match not in REQUEST_MATCHES:
        return _toast_resp("未知请求分类，请重新选择", "error")
    if method not in METHODS or not 100 <= status_code <= 599:
        return _toast_resp("方法或返回状态码无效", "error")
    try:
        obj = json.loads((response_headers or "{}").strip() or "{}")
        if not isinstance(obj, dict):
            raise ValueError
    except Exception:
        return _toast_resp("Headers 必须是合法 JSON 对象", "error")
    if rule_id:
        conflict = _configuration_conflict(
            rule_id, dict(url_pattern=url_pattern, method=method, request_match=request_match), enabled,
        )
        if conflict:
            return _toast_resp(f"与规则 #{conflict.id} 配置或状态冲突，请先调整", "error")
    if enabled:
        conflict = matcher.find_conflict(
            url_pattern, method, exclude_id=rule_id, request_match=request_match,
        )
        if conflict:
            return _toast_resp(
                f"与规则 #{conflict.id}「{conflict.name}」冲突，请先关闭它", "error"
            )
    if rule_id:
        models.update_rule(
            rule_id, name=name, url_pattern=url_pattern, method=method,
            status_code=status_code, response_headers=response_headers,
            response_body=response_body, enabled=enabled, request_match=request_match,
        )
    else:
        models.create_rule(
            name=name, url_pattern=url_pattern, method=method,
            status_code=status_code, response_headers=response_headers,
            response_body=response_body, enabled=enabled, request_match=request_match,
        )
    matcher.reload_from_db()
    if rule_id and request is not None:
        return _rule_changed(request, rule_id, "规则已保存，启用状态保持不变")
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/rules"
    return resp


def _toast_resp(msg: str, kind: str = "info") -> HTMLResponse:
    resp = HTMLResponse("", status_code=200)
    resp.headers["HX-Trigger"] = json.dumps({"toast": {"kind": kind, "msg": msg}})
    resp.headers["HX-Reswap"] = "none"
    return resp
