"""规则页 State：CRUD + 过滤 + 抽屉。
直接复用 app.models / app.matcher，不绕远路。
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict, dataclass, field
from typing import Optional

import reflex as rx

from app import models
from app.matcher import matcher
from app.models import ALLOWED_METHODS


@dataclass
class RuleVM:
    """前端用 view model：纯可序列化字段。"""
    id: int = 0
    name: str = ""
    url_pattern: str = ""
    method: str = "*"
    enabled: bool = True
    status_code: int = 200
    response_headers: str = "{}"
    response_body: str = ""
    match_count: int = 0
    created_at: str = ""
    updated_at: str = ""


def _to_vm(r: models.Rule) -> RuleVM:
    return RuleVM(**asdict(r))


class RulesState(rx.State):
    rules: list[RuleVM] = []

    # 过滤
    q: str = ""
    method_filter: str = "ALL"          # ALL / GET / POST ...
    enabled_only: bool = False

    # 抽屉
    drawer_open: bool = False
    editing_id: int = -1                # -1 = 新建

    # 表单字段
    f_name: str = ""
    f_url: str = ""
    f_method: str = "*"
    f_status: str = "200"
    f_headers: str = "{}"
    f_body: str = ""
    f_enabled: bool = True

    # toast
    toast_msg: str = ""
    toast_kind: str = "info"            # info/success/error

    # ---------- 计算 ----------

    @rx.var
    def filtered(self) -> list[RuleVM]:
        out = self.rules
        if self.method_filter != "ALL":
            out = [r for r in out if r.method == self.method_filter or r.method == "*"]
        if self.enabled_only:
            out = [r for r in out if r.enabled]
        if self.q.strip():
            kw = self.q.strip().lower()
            out = [r for r in out if kw in r.name.lower() or kw in r.url_pattern.lower()]
        return out

    @rx.var
    def total(self) -> int:
        return len(self.rules)

    @rx.var
    def enabled_count(self) -> int:
        return sum(1 for r in self.rules if r.enabled)

    @rx.var
    def drawer_title(self) -> str:
        return "新建规则" if self.editing_id < 0 else f"编辑规则 #{self.editing_id}"

    @rx.var
    def methods(self) -> list[str]:
        return ALLOWED_METHODS

    @rx.var
    def method_filter_options(self) -> list[str]:
        return ["ALL"] + [m for m in ALLOWED_METHODS if m != "*"] + ["*"]

    # ---------- 加载 ----------

    @rx.event
    def load(self):
        self.rules = [_to_vm(r) for r in models.list_rules()]

    # ---------- 抽屉控制 ----------

    @rx.event
    def open_create(self):
        self.editing_id = -1
        self.f_name = ""
        self.f_url = ""
        self.f_method = "*"
        self.f_status = "200"
        self.f_headers = "{}"
        self.f_body = ""
        self.f_enabled = True
        self.drawer_open = True

    @rx.event
    def open_edit(self, rule_id: int):
        r = models.get_rule(rule_id)
        if not r:
            return
        self.editing_id = r.id
        self.f_name = r.name
        self.f_url = r.url_pattern
        self.f_method = r.method
        self.f_status = str(r.status_code)
        self.f_headers = r.response_headers or "{}"
        self.f_body = r.response_body or ""
        self.f_enabled = r.enabled
        self.drawer_open = True

    @rx.event
    def close_drawer(self):
        self.drawer_open = False

    @rx.event
    def set_drawer_open(self, v: bool):
        # rx.dialog 的 on_open_change 回调
        self.drawer_open = v

    # ---------- 提交 ----------

    @rx.event
    def submit(self):
        name = self.f_name.strip()
        url = self.f_url.strip()
        if not name:
            return self._toast("名称不能为空", "error")
        if not url:
            return self._toast("URL pattern 不能为空", "error")
        # JSON headers 校验（空 = {}）
        try:
            headers_text = self.f_headers.strip() or "{}"
            obj = json.loads(headers_text)
            if not isinstance(obj, dict):
                raise ValueError
        except Exception:
            return self._toast("Headers 必须是合法 JSON 对象", "error")
        # 状态码
        try:
            status = int(self.f_status)
        except Exception:
            return self._toast("状态码必须是整数", "error")

        # 冲突检测（仅启用时）
        if self.f_enabled:
            exclude = self.editing_id if self.editing_id > 0 else None
            conflict = matcher.find_conflict(url, self.f_method, exclude_id=exclude)
            if conflict:
                return self._toast(
                    f"与规则 #{conflict.id}「{conflict.name}」冲突，请先关闭它", "error"
                )

        if self.editing_id > 0:
            models.update_rule(
                self.editing_id,
                name=name,
                url_pattern=url,
                method=self.f_method,
                status_code=status,
                response_headers=headers_text,
                response_body=self.f_body,
                enabled=self.f_enabled,
            )
            msg = "已更新"
        else:
            models.create_rule(
                name=name,
                url_pattern=url,
                method=self.f_method,
                status_code=status,
                response_headers=headers_text,
                response_body=self.f_body,
                enabled=self.f_enabled,
            )
            msg = "已创建"
        matcher.reload_from_db()
        self.drawer_open = False
        self.load()
        return self._toast(msg, "success")

    # ---------- 行操作 ----------

    @rx.event
    def toggle(self, rule_id: int):
        r = models.get_rule(rule_id)
        if not r:
            return
        if not r.enabled:
            conflict = matcher.find_conflict(r.url_pattern, r.method, exclude_id=rule_id)
            if conflict:
                return self._toast(
                    f"无法启用：与 #{conflict.id}「{conflict.name}」冲突", "error"
                )
        models.toggle_rule(rule_id)
        matcher.reload_from_db()
        self.load()

    @rx.event
    def delete(self, rule_id: int):
        models.delete_rule(rule_id)
        matcher.reload_from_db()
        self.load()
        self._toast("已删除", "success")

    # ---------- 表单字段 setter ----------

    @rx.event
    def set_q(self, v: str): self.q = v
    @rx.event
    def set_method_filter(self, v: str): self.method_filter = v
    @rx.event
    def set_enabled_only(self, v: bool): self.enabled_only = v
    @rx.event
    def set_f_name(self, v: str): self.f_name = v
    @rx.event
    def set_f_url(self, v: str): self.f_url = v
    @rx.event
    def set_f_method(self, v: str): self.f_method = v
    @rx.event
    def set_f_status(self, v: str): self.f_status = v
    @rx.event
    def set_f_headers(self, v: str): self.f_headers = v
    @rx.event
    def set_f_body(self, v: str): self.f_body = v
    @rx.event
    def set_f_enabled(self, v: bool): self.f_enabled = v

    @rx.event
    def quick_status(self, code: str):
        self.f_status = code

    @rx.event
    def format_headers(self):
        try:
            obj = json.loads(self.f_headers or "{}")
            self.f_headers = json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            self._toast("Headers 不是合法 JSON，无法格式化", "error")

    # ---------- 内部 ----------

    def _toast(self, msg: str, kind: str = "info"):
        self.toast_msg = msg
        self.toast_kind = kind
        return rx.toast(
            msg,
            duration=3000,
            close_button=True,
        ) if kind != "error" else rx.toast.error(msg, duration=4000, close_button=True)
