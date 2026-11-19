"""
SQLite 连接 + 建表。同步版（sqlite3 标准库），FastAPI 路由里用 run_in_threadpool 包一下即可。
本地工具单用户，不存在并发争抢问题。

两个独立库：
- rules.db   规则配置（持久）
- flows.db   流量原始 body（会话级，启动时清空）
"""
import sqlite3
from contextlib import closing
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
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    request_match    TEXT    NOT NULL DEFAULT '',
    default_config   TEXT    NOT NULL DEFAULT '',
    state_key        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
        migrate_rules(conn)
        migrate_rule_defaults(conn)
        migrate_system_rules(conn)
        migrate_calendar_rules(conn)
        migrate_notification_rules(conn)
    with connect_flows() as conn:
        conn.executescript(FLOWS_SCHEMA)


def migrate_rules(conn: sqlite3.Connection) -> bool:
    """Back up existing presets before adding selectors or splitting Case rules."""
    from app.case_rules import LEGACY_PATTERN, MIGRATION_KEY, migrate_case_rules

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rules)")}
    done = conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone()
    legacy = conn.execute("SELECT 1 FROM rules WHERE url_pattern=?", (LEGACY_PATTERN,)).fetchone()
    if legacy and not done:
        backup_path = DB_PATH.with_name(DB_PATH.name + ".bak-case-pages-v1")
        if not backup_path.exists():
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                conn.backup(backup)
    if "request_match" not in columns:
        conn.execute("ALTER TABLE rules ADD COLUMN request_match TEXT NOT NULL DEFAULT ''")
    return migrate_case_rules(conn)


def migrate_rule_defaults(conn: sqlite3.Connection) -> None:
    from app.models import capture_defaults

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(rules)")}
    if "default_config" not in columns:
        backup_path = DB_PATH.with_name(DB_PATH.name + ".bak-rule-defaults-v1")
        if not backup_path.exists():
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                conn.backup(backup)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for column in ("default_config", "state_key"):
            if column not in columns:
                conn.execute(f"ALTER TABLE rules ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        capture_defaults(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def migrate_system_rules(conn: sqlite3.Connection) -> bool:
    from app.system_rules import LEGACY_PATTERN, MIGRATION_KEY, migrate_system_rules as split_system_rules

    pending = conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone() is None
    legacy = conn.execute(
        "SELECT 1 FROM rules WHERE url_pattern=? AND request_match=''", (LEGACY_PATTERN,),
    ).fetchone()
    if pending and legacy:
        backup_path = DB_PATH.with_name(DB_PATH.name + ".bak-system-paths-v1")
        if not backup_path.exists():
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                conn.backup(backup)
    return split_system_rules(conn)


def migrate_calendar_rules(conn: sqlite3.Connection) -> bool:
    from app.calendar_rules import MIGRATION_KEY, migrate_calendar_rules as add_calendar_rules

    pending = conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone() is None
    if pending:
        backup_path = DB_PATH.with_name(DB_PATH.name + ".bak-calendar-list-v1")
        if not backup_path.exists():
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                conn.backup(backup)
    return add_calendar_rules(conn)


def migrate_notification_rules(conn: sqlite3.Connection) -> bool:
    from app.notification_rules import MIGRATION_KEY, migrate_notification_rules as add_notification_rules

    pending = conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone() is None
    if pending:
        backup_path = DB_PATH.with_name(DB_PATH.name + ".bak-notification-list-v1")
        if not backup_path.exists():
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                conn.backup(backup)
    return add_notification_rules(conn)
