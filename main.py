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


class _SuppressShutdownCancel(logging.Filter):
    """退出时若还有 SSE 等长连接，uvicorn graceful 超时会 cancel 它的
    listen_for_disconnect 任务，starlette 把这条 CancelledError 当成
    "Exception in ASGI application" 打一整条栈。这是框架对未关闭流式连接的
    固有行为、进程仍 exit=0 正常退出，并非故障，过滤掉以免误导。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc = record.exc_info[1]
            if isinstance(exc, asyncio.CancelledError) and (
                "graceful shutdown" in str(exc)
            ):
                return False
        return True


logging.getLogger("uvicorn.error").addFilter(_SuppressShutdownCancel())


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

    # shutdown 时 set，供 SSE 等长连接的生成器及时退出循环，
    # 否则它们会卡在 queue.get() 上直到 graceful 超时被强制 cancel（刷栈）。
    app.state.shutting_down = asyncio.Event()

    try:
        yield
    finally:
        app.state.shutting_down.set()
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
    # 信号交给 uvicorn 自己管：第一次 Ctrl+C 走 graceful shutdown，
    # 第二次直接退出。不要自己装 SIGINT handler——uvicorn 的 capture_signals
    # 退出时会恢复原 handler，在 asyncio 回调里 raise KeyboardInterrupt
    # 没人接，只会刷一大堆未处理异常栈。
    uvicorn.run(
        "main:app",
        host=UI_HOST,
        port=UI_PORT,
        reload=False,
        log_level="info",
        timeout_graceful_shutdown=3,  # 最多等 3 秒就强制收摊
    )
