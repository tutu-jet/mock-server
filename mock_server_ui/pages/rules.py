"""规则页：卡片网格 + 工具条 + 编辑抽屉。"""
from __future__ import annotations

import reflex as rx

from mock_server_ui.components.badges import method_badge, status_badge
from mock_server_ui.components.layout import layout
from mock_server_ui.states.rules_state import RulesState, RuleVM


# ---------- 单张卡片 ----------

def _rule_card(r: RuleVM) -> rx.Component:
    return rx.box(
        rx.vstack(
            # 头：method + 名称 + 开关
            rx.hstack(
                method_badge(r.method),
                rx.text(
                    r.name,
                    size="3",
                    weight="bold",
                    color=rx.color("gray", 12),
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                    flex_grow="1",
                ),
                rx.switch(
                    checked=r.enabled,
                    on_change=lambda _v: RulesState.toggle(r.id),
                    color_scheme="iris",
                    size="2",
                ),
                spacing="3",
                align="center",
                width="100%",
            ),
            # URL pattern
            rx.box(
                rx.text(
                    r.url_pattern,
                    size="1",
                    font_family="JetBrains Mono, ui-monospace, monospace",
                    color=rx.color("gray", 11),
                    overflow="hidden",
                    text_overflow="ellipsis",
                    white_space="nowrap",
                ),
                background=rx.color("gray", 3),
                padding="8px 10px",
                border_radius="8px",
                width="100%",
            ),
            # 数据行：状态码 / 命中次数
            rx.hstack(
                rx.hstack(
                    rx.text("状态", size="1", color=rx.color("gray", 10)),
                    status_badge(r.status_code),
                    spacing="2",
                    align="center",
                ),
                rx.hstack(
                    rx.icon("target", size=14, color=rx.color("gray", 10)),
                    rx.text(r.match_count, size="1", color=rx.color("gray", 11)),
                    spacing="1",
                    align="center",
                ),
                rx.spacer(),
                rx.hstack(
                    rx.icon_button(
                        rx.icon("pencil", size=14),
                        on_click=RulesState.open_edit(r.id),
                        variant="soft",
                        color_scheme="gray",
                        size="1",
                    ),
                    rx.alert_dialog.root(
                        rx.alert_dialog.trigger(
                            rx.icon_button(
                                rx.icon("trash-2", size=14),
                                variant="soft",
                                color_scheme="red",
                                size="1",
                            ),
                        ),
                        rx.alert_dialog.content(
                            rx.alert_dialog.title("删除规则"),
                            rx.alert_dialog.description(
                                f"确认删除「{r.name}」？此操作不可恢复。"
                            ),
                            rx.hstack(
                                rx.alert_dialog.cancel(rx.button("取消", variant="soft", color_scheme="gray")),
                                rx.alert_dialog.action(
                                    rx.button(
                                        "删除",
                                        on_click=RulesState.delete(r.id),
                                        color_scheme="red",
                                    ),
                                ),
                                spacing="3",
                                justify="end",
                                margin_top="16px",
                            ),
                            max_width="380px",
                        ),
                    ),
                    spacing="2",
                ),
                spacing="3",
                align="center",
                width="100%",
            ),
            spacing="3",
            align="start",
            width="100%",
        ),
        padding="16px",
        border_radius="14px",
        border=f"1px solid {rx.color('gray', 4)}",
        background=rx.color("gray", 2),
        opacity=rx.cond(r.enabled, "1", "0.55"),
        _hover={
            "border_color": rx.color("iris", 7),
            "transform": "translateY(-1px)",
            "box_shadow": "0 4px 16px rgba(0,0,0,0.08)",
        },
        transition="all 0.15s",
        width="100%",
    )


# ---------- 工具条 ----------

