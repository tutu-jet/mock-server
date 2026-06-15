# mock-server

丐版 Charles：本地代理 + Web UI 改 HTTP/HTTPS 返回值，专为安卓真机调试做的。

## 技术栈
- 代理内核：[mitmproxy](https://mitmproxy.org/)
- Web UI：[Reflex](https://reflex.dev/)（纯 Python 写的现代化 UI，Radix UI 主题）
- DB：SQLite（规则持久化）
- 流量：内存环形缓冲 1000 条 + Reflex 后台任务实时推送
- 部署：Reflex 进程统一调度，mitmproxy 在同一个 asyncio loop 启动

## 端口
- 代理：`9077`（手机 WiFi 设置代理填这个）
- UI：`9088`（浏览器打开 http://localhost:9088）
- Reflex 后端 WS：`9089`（前端会自动连，不用手动开）

## 启动

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 首次需要初始化 Reflex 前端构建（会下载 bun，需联网）
reflex init

# 生产模式（首次会编译前端，需要几十秒）
python main.py

# 或开发模式（热重载）
python main.py dev
```

## 目录结构

```
mock-server/
├── main.py                       # 薄入口，转给 reflex run
├── rxconfig.py                   # Reflex 配置（端口、app_name）
├── requirements.txt
├── app/                          # 业务层（UI 无关）
│   ├── db.py                     # SQLite
│   ├── models.py                 # Rule CRUD
│   ├── flows.py                  # 流量缓冲 + 订阅
│   ├── matcher.py                # URL 匹配
│   └── proxy_addon.py            # mitmproxy addon
├── mock_server_ui/               # Reflex 应用
│   ├── mock_server_ui.py         # rx.App + mitmproxy 生命周期
│   ├── theme.py                  # 设计 tokens
│   ├── components/               # layout / badges
│   ├── states/                   # rules_state / flows_state
│   └── pages/                    # rules / flows / settings
└── data/rules.db                 # 运行时生成
```

## 手机配代理（HTTPS 抓包）

打开 UI 进入「设置」页有详细图文步骤；简版：

1. WiFi → 当前网络 → 高级 → 代理 → 手动；主机名填电脑 IP，端口 `9077`
2. 浏览器访问 `http://mitm.it`，下载安装 CA 证书
3. **Android 7+** 默认不信任用户 CA：
   - 自己的 app：`network_security_config.xml` 加 `<certificates src="user"/>`
   - 第三方 app：需 root 把 CA 装到系统层
