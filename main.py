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
import os
import signal

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from app.db import init_db
from app.matcher import matcher
from app.proxy_addon import MockAddon
from app import models
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
# 被 Pin 的 App / 不信任用户证书的 App 会刷 "Client TLS handshake failed" WARNING，
# 这是正常现象，不是 mock-server 故障。把 mitmproxy 自身的 server 日志压到 ERROR。
logging.getLogger("mitmproxy.proxy.server").setLevel(logging.ERROR)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """startup: 起代理；shutdown: 收代理。"""
    init_db()
    # 流量 body 是会话级数据，重启清空，避免与内存环形缓冲不一致
    models.clear_flow_bodies()
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
    # 第二次 Ctrl+C 直接退出，不再等 uvicorn graceful shutdown
    _interrupts = {"n": 0}

    def _sigint(signum, frame):
        _interrupts["n"] += 1
        if _interrupts["n"] >= 2:
            log.warning("再次收到 Ctrl+C，强制退出")
            os._exit(130)
        # 第一次保留默认行为（让 uvicorn 走 graceful）
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint)

    uvicorn.run(
        "main:app",
        host=UI_HOST,
        port=UI_PORT,
        reload=False,
        log_level="info",
        timeout_graceful_shutdown=3,  # 最多等 3 秒就强制收摊
    )
