"""mock-server 启动入口。

UI 由 Reflex 接管（端口 9088 前端 + 9089 后端 WS）。
mitmproxy 代理由 Reflex 后端 FastAPI 的 startup 钩子起在同一个 event loop。
所以这里只需要拉起 reflex。

运行：
    python main.py        # 等价于 reflex run --env prod
    python main.py dev    # 等价于 reflex run（开发模式，热重载）
"""
import os
import subprocess
import sys


def main() -> None:
    args = sys.argv[1:]
    cmd = [sys.executable, "-m", "reflex", "run"]
    if not args or args[0] != "dev":
        cmd += ["--env", "prod"]
    # 保证 cwd 是项目根（rxconfig.py 在这里）
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
