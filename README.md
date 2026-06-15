# mock-server

丐版 Charles：本地代理 + Web UI 改 HTTP/HTTPS 返回值，专为安卓真机调试做的。

## 技术栈
- 代理内核：[mitmproxy](https://mitmproxy.org/)
- Web UI：FastAPI + Jinja2 + htmx + Tailwind (CDN)
- DB：SQLite（规则持久化）
- 流量：内存环形缓冲 1000 条 + SSE 实时推送
- 部署：单进程，FastAPI 与 mitmproxy 共用一个 asyncio loop

## 端口
- 代理：`9077`（手机 WiFi 设置代理填这个）
- UI：`9088`（浏览器打开 http://localhost:9088）

## 启动

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## 手机配代理（HTTPS 抓包）

1. 手机 WiFi 设置 → 修改网络 → 高级 → 代理：手动 → 主机名填电脑 IP，端口 `9077`
2. 浏览器访问 `http://mitm.it`，下载并安装 CA 证书
3. **Android 7+** 默认不信任用户 CA：
   - 自己的 app：在 `network_security_config.xml` 里加 `<certificates src="user"/>`
   - 第三方 app：需要 root 把 CA 装到系统层
4. 浏览器和 WebView 装完用户 CA 即可抓包

## 目录结构

```
mock-server/
├── main.py                    # 单入口
├── requirements.txt
├── app/
│   ├── web.py                 # FastAPI 路由
│   ├── db.py                  # SQLite
│   ├── models.py              # Rule CRUD
│   ├── flows.py               # 流量缓冲 + SSE
│   ├── matcher.py             # URL 匹配 + 改写
│   ├── proxy_addon.py         # mitmproxy addon
│   └── templates/
└── data/rules.db              # 运行时生成
```
