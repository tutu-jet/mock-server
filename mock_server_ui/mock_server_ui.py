"""mock_server_ui.mock_server_ui - rx.App 入口。

约定：app_name == 包名 == 此模块名（rxconfig.py 里写的 mock_server_ui）。

干两件事：
1. 注册三个页面（/、/flows、/settings）
2. 通过 Reflex 0.9 的 register_lifespan_task（asynccontextmanager 形式）
   在后端启动/关闭时拉起/收掉 mitmproxy DumpMaster，跟 UI 共一个 event loop
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import reflex as rx
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from app.db import init_db
from app.matcher import matcher
from app.proxy_addon import MockAddon
from mock_server_ui.pages.flows import flows_page
from mock_server_ui.pages.rules import rules_page
from mock_server_ui.pages.settings import settings_page
from mock_server_ui.states.flows_state import FlowsState
from mock_server_ui.states.rules_state import RulesState

PROXY_HOST = "0.0.0.0"
PROXY_PORT = 9077

log = logging.getLogger("mock-server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# ---------- rx.App ----------

# theme 已通过 rxconfig.py 的 RadixThemesPlugin 配置，这里不再传 theme=
app = rx.App()

app.add_page(
    rules_page,
    route="/",
    title="规则 · mock-server",
    on_load=RulesState.load,
)
app.add_page(
    flows_page,
    route="/flows",
    title="流量 · mock-server",
    on_load=[FlowsState.load, FlowsState.subscribe_loop],
)
app.add_page(
    settings_page,
    route="/settings",
    title="设置 · mock-server",
)


# ---------- mitmproxy 生命周期 ----------

@app.register_lifespan_task
@contextlib.asynccontextmanager
async def proxy_lifespan():
    """跟 Reflex 后端共生命周期：startup 起代理，shutdown 关代理。"""
    init_db()
    matcher.reload_from_db()
    addon = MockAddon()
    opts = Options(listen_host=PROXY_HOST, listen_port=PROXY_PORT)
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(addon)
    run_task = asyncio.create_task(master.run(), name="mitmproxy_master")
    log.info(f"代理启动: http://{PROXY_HOST}:{PROXY_PORT}")
    try:
        yield
    finally:
        master.shutdown()
        try:
            await asyncio.wait_for(run_task, timeout=1.0)
        except asyncio.TimeoutError:
            run_task.cancel()
            try:
                await run_task
            except (asyncio.CancelledError, Exception):
                pass
        log.info("代理已停止")
