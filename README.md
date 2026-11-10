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

System 域“system list”包含 `CACCloudAvaStatus` 的集合请求；“system detail”包含 `CACCloudAvaStatus('…')` 的带键请求、`CACSystemDetailHeader` 和 `SystemBundle`。集合与带键请求解析 URL path 后互斥匹配，开关独立，不依赖具体系统键值、host、`/backend` 前缀或 query 顺序。其他 System 接口保留在“未分类”。

System 下还按已确认页面归类：“bundle list”包含 `BundleStatuses`；“bundle detail”包含 `BundleSubscriptions`、`BundleDetailHeader` 和 `BundleSystem`。`BundleSystem` 与 `system detail` 中的 `SystemBundle` 是不同端点，不合并；这些 Bundle 分类只调整展示归属，不修改规则配置、默认快照或启用状态。

Calendar 下“calendar list”归入 `/odata/calendar/TimelineEvents`、`/odata/mobile/EventType`（单数）和 `/odata/mobile/CalendarSettings`。不把错误服务路径或相近名称当作对应接口，其他 Calendar 接口保留在“未分类”。

基于真实抓包确认，EventType 和 CalendarSettings 的响应均为 OData `value` 集合。首次新版启动会先备份 `data/rules.db.bak-calendar-list-v1`，再事务新增两个缺失接口的三态，全部默认关闭并同时保存初始默认快照。无数据响应保留 `@odata.context` 的协议片段和空 `value: []`，不保存真实 host、业务条目或动态 ETag；403 / 500 沿用预设的空响应体。新规则通过 `calendar-list` 分类限制精确集合路径，避免误匹配 EventTypes、相近名称或带键详情；已有 TimelineEvents 等规则和运行状态不变。迁移幂等，遇到已有相关自定义规则会明确报错，不静默覆盖。此迁移同样只在手动重启新版时执行。

首次新版启动会备份 `data/rules.db.bak-system-paths-v1`，再事务拆分原 `CACCloudAvaStatus` 三态：原 ID 保留为集合，详情复制相同配置、启用状态和预设状态身份。原命中次数不变，新增详情规则计数从零开始；域总计按独立匹配身份去重。已有默认快照原文不被覆盖，迁移映射保证恢复旧默认时仍限定对应集合 / 详情路径，不恢复成覆盖两页的宽泛匹配。重复启动不重复拆分；自定义匹配或启用状态冲突明确报错，不覆盖。真实运行库只在手动重启新版时迁移。

每张接口卡片保留四态切换，在状态标记右侧提供铅笔编辑图标，未启用时也可使用；卡片底部不增加操作区。有匹配或共享状态说明时，在铅笔左侧显示信息 SVG 图标，悬停、聚焦或点击可查看提示，Escape 或点外部关闭；正文不再重复展示说明，没有说明则不显示空图标。打开后在编辑抽屉中明确选择无数据、无权限或服务错误规则，并可进入详情或恢复默认；默认编辑当前启用态，透传时先显示无数据（没有则显示已有态），选择查看或编辑不会启用规则。详情沿用流量详情抽屉布局，展示保存的匹配条件、分类、返回码、Response Headers 和 Response Body，并可确认后“恢复默认”；不关联真实抓包，也不编造请求头、请求体或耗时。默认是当前已保存配置的持久快照，而非统一系统模板；首次启动新版会先备份 `data/rules.db.bak-rule-defaults-v1` 再捕获现有配置，新建规则也保存自己的初始快照，后续编辑不会覆盖它。默认不包含启用状态、命中次数或时间等运行信息。

编辑和恢复仅修改选定规则的配置，保留启用状态和命中次数；有冲突时提示错误，不覆盖其他规则。原有状态身份单独保存，修改返回码后仍可从原“无数据 / 无权限 / 服务错误”入口访问。保存与恢复以局部更新同步共享卡片、筛选和详情，不整页跳转。数据库升级在手动重启新版时执行，不在旧进程上直接改库；重启前请保存需要的抓包。

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
