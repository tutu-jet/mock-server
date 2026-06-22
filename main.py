"""mock-server 单进程入口。

一个 Python 进程同时跑：
- FastAPI UI (9088) — Jinja2 服务端渲染 + HTMX
- mitmproxy 代理 (9077) — lifespan 任务，跟 UI 共一个 event loop

运行：
    python main.py
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from app.db import init_db
from app.matcher import matcher
from app.proxy_addon import MockAddon
from web.routes import router

PROXY_HOST = "0.0.0.0"
PROXY_PORT = 9077
UI_HOST = "0.0.0.0"
UI_PORT = 9088

log = logging.getLogger("mock-server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """startup: 起代理；shutdown: 收代理。"""
    init_db()
    matcher.reload_from_db()

    addon = MockAddon()
    opts = Options(listen_host=PROXY_HOST, listen_port=PROXY_PORT)
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(addon)
    proxy_task = asyncio.create_task(master.run(), name="mitmproxy_master")
    log.info(f"代理启动: http://{PROXY_HOST}:{PROXY_PORT}")
    log.info(f"UI 启动:   http://{UI_HOST}:{UI_PORT}")

    try:
        yield
    finally:
        master.shutdown()
        try:
            await asyncio.wait_for(proxy_task, timeout=2.0)
        except asyncio.TimeoutError:
            proxy_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await proxy_task
        log.info("代理已停止")


app = FastAPI(lifespan=lifespan, title="mock-server")
app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=UI_HOST,
        port=UI_PORT,
        reload=False,
        log_level="info",
    )
