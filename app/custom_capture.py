"""One-shot upstream response capture without changing persisted Mock state."""
from dataclasses import dataclass, replace
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from app.models import Rule
from app.rule_matching import request_matches, rule_identity

CAPTURE_TIMEOUT = 60
RESULT_TTL = 300
MAX_CAPTURES = 8
MAX_CAPTURE_BYTES = 2 * 1024 * 1024


@dataclass
class Capture:
    token: str
    source: Rule
    expires: float
    state: str = "waiting"
    status_code: int = 0
    headers: str = ""
    body: str = ""
    error: str = ""


class CaptureManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._captures: dict[str, Capture] = {}

    def _expire(self) -> None:
        now = monotonic()
        for token, capture in list(self._captures.items()):
            if capture.expires > now:
                continue
            if capture.state in ("waiting", "inflight"):
                capture.state = "error"
                capture.error = "等待真实响应超时，请重新抓取"
                capture.expires = now + RESULT_TTL
            else:
                del self._captures[token]

    def start(self, source: Rule) -> Capture:
        with self._lock:
            self._expire()
            if any(
                c.state in ("waiting", "inflight") and rule_identity(c.source) == rule_identity(source)
                for c in self._captures.values()
            ):
                raise ValueError("该接口已有抓取任务，请先取消或等待完成")
            if len(self._captures) >= MAX_CAPTURES:
                raise ValueError("抓取任务数量已达上限，请先关闭已有任务")
            capture = Capture(token_urlsafe(24), source, monotonic() + CAPTURE_TIMEOUT)
            self._captures[capture.token] = capture
            return replace(capture)

    def claim(self, url: str, method: str) -> str | None:
        with self._lock:
            self._expire()
            for capture in self._captures.values():
                if capture.state == "waiting" and request_matches(capture.source, url, method):
                    capture.state = "inflight"
                    return capture.token
        return None

    def finish(
        self, token: str, *, status_code: int = 0, headers: str = "",
        body: str = "", error: str = "",
    ) -> None:
        with self._lock:
            self._expire()
            capture = self._captures.get(token)
            if capture is None or capture.state != "inflight":
                return
            capture.state = "error" if error else "complete"
            capture.status_code = status_code
            capture.headers = headers
            capture.body = body
            capture.error = error
            capture.expires = monotonic() + RESULT_TTL

    def get(self, token: str) -> Capture | None:
        with self._lock:
            self._expire()
            capture = self._captures.get(token)
            return replace(capture) if capture else None

    def cancel(self, token: str) -> None:
        with self._lock:
            self._captures.pop(token, None)


captures = CaptureManager()
