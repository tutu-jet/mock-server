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
from app.matcher import matcher, MASTER_SWITCH_KEY

TEMPLATES_DIR = Path(__file__).parent / "templates"
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
env.filters["strftime"] = lambda ts, fmt="%H:%M:%S": time.strftime(fmt, time.localtime(float(ts)))
env.filters["human_bytes"] = flows.human_bytes
env.globals["MAX_BODY_BYTES"] = flows.MAX_BODY_BYTES


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
DOMAIN_ORDER = ["case", "servicerequest", "system", "calendar", "consumption", "provisioning"]
DOMAIN_LABELS = {
    "case": "案件 Case",
    "servicerequest": "服务请求 Service Request",
    "system": "云系统 System",
    "calendar": "日历 Calendar",
    "consumption": "消费 Consumption",
    "provisioning": "预配置 Provisioning",
    "other": "其他 Other",
}


def _rule_domain(rule) -> str:
    """从规则名 '[域] ...' 提取域；取不到归 'other'。"""
    name = rule.name or ""
    if name.startswith("[") and "]" in name:
        return name[1:name.index("]")].strip()
    return "other"


def _rule_state(rule) -> str:
    """从 status_code + body 推断态：noauth / error / nodata / other。"""
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


def _group_rules_by_domain(rules):
    """两层聚合：域 -> [entity 卡片]。每个 entity 卡片含 403/500/nodata 三态规则 id 与当前启用态。
    返回 [{key,label,total,enabled_count, entities:[{name,pattern,method,states:{noauth,error,nodata},active}]}]。
    """
    # 先按域、再按 (entity url_pattern) 聚合
    by_domain: dict[str, dict[str, dict]] = {}
    for r in rules:
        dom = _rule_domain(r)
        ent_key = r.url_pattern  # url_pattern 唯一标识一个 entity
        ent_map = by_domain.setdefault(dom, {})
        card = ent_map.setdefault(ent_key, {
            "name": _entity_name(r),
            "pattern": r.url_pattern,
            "method": r.method,
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
        groups.append({
            "key": key,
            "label": DOMAIN_LABELS.get(key, key),
            "entities": cards,
            "enabled_count": enabled,
            "total": len(cards),
        })
    return groups


@router.get("/rules", response_class=HTMLResponse)
def rules_page(q: str = "", method: str = "ALL", enabled_only: bool = False):
    rules = models.list_rules()
    # q/method 过滤仍按单条规则；enabled_only 在聚合后按 entity 过滤
    filtered = _filter_rules(rules, q, method, False)
    filtered_sorted = sorted(filtered, key=lambda r: r.id)
    groups = _group_rules_by_domain(filtered_sorted)
    if enabled_only:
        for g in groups:
            g["entities"] = [e for e in g["entities"] if e["active"]]
        groups = [g for g in groups if g["entities"]]
    return render(
        "rules.html",
        active_tab="rules",
        groups=groups,
        master_enabled=matcher.master_enabled,
        total=len({r.url_pattern for r in rules}),
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


@router.post("/rules/entity/state")
def rule_set_entity_state(pattern: str = Form(...), state: str = Form(...)):
    """设置某 entity（由 url_pattern 唯一标识）的态：单选语义。
    state ∈ {noauth, error, nodata, off}。开目标态、关同 pattern 其它态。操作后回规则页。"""
    for r in models.list_rules():
        if r.url_pattern != pattern:
            continue
        want = (state != "off" and _rule_state(r) == state)
        if r.enabled != want:
            models.set_rule_enabled(r.id, want)
    matcher.reload_from_db()
    return RedirectResponse("/rules", status_code=303)


@router.delete("/rules/{rule_id}", response_class=Response)
def rule_delete(rule_id: int):
    models.delete_rule(rule_id)
    matcher.reload_from_db()
    return Response(status_code=200)


@router.post("/rules/master")
def rules_master_toggle():
    """整体 mock 总开关：关闭时所有请求透传，忽略全部规则。切换后回规则页。"""
    current = models.get_setting(MASTER_SWITCH_KEY, "1") != "0"
    models.set_setting(MASTER_SWITCH_KEY, "0" if current else "1")
    matcher.reload_from_db()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/domain/{domain}/disable")
def rules_domain_disable(domain: str):
    """一键关闭某个域下的所有规则（恢复该域透传）。操作后回规则页。"""
    for r in models.list_rules():
        if _rule_domain(r) == domain and r.enabled:
            models.set_rule_enabled(r.id, False)
    matcher.reload_from_db()
    return RedirectResponse("/rules", status_code=303)
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
        if rule.url_pattern != url_pattern:
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
