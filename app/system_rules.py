"""Split the saved System collection/keyed-path presets without losing defaults."""
import json
import logging


MIGRATION_KEY = "system_path_rules_v1"
LEGACY_PATTERN = "*/odata/mobile/CACCloudAvaStatus*"


def migrate_system_rules(conn) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone():
            conn.execute("COMMIT")
            return False
        legacy = conn.execute(
            "SELECT * FROM rules WHERE url_pattern=? AND request_match='' ORDER BY id",
            (LEGACY_PATTERN,),
        ).fetchall()
        if not legacy:
            conn.execute("COMMIT")
            return False
        if (
            len(legacy) != 3
            or {r["state_key"] for r in legacy} != {"nodata", "noauth", "error"}
            or any(r["method"] != "GET" or not r["name"].startswith("[system] ") for r in legacy)
            or sum(r["enabled"] for r in legacy) > 1
            or conn.execute(
                "SELECT 1 FROM rules WHERE request_match IN ('system-list','system-detail') "
                "OR (url_pattern LIKE '%CACCloudAvaStatus%' AND url_pattern<>?)",
                (LEGACY_PATTERN,),
            ).fetchone()
        ):
            raise RuntimeError("System 集合/详情规则迁移存在自定义规则或启用状态冲突；未修改规则。")
        scopes = {}
        for rule in legacy:
            conn.execute("UPDATE rules SET request_match='system-list' WHERE id=?", (rule["id"],))
            scopes[str(rule["id"])] = "system-list"
            clone = conn.execute(
                "INSERT INTO rules "
                "(name,url_pattern,method,enabled,status_code,response_headers,response_body,"
                "request_match,default_config,state_key,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,'system-detail',?,?,?,?)",
                (rule["name"], rule["url_pattern"], rule["method"], rule["enabled"],
                 rule["status_code"], rule["response_headers"], rule["response_body"],
                 rule["default_config"], rule["state_key"], rule["created_at"], rule["updated_at"]),
            )
            scopes[str(clone.lastrowid)] = "system-detail"
        # Preserve immutable snapshots verbatim; remember how their old broad match was split.
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?)",
            (MIGRATION_KEY, json.dumps(scopes)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logging.getLogger("mock-server").info("System 集合/详情规则迁移完成；原配置、默认及状态已保留")
    return True


def scoped_default(rule_id: int, config: dict, migration: str) -> dict:
    """Translate only a migrated legacy default; never widen it back to both paths."""
    scope = json.loads(migration).get(str(rule_id))
    if scope and config["url_pattern"] == LEGACY_PATTERN and not config["request_match"]:
        return {**config, "request_match": scope}
    return config
