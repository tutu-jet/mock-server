"""One-time, transactional split of the original CaseListItem presets."""
import json
import logging


MIGRATION_KEY = "case_page_rules_v1"
LEGACY_PATTERN = "*/odata/mobile/CaseListItem*"


def migrate_case_rules(conn) -> bool:
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
            or {r["status_code"] for r in legacy} != {200, 403, 500}
            or any(r["method"] != "GET" or not r["name"].startswith("[case] ") for r in legacy)
            or sum(r["enabled"] for r in legacy) > 1
            or conn.execute(
                "SELECT 1 FROM rules WHERE request_match<>'' OR "
                "url_pattern LIKE '%/CaseSystem%' OR "
                "url_pattern LIKE '%/CaseCloseButtonStatus%' OR "
                "url_pattern LIKE '%/IncidentMessageAuth%'"
            ).fetchone()
        ):
            raise RuntimeError("Case 页面规则迁移存在自定义规则或启用状态冲突；未修改规则，请先核对。")
        for rule in legacy:
            suffix = rule["name"].partition("·")[2].strip()
            for scope, label in (("case-my", "My"), ("case-all", "All"), ("case-detail", "Detail")):
                name = f"[case] CaseListItem-{label} · {suffix}"
                if scope == "case-my":
                    conn.execute(
                        "UPDATE rules SET name=?, request_match=? WHERE id=?",
                        (name, scope, rule["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO rules "
                        "(name,url_pattern,method,enabled,status_code,response_headers,"
                        "response_body,request_match,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (name, rule["url_pattern"], rule["method"], rule["enabled"],
                         rule["status_code"], rule["response_headers"], rule["response_body"],
                         scope, rule["created_at"], rule["updated_at"]),
                    )
        for entity in ("CaseSystem", "CaseCloseButtonStatus", "IncidentMessageAuth"):
            pattern = f"*/odata/mobile/{entity}*"
            for status, label in ((403, "无权限 403"), (500, "服务错误 500"), (200, "无数据")):
                # The auth function returns an object, not an OData value collection.
                body = ""
                if status == 200:
                    body = "{}" if entity == "IncidentMessageAuth" else '{"value":[]}'
                conn.execute(
                    "INSERT INTO rules "
                    "(name,url_pattern,method,enabled,status_code,response_headers,response_body) "
                    "VALUES (?,?, 'GET', 0, ?,?,?)",
                    (f"[case] {entity} · {label}", pattern, status,
                     json.dumps({"Content-Type": "application/json;odata.metadata=minimal"}), body),
                )
        conn.execute("INSERT INTO settings (key,value) VALUES (?, '1')", (MIGRATION_KEY,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logging.getLogger("mock-server").info("Case 页面规则迁移完成；原状态已保留，新接口默认透传")
    return True
