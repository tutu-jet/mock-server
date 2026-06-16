"""流量页：实时列表 + 详情面板。"""
from __future__ import annotations

import reflex as rx

from mock_server_ui.components.badges import method_badge, status_badge
from mock_server_ui.components.layout import layout
from mock_server_ui.states.flows_state import FlowsState, FlowVM
from mock_server_ui.states.rules_state import RulesState


def _flow_row(f: FlowVM) -> rx.Component:
    is_hit = f.rule_id != None  # noqa: E711  Var 比较
    is_selected = FlowsState.selected_id == f.id
    return rx.box(
        rx.hstack(
            # 命中标 / 时间
            rx.box(
                width="3px",
                height="36px",
                background=rx.cond(is_hit, rx.color("iris", 9), "transparent"),
                border_radius="2px",
            ),
            rx.text(
                f.time_str,
                size="1",
                color=rx.color("gray", 10),
                font_family="JetBrains Mono, monospace",
                width="70px",
            ),
            method_badge(f.method),
            status_badge(f.status),
            rx.text(
                rx.cond(f.duration_ms > 0, f.duration_ms.to_string() + "ms", "-"),
                size="1",
                color=rx.color("gray", 10),
                width="60px",
            ),
            rx.text(
                f.url,
                size="2",
                color=rx.color("gray", 12),
                overflow="hidden",
                text_overflow="ellipsis",
                white_space="nowrap",
                flex_grow="1",
                font_family="JetBrains Mono, monospace",
            ),
            rx.cond(
                is_hit,
                rx.badge(
                    rx.icon("target", size=12),
                    f.rule_name,
                    color_scheme="iris",
                    variant="soft",
                    size="1",
                ),
                rx.fragment(),
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        on_click=FlowsState.select(f.id),
        padding="10px 14px",
        border_radius="8px",
        background=rx.cond(is_selected, rx.color("iris", 3), "transparent"),
        border=rx.cond(
            is_selected,
            f"1px solid {rx.color('iris', 7)}",
            f"1px solid transparent",
        ),
        cursor="pointer",
        _hover={"background": rx.color("gray", 3)},
        transition="background 0.1s",
        width="100%",
    )


def _toolbar() -> rx.Component:
    return rx.hstack(
        rx.input(
            rx.input.slot(rx.icon("search", size=16)),
            placeholder="搜索 URL...",
            value=FlowsState.q,
            on_change=FlowsState.set_q,
            size="2",
            width="320px",
        ),
        rx.select(
            FlowsState.method_filter_options,
            value=FlowsState.method_filter,
            on_change=FlowsState.set_method_filter,
            size="2",
        ),
        rx.hstack(
            rx.switch(
                checked=FlowsState.only_hit,
                on_change=FlowsState.set_only_hit,
                color_scheme="iris",
                size="1",
            ),
            rx.text("仅命中规则", size="2", color=rx.color("gray", 11)),
            spacing="2",
            align="center",
        ),
        rx.spacer(),
        rx.text(
            f"共 {FlowsState.total} 条 · 命中 {FlowsState.hit_count}",
            size="2",
            color=rx.color("gray", 10),
        ),
        rx.button(
            rx.cond(FlowsState.paused, rx.icon("play", size=14), rx.icon("pause", size=14)),
            rx.cond(FlowsState.paused, "继续", "暂停"),
            on_click=FlowsState.toggle_pause,
            color_scheme=rx.cond(FlowsState.paused, "amber", "gray"),
            variant="soft",
            size="2",
        ),
        rx.button(
            rx.icon("trash-2", size=14),
            "清空",
            on_click=FlowsState.clear,
            variant="soft",
            color_scheme="red",
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


# ---------- 详情抽屉 ----------

def _kv_table(d) -> rx.Component:
    return rx.vstack(
        rx.foreach(
            d,
            lambda kv: rx.hstack(
                rx.text(
                    kv[0],
                    size="1",
                    weight="medium",
                    color=rx.color("gray", 11),
                    font_family="JetBrains Mono, monospace",
                    width="180px",
                    flex_shrink="0",
                ),
                rx.text(
                    kv[1],
                    size="1",
                    color=rx.color("gray", 12),
                    font_family="JetBrains Mono, monospace",
                    style={"word_break": "break-all"},
                ),
                spacing="3",
                align="start",
                width="100%",
                padding="6px 0",
                border_bottom=f"1px solid {rx.color('gray', 4)}",
            ),
        ),
        spacing="0",
        width="100%",
    )


def _detail_drawer() -> rx.Component:
    f = FlowsState.selected
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.hstack(
                    rx.dialog.title("流量详情"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon_button(rx.icon("x", size=16), variant="ghost", color_scheme="gray"),
                    ),
                    width="100%",
                    align="center",
                ),
                rx.cond(
                    FlowsState.has_selection,
                    rx.vstack(
                        # 摘要条
                        rx.hstack(
                            method_badge(f.method),
                            status_badge(f.status),
                            rx.text(f.time_str, size="1", color=rx.color("gray", 10)),
                            rx.text(f.duration_ms.to_string() + "ms", size="1", color=rx.color("gray", 10)),
                            rx.cond(
                                f.rule_id != None,  # noqa: E711
                                rx.badge(
                                    rx.icon("target", size=12),
                                    f.rule_name,
                                    color_scheme="iris",
                                    variant="soft",
                                ),
                                rx.fragment(),
                            ),
                            rx.spacer(),
                            rx.button(
                                rx.icon("save", size=14),
                                "另存为规则",
                                on_click=[
                                    FlowsState.deselect,
                                    RulesState.open_create_from_flow(
                                        f.method, f.url, f.resp_body
                                    ),
                                ],
                                color_scheme="iris",
                                variant="soft",
                                size="2",
                            ),
                            spacing="3",
                            align="center",
                            width="100%",
                        ),
                        # URL
                        rx.box(
                            rx.text(
                                f.url,
                                size="2",
                                font_family="JetBrains Mono, monospace",
                                style={"word_break": "break-all"},
                            ),
                            background=rx.color("gray", 3),
                            padding="10px 12px",
                            border_radius="8px",
                            width="100%",
                        ),
                        # Tabs
                        rx.tabs.root(
                            rx.tabs.list(
                                rx.tabs.trigger("请求头", value="req_h"),
                                rx.tabs.trigger("请求体", value="req_b"),
                                rx.tabs.trigger("响应头", value="resp_h"),
                                rx.tabs.trigger("响应体", value="resp_b"),
                            ),
                            rx.tabs.content(_kv_table(f.req_headers), value="req_h", padding_top="14px"),
                            rx.tabs.content(
                                rx.box(
                                    rx.code_block(
                                        rx.cond(f.req_body == "", "(空)", f.req_body),
                                        language="json",
                                        wrap_long_lines=True,
                                        font_size="12px",
                                    ),
                                    rx.cond(
                                        f.req_body_truncated,
                                        rx.text("（已截断）", size="1", color="amber"),
                                        rx.fragment(),
                                    ),
                                ),
                                value="req_b",
                                padding_top="14px",
                            ),
                            rx.tabs.content(_kv_table(f.resp_headers), value="resp_h", padding_top="14px"),
                            rx.tabs.content(
                                rx.box(
                                    rx.code_block(
                                        rx.cond(f.resp_body == "", "(空)", f.resp_body),
                                        language="json",
                                        wrap_long_lines=True,
                                        font_size="12px",
                                    ),
                                    rx.cond(
                                        f.resp_body_truncated,
                                        rx.text("（已截断）", size="1", color="amber"),
                                        rx.fragment(),
                                    ),
                                ),
                                value="resp_b",
                                padding_top="14px",
                            ),
                            default_value="resp_b",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    rx.text("流量已过期", size="2", color=rx.color("gray", 10)),
                ),
                spacing="3",
                width="100%",
            ),
            max_width="800px",
            style={"max_height": "85vh", "overflow_y": "auto"},
        ),
        open=FlowsState.has_selection,
        on_open_change=lambda _v: FlowsState.deselect(),
    )


def _empty() -> rx.Component:
    return rx.vstack(
        rx.icon("radio", size=48, color=rx.color("gray", 8)),
        rx.text("等待第一条流量...", size="3", color=rx.color("gray", 11)),
        rx.text("把手机代理指向这台机器的 9077，访问网络即可", size="2", color=rx.color("gray", 10)),
        spacing="3",
        align="center",
        padding="80px 0",
    )


def flows_page() -> rx.Component:
    return layout(
        _toolbar(),
        rx.cond(
            FlowsState.total == 0,
            _empty(),
            rx.vstack(
                rx.foreach(FlowsState.filtered, _flow_row),
                spacing="1",
                width="100%",
                padding="6px",
                background=rx.color("gray", 2),
                border=f"1px solid {rx.color('gray', 4)}",
                border_radius="12px",
            ),
        ),
        _detail_drawer(),
        title="流量",
        subtitle="实时显示经过代理的请求",
    )
