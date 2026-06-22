"""
SQLite 连接 + 建表。同步版（sqlite3 标准库），FastAPI 路由里用 run_in_threadpool 包一下即可。
本地工具单用户，不存在并发争抢问题。

两个独立库：
- rules.db   规则配置（持久）
- flows.db   流量原始 body（会话级，启动时清空）
"""
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "rules.db"
FLOWS_DB_PATH = DATA_DIR / "flows.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    url_pattern      TEXT    NOT NULL,                    -- fnmatch glob
    method           TEXT    NOT NULL DEFAULT '*',        -- GET/POST/.../*
    enabled          INTEGER NOT NULL DEFAULT 1,
    status_code      INTEGER NOT NULL DEFAULT 200,
    response_headers TEXT    NOT NULL DEFAULT '{}',       -- JSON 文本
    response_body    TEXT    NOT NULL DEFAULT '',         -- 原始 body 文本
    match_count      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

FLOWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_bodies (
    flow_id      INTEGER NOT NULL,
    kind         TEXT    NOT NULL,                        -- 'req' | 'resp'
    content      BLOB    NOT NULL,                        -- 原始字节
    content_type TEXT    NOT NULL DEFAULT '',
    size         INTEGER NOT NULL,
    PRIMARY KEY (flow_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_flow_bodies_flow_id ON flow_bodies(flow_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_flows() -> sqlite3.Connection:
    conn = sqlite3.connect(FLOWS_DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL 提升大 body 写入性能
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
    with connect_flows() as conn:
        conn.executescript(FLOWS_SCHEMA)
