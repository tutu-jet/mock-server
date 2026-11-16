"""Local persistent history for device and topic FCM tests."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager

from app.db import DATA_DIR

DB_PATH = DATA_DIR / "push.db"
LIMIT = 100


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS push_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            project TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT NOT NULL DEFAULT ''
        )""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def start(payload: dict, project: str) -> int:
    # Persist an uncertain outcome before contacting FCM, including across crashes.
    with connection() as conn:
        cursor = conn.execute(
            "INSERT INTO push_history(created_at,project,payload,status,result) VALUES(?,?,?,?,?)",
            (time.time(), project, json.dumps(payload, ensure_ascii=False), "unknown", "发送尚未确认完成；请先检查设备是否收到。"),
        )
        entry_id = cursor.lastrowid
        conn.execute("DELETE FROM push_history WHERE id NOT IN (SELECT id FROM push_history ORDER BY id DESC LIMIT ?)", (LIMIT,))
    return entry_id


def finish(entry_id: int, status: str, result: str):
    if status not in ("accepted", "failed", "unknown"):
        raise ValueError("Unknown push outcome")
    with connection() as conn:
        conn.execute("UPDATE push_history SET status=?,result=? WHERE id=?", (status, result, entry_id))


def summary(row) -> dict:
    payload = json.loads(row["payload"])
    message = payload["message"]
    topic = message.get("topic")
    target = ("全局 · Topic: " + topic) if topic else ("设备 · " + hashlib.sha256(message["token"].encode()).hexdigest()[:8])
    return {
        "id": row["id"], "created_at": row["created_at"], "project": row["project"],
        "status": row["status"], "result": row["result"],
        "source": message.get("data", {}).get("source", ""),
        "source_id": message.get("data", {}).get("sourceId", ""),
        "title": message.get("notification", {}).get("title", ""),
        "body": message.get("notification", {}).get("body", ""),
        "device": target,
        "target_type": "topic" if topic else "token",
    }


def list_entries() -> list[dict]:
    with connection() as conn:
        return [summary(row) for row in conn.execute("SELECT * FROM push_history ORDER BY id DESC LIMIT ?", (LIMIT,))]


def get_entry(entry_id: int) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM push_history WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return None
        return {**summary(row), "payload": json.loads(row["payload"])}
