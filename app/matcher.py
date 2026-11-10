"""
URL 匹配器。
内存缓存启用规则 -> addon 在 response 钩子里调 match() 决定改不改。
Web 层 CRUD 后必须调 reload_from_db() 让缓存生效。

整体 mock 开关：master_enabled 为内存标志，从 settings 表读取（key=mock_master_enabled）。
关闭时 match() 直接返回 None，所有请求全部透传到真实上游，忽略所有规则。
"""
from threading import Lock
from typing import Optional

from app import models
from app.rule_matching import request_matches

# settings 表里整体开关的 key；缺省（未设置）视为开启
MASTER_SWITCH_KEY = "mock_master_enabled"


class Matcher:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rules: list[models.Rule] = []  # 按 id 降序，方便 first match
        self._master_enabled = True
        self.reload_from_db()

    def reload_from_db(self) -> None:
        try:
            rules = [r for r in models.list_rules() if r.enabled]
        except Exception:
            # 表还没建好（启动时序问题），视作空
            rules = []
        try:
            master = models.get_setting(MASTER_SWITCH_KEY, "1") != "0"
        except Exception:
            master = True
        # list_rules 已经 ORDER BY id DESC，这里再保险一下
        rules.sort(key=lambda r: r.id, reverse=True)
        with self._lock:
            self._rules = rules
            self._master_enabled = master

    @property
    def master_enabled(self) -> bool:
        with self._lock:
            return self._master_enabled

    def match(self, url: str, method: str) -> Optional[models.Rule]:
        """按 id 降序找首个命中。整体开关关闭时直接放行（全透传）。"""
        with self._lock:
            if not self._master_enabled:
                return None
            rules = list(self._rules)
        for rule in rules:
            if request_matches(rule, url, method):
                return rule
        return None

    def find_conflict(
        self,
        url_pattern: str,
        method: str,
        exclude_id: Optional[int] = None,
        request_match: str = "",
    ) -> Optional[models.Rule]:
        """
        冲突 = 同 pattern、method 有交集，且请求分类相同或一方无分类。
        编辑时传 exclude_id 排除自己。
        """
        for rule in models.list_rules():
            if not rule.enabled:
                continue
            if exclude_id is not None and rule.id == exclude_id:
                continue
            if rule.url_pattern != url_pattern:
                continue
            if rule.request_match and request_match and rule.request_match != request_match:
                continue
            if rule.method == method or rule.method == "*" or method == "*":
                return rule
        return None


# 全局单例：addon 和 web 共用一个 matcher 实例
matcher = Matcher()
