"""
URL 匹配器。
内存缓存启用规则 -> addon 在 response 钩子里调 match() 决定改不改。
Web 层 CRUD 后必须调 reload_from_db() 让缓存生效。
"""
import fnmatch
from threading import Lock
from typing import Optional

from app import models


class Matcher:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rules: list[models.Rule] = []  # 按 id 降序，方便 first match
        self.reload_from_db()

    def reload_from_db(self) -> None:
        try:
            rules = [r for r in models.list_rules() if r.enabled]
        except Exception:
            # 表还没建好（启动时序问题），视作空
            rules = []
        # list_rules 已经 ORDER BY id DESC，这里再保险一下
        rules.sort(key=lambda r: r.id, reverse=True)
        with self._lock:
            self._rules = rules

    def match(self, url: str, method: str) -> Optional[models.Rule]:
        """按 id 降序找首个命中。"""
        with self._lock:
            rules = list(self._rules)
        for rule in rules:
            if rule.method != "*" and rule.method != method:
                continue
            if fnmatch.fnmatch(url, rule.url_pattern):
                return rule
        return None

    def find_conflict(
        self,
        url_pattern: str,
        method: str,
        exclude_id: Optional[int] = None,
    ) -> Optional[models.Rule]:
        """
        冲突 = 已启用 + url_pattern 完全相同 + method 有交集（同名或一方是 *）
        编辑时传 exclude_id 排除自己。
        """
        for rule in models.list_rules():
            if not rule.enabled:
                continue
            if exclude_id is not None and rule.id == exclude_id:
                continue
            if rule.url_pattern != url_pattern:
                continue
            if rule.method == method or rule.method == "*" or method == "*":
                return rule
        return None


# 全局单例：addon 和 web 共用一个 matcher 实例
matcher = Matcher()
