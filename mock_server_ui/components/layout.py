"""主布局：左侧栏 + 顶栏 + 内容区。
所有页面用 layout(content) 包一层。
"""
from __future__ import annotations

import reflex as rx


# ---------- 侧栏 ----------

def _nav_item(icon: str, label: str, href: str) -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.icon(icon, size=18),
            rx.text(label, size="3", weight="medium"),
            spacing="3",
            align="center",
            width="100%",
            padding="10px 14px",
            border_radius="10px",
            _hover={"background_color": rx.color("gray", 4)},
            transition="background-color 0.15s",
        ),
        href=href,
        underline="none",
        color=rx.color("gray", 12),
        width="100%",
    )


def _sidebar() -> rx.Component:
    return rx.vstack(
        # 顶部 logo
        rx.hstack(
            rx.box(
                rx.icon("zap", size=20, color="white"),
                background=f"linear-gradient(135deg, {rx.color('iris', 9)}, {rx.color('cyan', 9)})",
                padding="8px",
                border_radius="10px",
            ),
            rx.vstack(
                rx.text("mock-server", size="3", weight="bold"),
                rx.text("丐版 Charles", size="1", color=rx.color("gray", 10)),
                spacing="0",
                align="start",
            ),
            spacing="3",
            align="center",
            padding="20px 14px 18px",
        ),
        rx.divider(),
        # 导航
        rx.vstack(
            _nav_item("list-checks", "规则", "/"),
            _nav_item("activity", "流量", "/flows"),
            _nav_item("settings", "设置", "/settings"),
            spacing="1",
            padding="12px 10px",
            width="100%",
        ),
        rx.spacer(),
        # 底部状态
        rx.vstack(
            rx.divider(),
            rx.hstack(
                rx.box(
                    width="8px",
                    height="8px",
                    border_radius="50%",
                    background=rx.color("green", 9),
                    box_shadow=f"0 0 8px {rx.color('green', 9)}",
                ),
                rx.text("代理在线 :9077", size="1", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
            ),
            rx.hstack(
                rx.box(
                    width="8px",
                    height="8px",
                    border_radius="50%",
                    background=rx.color("iris", 9),
                ),
                rx.text("UI :9088", size="1", color=rx.color("gray", 11)),
                spacing="2",
                align="center",
            ),
            spacing="2",
            padding="14px 18px 18px",
            width="100%",
            align="start",
        ),
        height="100vh",
        width="240px",
        background=rx.color("gray", 2),
        border_right=f"1px solid {rx.color('gray', 4)}",
        spacing="0",
        align="stretch",
        position="fixed",
        left="0",
        top="0",
    )


# ---------- 顶栏 ----------

def _topbar(title: str, subtitle: str = "") -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.heading(title, size="6", weight="bold"),
            rx.cond(
                subtitle != "",
                rx.text(subtitle, size="2", color=rx.color("gray", 10)),
                rx.fragment(),
            ),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.color_mode.button(),
        width="100%",
        padding="20px 28px",
        border_bottom=f"1px solid {rx.color('gray', 4)}",
        background=rx.color("gray", 1),
        align="center",
    )


# ---------- 主入口 ----------

def layout(*content: rx.Component, title: str = "", subtitle: str = "") -> rx.Component:
    return rx.box(
        _sidebar(),
        rx.box(
            _topbar(title, subtitle),
            rx.box(
                *content,
                padding="28px",
                width="100%",
                max_width="1400px",
                margin="0 auto",
            ),
            margin_left="240px",
            min_height="100vh",
            background=rx.color("gray", 1),
        ),
        width="100%",
        background=rx.color("gray", 1),
    )