def _toolbar() -> rx.Component:
    return rx.hstack(
        rx.input(
            rx.input.slot(rx.icon("search", size=16)),
            placeholder="搜索名称或 URL pattern...",
            value=RulesState.q,
            on_change=RulesState.set_q,
            size="2",
            width="320px",
        ),
        rx.select(
            RulesState.method_filter_options,
            value=RulesState.method_filter,
            on_change=RulesState.set_method_filter,
            size="2",
        ),
        rx.hstack(
            rx.switch(
                checked=RulesState.enabled_only,
                on_change=RulesState.set_enabled_only,
                color_scheme="iris",
                size="1",
            ),
            rx.text("仅显示启用", size="2", color=rx.color("gray", 11)),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        rx.text(
            f"共 {RulesState.total} 条 · 启用 {RulesState.enabled_count}",
            size="2",
            color=rx.color("gray", 10),
        ),
        rx.button(
            rx.icon("plus", size=16),
            "新建规则",
            on_click=RulesState.open_create,
            color_scheme="iris",
            size="2",
        ),
        spacing="3",
        align="center",
        width="100%",
        padding="14px",
        background=rx.color("gray", 2),
        border=f"1px solid {rx.color('gray', 4)}",
        border_radius="12px",
        margin_bottom="20px",
    )


# ---------- 抽屉表单 ----------

def _quick_status_chip(code: str) -> rx.Component:
    return rx.button(
        code,
        on_click=lambda: RulesState.quick_status(code),
        variant="soft",
        color_scheme="gray",
        size="1",
    )


def _field(label: str, *children, hint: str | None = None) -> rx.Component:
    """统一表单字段：标签 + 可选 hint + 内容。"""
    head = rx.hstack(
        rx.text(label, size="2", weight="medium", color=rx.color("gray", 12)),
        *(
            [rx.text(hint, size="1", color=rx.color("gray", 10))]
            if hint
            else []
        ),
        spacing="2",
        align="center",
    )
    return rx.vstack(
        head,
        *children,
        spacing="2",
        align="stretch",
        width="100%",
    )


def _drawer() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                # 标题栏：标题 + 启用开关 + 关闭
                rx.hstack(
                    rx.dialog.title(
                        RulesState.drawer_title,
                        margin="0",
                    ),
                    rx.spacer(),
                    rx.hstack(
                        rx.text("启用", size="2", color=rx.color("gray", 11)),
                        rx.switch(
                            checked=RulesState.f_enabled,
                            on_change=RulesState.set_f_enabled,
                            color_scheme="iris",
                            size="2",
                        ),
                        spacing="2",
                        align="center",
                    ),
                    rx.dialog.close(
                        rx.icon_button(
                            rx.icon("x", size=16),
                            variant="ghost",
                            color_scheme="gray",
                        ),
                    ),
                    width="100%",
                    align="center",
                    spacing="3",
                ),
                rx.divider(),
                # 名称
                _field(
                    "名称",
                    rx.input(
                        value=RulesState.f_name,
                        on_change=RulesState.set_f_name,
                        placeholder="给这条规则起个名字",
                        size="3",
                        width="100%",
                    ),
                ),
                # URL pattern
                _field(
                    "URL Pattern",
                    rx.input(
                        value=RulesState.f_url,
                        on_change=RulesState.set_f_url,
                        placeholder="例如 https://api.example.com/user/*",
                        size="3",
                        font_family="JetBrains Mono, monospace",
                        width="100%",
                    ),
                    hint="支持 * 通配",
                ),
                # 方法 + 状态码（两列等宽）
                rx.grid(
                    _field(
                        "方法",
                        rx.select(
                            RulesState.methods,
                            value=RulesState.f_method,
                            on_change=RulesState.set_f_method,
                            size="3",
                            width="100%",
                        ),
                    ),
                    _field(
                        "状态码",
                        rx.input(
                            value=RulesState.f_status,
                            on_change=RulesState.set_f_status,
                            size="3",
                            width="100%",
                        ),
                    ),
                    columns="2",
                    spacing="4",
                    width="100%",
                ),
                # 快捷状态码
                rx.hstack(
                    rx.text(
                        "常用",
                        size="1",
                        color=rx.color("gray", 10),
                        margin_right="4px",
                    ),
                    _quick_status_chip("200"),
                    _quick_status_chip("204"),
                    _quick_status_chip("400"),
                    _quick_status_chip("401"),
                    _quick_status_chip("404"),
                    _quick_status_chip("500"),
                    spacing="2",
                    wrap="wrap",
                    align="center",
                    width="100%",
                ),
                # Headers
                _field(
                    "Response Headers (JSON)",
                    rx.text_area(
                        value=RulesState.f_headers,
                        on_change=RulesState.set_f_headers,
                        placeholder='{"Content-Type": "application/json"}',
                        rows="4",
                        font_family="JetBrains Mono, monospace",
                        style={"font_size": "13px"},
                        width="100%",
                    ),
                    rx.hstack(
                        rx.spacer(),
                        rx.button(
                            rx.icon("braces", size=14),
                            "格式化",
                            on_click=RulesState.format_headers,
                            size="1",
                            variant="soft",
                            color_scheme="gray",
                        ),
                        width="100%",
                    ),
                ),
                # Body
                _field(
                    "Response Body",
                    rx.text_area(
                        value=RulesState.f_body,
                        on_change=RulesState.set_f_body,
                        placeholder="返回的 body 文本，可以是 JSON 字符串或纯文本",
                        rows="8",
                        font_family="JetBrains Mono, monospace",
                        style={"font_size": "13px"},
                        width="100%",
                    ),
                ),
                rx.divider(),
                # 底栏按钮
                rx.hstack(
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button(
                            "取消",
                            variant="soft",
                            color_scheme="gray",
                            size="2",
                        ),
                    ),
                    rx.button(
                        rx.icon("check", size=16),
                        "保存",
                        on_click=RulesState.submit,
                        color_scheme="iris",
                        size="2",
                    ),
                    spacing="3",
                    width="100%",
                    align="center",
                ),
                spacing="4",
                width="100%",
                align="stretch",
            ),
            max_width="640px",
            style={"max_height": "85vh", "overflow_y": "auto"},
        ),
        open=RulesState.drawer_open,
        on_open_change=RulesState.set_drawer_open,
    )


# ---------- 页面 ----------

def _empty_state() -> rx.Component:
    return rx.vstack(
        rx.icon("inbox", size=48, color=rx.color("gray", 8)),
        rx.text("还没有规则", size="3", color=rx.color("gray", 11)),
        rx.text("点击右上角「新建规则」开始添加", size="2", color=rx.color("gray", 10)),
        rx.button(
            rx.icon("plus", size=16),
            "新建规则",
            on_click=RulesState.open_create,
            color_scheme="iris",
            size="2",
            margin_top="12px",
        ),
        spacing="3",
        align="center",
        padding="80px 0",
    )


def rules_page() -> rx.Component:
    return layout(
        _toolbar(),
        rx.cond(
            RulesState.total == 0,
            _empty_state(),
            rx.grid(
                rx.foreach(RulesState.filtered, _rule_card),
                columns=rx.breakpoints(initial="1", sm="1", md="2", lg="3"),
                spacing="4",
                width="100%",
            ),
        ),
        _drawer(),
        title="规则",
        subtitle="匹配命中的请求会被改写返回",
    )
