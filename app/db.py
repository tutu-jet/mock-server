"""
SQLite 连接 + 建表。同步版（sqlite3 标准库），FastAPI 路由里用 run_in_threadpool 包一下即可。
本地工具单用户，不存在并发争抢问题。
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rules.db"

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


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
