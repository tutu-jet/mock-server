# mock-server

本地 HTTP/HTTPS 代理 + Web UI，按规则改返回值，主要给安卓真机调试用。

- 抓所有经过代理的流量，实时显示
- 按 host / path / method 配置 mock 规则，命中则替换 status / headers / body
- 未命中的请求透传到真实服务器

## 架构

![architecture](docs/architecture.svg)

单进程 Python：一个 asyncio loop 里同时跑 mitmproxy（DumpMaster）和 FastAPI（uvicorn）。两边共用 `app/` 下的业务层（规则匹配、流量缓冲、SQLite）。

## 请求流程

![flow](docs/flow.svg)

## 技术栈

- Python 3.14
- [mitmproxy](https://mitmproxy.org/) 11+ — 代理内核
- FastAPI + Jinja2 + HTMX — Web UI（SSE 推送流量）
- SQLite — `rules.db` 持久化规则与设置，`flows.db` 会话级保存请求/响应体

## 端口

| 端口 | 用途 |
| ---- | ---- |
| 9077 | 代理端口（手机 WiFi 代理填这个） |
| 9088 | Web UI（浏览器打开 `http://localhost:9088`） |

## 启动

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python main.py
```

打开浏览器访问 <http://localhost:9088>。

规则页切换接口状态、整体 Mock 或“一键全关”时局部更新，保留当前滚动位置和筛选条件，并同步更新启用数量。仅显示启用时，关闭的接口会从列表移除。

每个域下按页面分组，未确认归属的接口保留在“未分类”。Case 的页面归属：

| 页面 | 接口 |
| ---- | ---- |
| Overview | CaseListItem-My、CaseListItem-All |
| List | 与 Overview 共用的 My / All、CaseSystem |
| Detail | CaseListItem-Detail、CaseCloseButtonStatus、IncidentHeader、IncidentMessageAuth |

跨页面的 My / All 使用同一组规则和状态，域计数与总计去重。CaseListItem 的“请求分类”解析 URL 解码后的 `$filter`：My 为 `auth eq 'ME'`，All 为 `auth eq 'ALL'`（两者都不含 pointer）；Detail 为非空的 `pointer eq '…'`。匹配不依赖 query 参数顺序、空格编码、具体案件 ID 或 `OPEN` 状态。仅接受由 `and` 连接的字符串等值条件；歧义表达式（如 `or`、重复字段或重复 `$filter`）不命中分类规则。普通规则仍按完整 URL 通配匹配，编辑时可选择请求分类。

首次启动新版会先备份 `data/rules.db.bak-case-pages-v1`，再事务迁移旧版 CaseListItem 三态：原 ID 保留为 My，复制为 All 和 Detail，各自保留原响应与启用状态；新增三个缺失接口默认关闭，不修改其他规则。遇到自定义规则或多态同时启用等冲突会明确报错，不批量覆盖。IncidentMessageAuth 返回对象，其“无数据”为 `{}`，其他新增接口为空 `value` 集合。迁移只执行一次；备份保留原规则和计数。**已运行的进程需要手动重启**以加载新路由、匹配逻辑与迁移；重启会按既有行为清空会话抓包，需先保存需要的流量。

## 目录结构

```
mock-server/
├── main.py             # 单进程入口，FastAPI app + mitmproxy lifespan
├── requirements.txt
├── app/                # 业务层（UI 无关）
│   ├── db.py           # rules.db / flows.db
│   ├── models.py       # rules / flow_bodies / settings CRUD
│   ├── matcher.py      # 规则匹配 + 冲突检测
│   ├── flows.py        # 流量环形缓冲 + asyncio Queue 订阅
│   ├── addon.py        # MockAddon（应用规则）
│   └── proxy_addon.py  # 原始 req/resp 入库
├── web/
│   ├── routes.py       # 所有 UI 路由
│   ├── templates/      # Jinja2 模板
│   └── static/app.css
├── docs/               # 架构图
└── data/               # 运行时 SQLite
```

## 手机配代理（HTTPS 抓包）

1. 手机 WiFi → 当前网络 → 高级 → 代理 → 手动；主机名填电脑 IP，端口 `9077`
2. 手机浏览器访问 `http://mitm.it` 下载并安装 CA 证书
3. Android 7+ 默认不信任用户 CA：
   - 自己的 app：`network_security_config.xml` 加 `<certificates src="user"/>`
   - 第三方 app：需 root 把 CA 装到系统层
