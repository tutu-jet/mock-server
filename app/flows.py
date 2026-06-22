"""
流量记录环形缓冲 + SSE 订阅。
- addon.response 钩子调 add(flow_record) 把记录入队
- /flows/stream SSE 端点把新记录推给所有订阅者
- /flows 页面首次加载时一次性渲染当前缓冲
"""
import asyncio
import itertools
import json
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Optional

# body 单条最多保留 64KB，避免大文件吃光内存
MAX_BODY_BYTES = 64 * 1024
# 环形缓冲容量
RING_SIZE = 1000

# 视为文本的 content-type：text/*、常见结构化文本、以及 +json / +xml 子类型
_TEXTUAL_RE = re.compile(
    r"^(text/|application/(json|xml|x-www-form-urlencoded|javascript|graphql|x-ndjson)"
    r"|[^;]*\+(json|xml))",
    re.I,
)


@dataclass
class FlowRecord:
    id: int
    ts: float                       # 完成时间（unix）
    method: str
    url: str
    status: int
    duration_ms: int
    rule_id: Optional[int] = None   # 命中规则 id；None 表示透传
    rule_name: Optional[str] = None
    req_headers: dict = field(default_factory=dict)
    resp_headers: dict = field(default_factory=dict)
    req_body: str = ""              # 已按 UTF-8 截断/转义
    resp_body: str = ""
    req_body_truncated: bool = False
    resp_body_truncated: bool = False
    req_body_size: int = 0          # 原始字节数（未截断）
    resp_body_size: int = 0


def _truncate_body(b: bytes, content_type: str = "") -> tuple[str, bool, int]:
    """按字节截到 MAX_BODY_BYTES，再按 content-type 决定文本/二进制展示。返回 (text, truncated, raw_len)。"""
    raw_len = len(b)
    if not b:
        return "", False, 0
    truncated = raw_len > MAX_BODY_BYTES
    if truncated:
        b = b[:MAX_BODY_BYTES]
    # content-type 明确不是文本时，给个友好占位，避免一片乱码
    if content_type and not _TEXTUAL_RE.match(content_type.strip()):
        return f"(二进制内容: {content_type}, {raw_len} bytes)", truncated, raw_len
    try:
        return b.decode("utf-8", errors="replace"), truncated, raw_len
    except Exception:
        return repr(b), truncated, raw_len


class FlowBuffer:
    def __init__(self, maxlen: int = RING_SIZE) -> None:
        self._buf: deque[FlowRecord] = deque(maxlen=maxlen)
        self._index: dict[int, FlowRecord] = {}
        self._lock = Lock()
        self._id_seq = itertools.count(1)
        # 每个订阅者一个 asyncio.Queue；推送时遍历广播
        self._subscribers: list[asyncio.Queue] = []
        self._sub_lock = Lock()

    def next_id(self) -> int:
        return next(self._id_seq)

    def add(self, rec: FlowRecord) -> None:
        with self._lock:
            # deque 满时新元素会顶掉最旧的，需要同步从索引里删
            evicted: Optional[FlowRecord] = None
            if len(self._buf) == self._buf.maxlen:
                evicted = self._buf[0]
            self._buf.append(rec)
            if evicted is not None:
                self._index.pop(evicted.id, None)
            self._index[rec.id] = rec
        # 同步删除被挤出记录的 DB body（在锁外，避免长事务阻塞）
        if evicted is not None:
            try:
                from app import models
                models.delete_flow_bodies(evicted.id)
            except Exception:
                pass
        # 广播：用 put_nowait，订阅者掉线满了就丢（防止单个慢消费者拖垮全局）
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                pass

    def snapshot(self) -> list[FlowRecord]:
        """当前缓冲快照，按时间正序（旧->新）。前端会自己倒序展示。"""
        with self._lock:
            return list(self._buf)

    def get(self, flow_id: int) -> Optional[FlowRecord]:
        """按 id 取完整记录；已被环形缓冲挤出则返回 None。"""
        with self._lock:
            return self._index.get(flow_id)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
            self._index.clear()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


# 全局单例
buffer = FlowBuffer()


def record_to_json(rec: FlowRecord) -> str:
    """SSE data 字段用，紧凑 JSON。"""
    return json.dumps(asdict(rec), ensure_ascii=False, separators=(",", ":"))


def now_ms() -> int:
    return int(time.time() * 1000)


def human_bytes(n: int) -> str:
    """人类可读字节数：1023 -> '1023 B'，2048 -> '2.0 KB'，3145728 -> '3.0 MB'。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"
