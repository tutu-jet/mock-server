"""
mitmproxy addon - Task #4 版本
- request 钩子：按 matcher 命中规则就直接构造 mock response，不真发到上游；同时记下 start_ts
- response 钩子：组装 FlowRecord 入环形缓冲，触发 SSE 推送
"""
import json
import logging
import time

from mitmproxy import http

from app import flows, models
from app.matcher import matcher

log = logging.getLogger("mock-server.addon")


def _parse_headers_json(s: str) -> dict[str, str]:
    """规则里的 response_headers 是 JSON 文本，解析失败时返回空 dict 不影响主流程"""
    try:
        obj = json.loads(s) if s else {}
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items()}
    except json.JSONDecodeError:
        log.warning(f"规则 headers 不是合法 JSON: {s!r}")
    return {}


def _headers_to_dict(headers) -> dict[str, str]:
    """mitmproxy Headers -> 普通 dict（同名头取最后一个，够用）"""
    return {k: v for k, v in headers.items()}


class MockAddon:
    def __init__(self) -> None:
        log.info("MockAddon 初始化")

    def request(self, flow: http.HTTPFlow) -> None:
        """命中规则 -> 直接 set response，mitmproxy 不会再去 fetch 上游"""
        # 给每个 flow 打个开始时间戳，response 钩子算耗时用
        flow.metadata["mock_start"] = time.time()

        url = flow.request.pretty_url
        method = flow.request.method
        rule = matcher.match(url, method)
        if rule is None:
            return
        log.info(f"[HIT #{rule.id} {rule.name}] {method} {url}")
        flow.response = http.Response.make(
            status_code=rule.status_code,
            content=rule.response_body.encode("utf-8"),
            headers=_parse_headers_json(rule.response_headers),
        )
        # 把命中信息挂在 metadata 上，response 钩子读
        flow.metadata["mock_rule_id"] = rule.id
        flow.metadata["mock_rule_name"] = rule.name
        try:
            models.increment_match_count(rule.id)
        except Exception as e:
            log.warning(f"increment_match_count 失败: {e}")

    def response(self, flow: http.HTTPFlow) -> None:
        """组装流量记录推到环形缓冲（SSE 自动广播）"""
        try:
            start = flow.metadata.get("mock_start") or time.time()
            duration_ms = int((time.time() - start) * 1000)
            req = flow.request
            resp = flow.response
            # 用 get_content(strict=False) 让 mitmproxy 自动按 Content-Encoding 解压
            # （gzip/br/deflate/zstd），解不出来时回退原始字节而不是抛异常
            req_ct = req.headers.get("content-type", "")
            try:
                req_raw = req.get_content(strict=False) or b""
            except Exception:
                req_raw = req.raw_content or b""
            req_body, req_trunc = flows._truncate_body(req_raw, req_ct)
            if resp:
                resp_ct = resp.headers.get("content-type", "")
                try:
                    resp_raw = resp.get_content(strict=False) or b""
                except Exception:
                    resp_raw = resp.raw_content or b""
                resp_body, resp_trunc = flows._truncate_body(resp_raw, resp_ct)
            else:
                resp_body, resp_trunc = "", False

            rec = flows.FlowRecord(
                id=flows.buffer.next_id(),
                ts=time.time(),
                method=req.method,
                url=req.pretty_url,
                status=resp.status_code if resp else 0,
                duration_ms=duration_ms,
                rule_id=flow.metadata.get("mock_rule_id"),
                rule_name=flow.metadata.get("mock_rule_name"),
                req_headers=_headers_to_dict(req.headers),
                resp_headers=_headers_to_dict(resp.headers) if resp else {},
                req_body=req_body,
                resp_body=resp_body,
                req_body_truncated=req_trunc,
                resp_body_truncated=resp_trunc,
            )
            flows.buffer.add(rec)
        except Exception as e:
            log.warning(f"记录流量失败: {e}")
