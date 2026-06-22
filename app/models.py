"""
Rule 数据访问层。纯 CRUD，不做业务校验（页面层做）。
"""
from dataclasses import dataclass
from typing import Optional

from app.db import connect, connect_flows

ALLOWED_METHODS = ["*", "GET", "POST", "PUT", "DELETE", "PATCH"]


@dataclass
class Rule:
    id: int
    name: str
    url_pattern: str
    method: str
    enabled: bool
    status_code: int
    response_headers: str
    response_body: str
    match_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> "Rule":
        return cls(
            id=row["id"],
            name=row["name"],
            url_pattern=row["url_pattern"],
            method=row["method"],
            enabled=bool(row["enabled"]),
            status_code=row["status_code"],
            response_headers=row["response_headers"],
            response_body=row["response_body"],
            match_count=row["match_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def list_rules() -> list[Rule]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY id DESC"
        ).fetchall()
    return [Rule.from_row(r) for r in rows]


def get_rule(rule_id: int) -> Optional[Rule]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
    return Rule.from_row(row) if row else None


def create_rule(
    name: str,
    url_pattern: str,
    method: str,
    status_code: int,
    response_headers: str,
    response_body: str,
    enabled: bool = True,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO rules
                (name, url_pattern, method, enabled, status_code, response_headers, response_body)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, url_pattern, method, int(enabled), status_code, response_headers, response_body),
        )
        return cur.lastrowid


def update_rule(
    rule_id: int,
    *,
    name: str,
    url_pattern: str,
    method: str,
    status_code: int,
    response_headers: str,
    response_body: str,
    enabled: bool,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE rules SET
                name = ?, url_pattern = ?, method = ?, enabled = ?,
                status_code = ?, response_headers = ?, response_body = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (name, url_pattern, method, int(enabled), status_code,
             response_headers, response_body, rule_id),
        )


def delete_rule(rule_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def toggle_rule(rule_id: int) -> bool:
    """切换 enabled，返回新状态"""
    with connect() as conn:
        conn.execute(
            "UPDATE rules SET enabled = 1 - enabled, updated_at = datetime('now') WHERE id = ?",
            (rule_id,),
        )
        row = conn.execute(
            "SELECT enabled FROM rules WHERE id = ?", (rule_id,)
        ).fetchone()
    return bool(row["enabled"]) if row else False


def set_rule_enabled(rule_id: int, enabled: bool) -> None:
    """幂等 setter：直接把 enabled 设为目标值。"""
    with connect() as conn:
        conn.execute(
            "UPDATE rules SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
            (int(enabled), rule_id),
        )


def increment_match_count(rule_id: int) -> None:
    """命中规则后 +1，Task #3 会用到"""
    with connect() as conn:
        conn.execute(
            "UPDATE rules SET match_count = match_count + 1 WHERE id = ?",
            (rule_id,),
        )


# ---------- flow_bodies ----------

def insert_flow_body(flow_id: int, kind: str, content: bytes, content_type: str) -> None:
    """kind: 'req' | 'resp'。空 body 跳过写入。"""
    if not content:
        return
    with connect_flows() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO flow_bodies (flow_id, kind, content, content_type, size) VALUES (?, ?, ?, ?, ?)",
            (flow_id, kind, content, content_type, len(content)),
        )


def get_flow_body(flow_id: int, kind: str) -> Optional[tuple[bytes, str, int]]:
    """返回 (content, content_type, size)，找不到返回 None。"""
    with connect_flows() as conn:
        row = conn.execute(
            "SELECT content, content_type, size FROM flow_bodies WHERE flow_id = ? AND kind = ?",
            (flow_id, kind),
        ).fetchone()
    if not row:
        return None
    return row["content"], row["content_type"], row["size"]


def delete_flow_bodies(flow_id: int) -> None:
    """删除某条流量对应的所有 body（req + resp）。"""
    with connect_flows() as conn:
        conn.execute("DELETE FROM flow_bodies WHERE flow_id = ?", (flow_id,))


def clear_flow_bodies() -> None:
    """清空全部 body，并 VACUUM 回收空间。"""
    with connect_flows() as conn:
        conn.execute("DELETE FROM flow_bodies")
        # VACUUM 不能在事务里，autocommit 模式可直接调
        conn.execute("VACUUM")


# ---------- settings (key/value，持久) ----------

def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
