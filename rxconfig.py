"""Reflex 配置。
- app_name 必须等于包目录名 mock_server_ui
- frontend_port: 浏览器访问的端口（取代旧 FastAPI UI 的 9088）
- backend_port: Reflex 后端 WS/事件，前端自动连

注意：本文件不要 import mock_server_ui.* 任何子模块——
Reflex 加载 app 时反向需要 get_config()，会触发循环 import。
所以主题 token 在这里直接写字面量，跟 mock_server_ui/theme.py 保持一致。
"""
import reflex as rx
from reflex_base.plugins.sitemap import SitemapPlugin
from reflex_components_radix.plugin import RadixThemesPlugin

config = rx.Config(
    app_name="mock_server_ui",
    frontend_port=9088,
    backend_port=9089,
    backend_host="0.0.0.0",
    show_built_with_reflex=False,
    disable_plugins=[SitemapPlugin],
    plugins=[
        RadixThemesPlugin(
            theme=rx.theme(
                appearance="dark",
                has_background=True,
                radius="large",
                accent_color="iris",
                gray_color="slate",
                scaling="100%",
            ),
        ),
    ],
)
