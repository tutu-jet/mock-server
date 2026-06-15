"""
mock-server 单入口：同进程同 event loop 同时跑 FastAPI(uvicorn) + mitmproxy(DumpMaster)
"""
import asyncio
import logging
import os
import signal

import uvicorn
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from app.web import build_app
from app.proxy_addon import MockAddon

# 配置（先硬编码，后面要改成 env / config 文件再说）
PROXY_HOST = "0.0.0.0"  # 监听所有网卡，手机才能连过来
PROXY_PORT = 9077
UI_HOST = "0.0.0.0"
UI_PORT = 9088

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("mock-server")


async def run_proxy(addon: MockAddon, stop_event: asyncio.Event) -> None:
    """启动 mitmproxy DumpMaster，stop_event 触发后优雅关闭，超时强取消。"""
    opts = Options(
        listen_host=PROXY_HOST,
        listen_port=PROXY_PORT,
    )
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(addon)
    log.info(f"代理启动: http://{PROXY_HOST}:{PROXY_PORT}")
    run_task = asyncio.create_task(master.run())
    try:
        await stop_event.wait()
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


async def run_ui(addon: MockAddon, stop_event: asyncio.Event) -> None:
    """启动 FastAPI / uvicorn，stop_event 触发后优雅关闭，超时 force_exit。"""
    app = build_app(addon)
    config = uvicorn.Config(
        app,
        host=UI_HOST,
        port=UI_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # 不让 uvicorn 抢 SIGINT/SIGTERM，由 main 统一调度
    server.install_signal_handlers = lambda: None
    log.info(f"UI 启动: http://{UI_HOST}:{UI_PORT}")
    serve_task = asyncio.create_task(server.serve())
    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(serve_task), timeout=1.5)
        except asyncio.TimeoutError:
            server.force_exit = True
            try:
                await serve_task
            except Exception:
                pass


async def main() -> None:
    # addon 实例在 UI 和代理之间共享：UI 改规则后，addon 内存里的匹配器直接刷新
    addon = MockAddon()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    hit_count = {"n": 0}

    def _request_stop() -> None:
        hit_count["n"] += 1
        if hit_count["n"] == 1:
            log.info("收到信号，正在退出（再按一次 Ctrl+C 强制退出）…")
            stop_event.set()
        else:
            log.warning("强制退出")
            os._exit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows 上 add_signal_handler 不支持，退化到默认 KeyboardInterrupt 流程
            pass

    await asyncio.gather(
        run_proxy(addon, stop_event),
        run_ui(addon, stop_event),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，退出")
