"""设置/帮助页：端口信息 + CA 证书 + 配代理引导。"""
from __future__ import annotations

import socket

import reflex as rx

from mock_server_ui.components.layout import layout


def _local_ip() -> str:
    """尽力拿一个局域网 IP 给手机用，失败返回 0.0.0.0。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def _info_card(icon: str, title: str, value: str, copyable: bool = False) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.box(
                rx.icon(icon, size=18, color="white"),
                background=f"linear-gradient(135deg, {rx.color('iris', 9)}, {rx.color('cyan', 9)})",
                padding="10px",
                border_radius="10px",
            ),
            rx.vstack(
                rx.text(title, size="1", color=rx.color("gray", 10)),
                rx.text(
                    value,
                    size="3",
                    weight="bold",
                    font_family="JetBrains Mono, monospace",
                ),
                spacing="0",
                align="start",
            ),
            rx.spacer(),
            rx.cond(
                copyable,
                rx.icon_button(
                    rx.icon("copy", size=14),
                    on_click=rx.set_clipboard(value),
                    variant="soft",
                    color_scheme="gray",
                    size="2",
                ),
                rx.fragment(),
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        padding="16px",
        border_radius="12px",
        border=f"1px solid {rx.color('gray', 4)}",
        background=rx.color("gray", 2),
        width="100%",
    )


def _step(num: int, title: str, body: rx.Component) -> rx.Component:
    return rx.hstack(
        rx.box(
            rx.text(str(num), size="3", weight="bold", color="white"),
            background=rx.color("iris", 9),
            width="32px",
            height="32px",
            border_radius="50%",
            display="flex",
            align_items="center",
            justify_content="center",
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(title, size="3", weight="bold"),
            body,
            spacing="2",
            align="start",
            flex_grow="1",
        ),
        spacing="3",
        align="start",
        width="100%",
        padding="12px 0",
    )


def settings_page() -> rx.Component:
    ip = _local_ip()
    return layout(
        rx.vstack(
            # 信息卡片网格
            rx.grid(
                _info_card("globe", "本机 IP（手机连这个）", ip, copyable=True),
                _info_card("plug-zap", "代理端口", "9077"),
                _info_card("monitor", "UI 端口", "9088"),
                columns="3",
                spacing="4",
                width="100%",
            ),
            # 配代理步骤
            rx.box(
                rx.vstack(
                    rx.hstack(
                        rx.icon("smartphone", size=20, color=rx.color("iris", 11)),
                        rx.heading("手机抓 HTTPS 包步骤", size="4"),
                        spacing="2",
                        align="center",
                    ),
                    rx.divider(),
                    _step(
                        1, "WiFi 设置代理",
                        rx.text(
                            f"WiFi → 当前网络 → 高级 → 代理 → 手动；主机名填 {ip}，端口 9077",
                            size="2",
                            color=rx.color("gray", 11),
                        ),
                    ),
                    _step(
                        2, "下载 CA 证书",
                        rx.hstack(
                            rx.text("浏览器访问", size="2", color=rx.color("gray", 11)),
                            rx.link(
                                "http://mitm.it",
                                href="http://mitm.it",
                                color=rx.color("iris", 11),
                                weight="medium",
                            ),
                            rx.text("，下载并安装", size="2", color=rx.color("gray", 11)),
                            spacing="1",
                            align="center",
                            wrap="wrap",
                        ),
                    ),
                    _step(
                        3, "信任用户 CA（Android 7+）",
                        rx.vstack(
                            rx.text(
                                "自己的 app：在 network_security_config.xml 加 <certificates src=\"user\"/>",
                                size="2",
                                color=rx.color("gray", 11),
                            ),
                            rx.text(
                                "第三方 app：需要 root 把 CA 装到系统层",
                                size="2",
                                color=rx.color("gray", 11),
                            ),
                            spacing="1",
                            align="start",
                        ),
                    ),
                    _step(
                        4, "开始抓包",
                        rx.text(
                            "回到本工具的「流量」页，手机访问网络就能看到流量了",
                            size="2",
                            color=rx.color("gray", 11),
                        ),
                    ),
                    spacing="2",
                    align="start",
                    width="100%",
                ),
                padding="20px",
                border_radius="14px",
                border=f"1px solid {rx.color('gray', 4)}",
                background=rx.color("gray", 2),
                width="100%",
                margin_top="24px",
            ),
            spacing="0",
            width="100%",
        ),
        title="设置 / 帮助",
        subtitle="端口、证书、配代理",
    )
