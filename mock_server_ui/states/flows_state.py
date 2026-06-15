"""流量页 State：背景任务订阅 flows.buffer + 过滤 + 详情。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import reflex as rx

from app import flows


@dataclass
class FlowVM:
    id: int = 0
    ts: float = 0.0
    time_str: str = ""
    method: str = ""
    url: str = ""
    status: int = 0
    duration_ms: int = 0
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None
    req_headers: dict = field(default_factory=dict)
    resp_headers: dict = field(default_factory=dict)
    req_body: str = ""
    resp_body: str = ""
    req_body_truncated: bool = False
    resp_body_truncated: bool = False


def _to_vm(rec: flows.FlowRecord) -> FlowVM:
    d = asdict(rec)
    d["time_str"] = time.strftime("%H:%M:%S", time.localtime(rec.ts))
    return FlowVM(**d)


# 上限：超过就截断（Reflex State 同步成本不低，1000 已经足够）
MAX_FLOWS = 1000


class FlowsState(rx.State):
    flows: list[FlowVM] = []

    # 过滤
    q: str = ""
    method_filter: str = "ALL"
    only_hit: bool = False

    paused: bool = False
    selected_id: int = -1

    # 控制后台任务只起一次
    _running: bool = False

    @rx.var
    def filtered(self) -> list[FlowVM]:
        out = self.flows
        if self.method_filter != "ALL":
            out = [f for f in out if f.method == self.method_filter]
        if self.only_hit:
            out = [f for f in out if f.rule_id is not None]
        if self.q.strip():
            kw = self.q.strip().lower()
            out = [f for f in out if kw in f.url.lower()]
        return out

    @rx.var
    def selected(self) -> Optional[FlowVM]:
        for f in self.flows:
            if f.id == self.selected_id:
                return f
        return None

    @rx.var
    def has_selection(self) -> bool:
        return self.selected_id > 0 and any(f.id == self.selected_id for f in self.flows)

    @rx.var
    def total(self) -> int:
        return len(self.flows)

    @rx.var
    def hit_count(self) -> int:
        return sum(1 for f in self.flows if f.rule_id is not None)

    @rx.var
    def method_filter_options(self) -> list[str]:
        return ["ALL", "GET", "POST", "PUT", "DELETE", "PATCH"]

    # ---------- 加载快照 + 启动订阅 ----------

    @rx.event
    def load(self):
        # 首屏：把缓冲区当前内容拉过来（倒序：新的在上）
        snap = list(reversed(flows.buffer.snapshot()))
        self.flows = [_to_vm(r) for r in snap[:MAX_FLOWS]]

    @rx.event(background=True)
    async def subscribe_loop(self):
        """订阅 flows.buffer，新流量来了塞 State 里。每个浏览器会话起一份。"""
        async with self:
            if self._running:
                return
            self._running = True
        queue = flows.buffer.subscribe()
        try:
            while True:
                try:
                    rec = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                async with self:
                    if self.paused:
                        continue
                    self.flows.insert(0, _to_vm(rec))
                    if len(self.flows) > MAX_FLOWS:
                        self.flows = self.flows[:MAX_FLOWS]
        finally:
            flows.buffer.unsubscribe(queue)
            async with self:
                self._running = False

    # ---------- 行操作 ----------

    @rx.event
    def select(self, flow_id: int):
        self.selected_id = flow_id

    @rx.event
    def deselect(self):
        self.selected_id = -1

    @rx.event
    def clear(self):
        flows.buffer.clear()
        self.flows = []
        self.selected_id = -1

    @rx.event
    def toggle_pause(self):
        self.paused = not self.paused

    # setters
    @rx.event
    def set_q(self, v: str): self.q = v
    @rx.event
    def set_method_filter(self, v: str): self.method_filter = v
    @rx.event
    def set_only_hit(self, v: bool): self.only_hit = v
