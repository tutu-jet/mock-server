"""HTTP method 徽章 + 状态码徽章。"""
from __future__ import annotations

import reflex as rx

from mock_server_ui.theme import METHOD_COLORS


def method_badge(method: rx.Var[str] | str, size: str = "2") -> rx.Component:
    """method 文本可能是 Var（State 来的），所以用 rx.match 而不是 dict 索引。"""
    if isinstance(method, str):
        color = METHOD_COLORS.get(method, "gray")
        return rx.badge(method, color_scheme=color, variant="solid", size=size, radius="medium")
    # Var 路径：用 match 给每种方法选色
    return rx.match(
        method,
        ("GET",    rx.badge("GET",    color_scheme="blue",   variant="solid", size=size, radius="medium")),
        ("POST",   rx.badge("POST",   color_scheme="green",  variant="solid", size=size, radius="medium")),
        ("PUT",    rx.badge("PUT",    color_scheme="amber",  variant="solid", size=size, radius="medium")),
        ("DELETE", rx.badge("DELETE", color_scheme="red",    variant="solid", size=size, radius="medium")),
        ("PATCH",  rx.badge("PATCH",  color_scheme="purple", variant="solid", size=size, radius="medium")),
        rx.badge("ANY", color_scheme="gray", variant="solid", size=size, radius="medium"),
    )


def status_badge(code: rx.Var[int] | int, size: str = "2") -> rx.Component:
    """2xx 绿 / 3xx 蓝 / 4xx 橙 / 5xx 红 / 0 灰。"""
    if isinstance(code, int):
        from mock_server_ui.theme import status_color
        return rx.badge(str(code), color_scheme=status_color(code), variant="soft", size=size)
    return rx.badge(
        code.to_string(),
        color_scheme=rx.cond(
            code < 1, "gray",
            rx.cond(
                code < 300, "green",
                rx.cond(
                    code < 400, "blue",
                    rx.cond(code < 500, "amber", "red"),
                ),
            ),
        ),
        variant="soft",
        size=size,
    )
