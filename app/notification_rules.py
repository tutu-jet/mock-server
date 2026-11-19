"""Notification presets backed by the observed OData collection response envelopes."""
import json
import logging

from app.models import capture_defaults


MIGRATION_KEY = "notification_rules_v1"

ENTITIES = ("CommunityNotification", "PushNotification")


def migrate_notification_rules(conn) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone():
            conn.execute("COMMIT")
            return False
        if conn.execute(
            "SELECT 1 FROM rules WHERE url_pattern LIKE '%/CommunityNotification%' "
            "OR url_pattern LIKE '%/PushNotification%'"
        ).fetchone():
            raise RuntimeError("Notification 预设迁移发现已有相关自定义规则；未新增或覆盖，请先核对。")
        for entity in ENTITIES:
            for status, label in ((403, "无权限 403"), (500, "服务错误 500"), (200, "无数据")):
                body = ""
                if status == 200:
                    body = json.dumps({
                        "@odata.context": f"$metadata#{entity}/$entity", "value": [],
                    }, separators=(",", ":"))
                cursor = conn.execute(
                    "INSERT INTO rules "
                    "(name,url_pattern,method,enabled,status_code,response_headers,response_body) "
                    "VALUES (?,?, 'GET', 0, ?,?,?)",
                    (f"[notification] {entity} · {label}", f"*/odata/mobile/{entity}*", status,
                     '{"Content-Type":"application/json;odata.metadata=minimal"}', body),
                )
                capture_defaults(conn, cursor.lastrowid)
        conn.execute("INSERT INTO settings (key,value) VALUES (?,'1')", (MIGRATION_KEY,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logging.getLogger("mock-server").info("Notification 列表预设已补齐，初始默认已保存，新增规则全部透传")
    return True
