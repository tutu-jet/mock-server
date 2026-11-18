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
from app.custom_capture import captures, MAX_CAPTURE_BYTES

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
        capture_token = captures.claim(url, method)
        if capture_token:
            flow.metadata["custom_capture_token"] = capture_token
            return
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
        capture_token = flow.metadata.get("custom_capture_token")
        if capture_token:
            try:
                response = flow.response
                if response is None:
                    raise ValueError("接口没有返回响应")
                content = response.get_content(strict=True) or b""
                if len(content) > MAX_CAPTURE_BYTES:
                    raise ValueError("响应超过 2 MiB，无法完整填入，请下载响应后手动编辑")
                content_type = response.headers.get("content-type", "")
                if content and content_type and not flows._TEXTUAL_RE.match(content_type.strip()):
                    raise ValueError("响应为二进制内容，无法作为文本自定义响应")
                body = response.get_text(strict=True) or ""
                if len(body.encode("utf-8")) > MAX_CAPTURE_BYTES:
                    raise ValueError("响应转换为 UTF-8 后超过 2 MiB，请手动编辑")
                excluded = {
                    "content-encoding", "content-length", "transfer-encoding",
                    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                    "te", "trailer", "upgrade",
                }
                excluded.update(
                    value.strip().lower()
                    for value in response.headers.get("connection", "").split(",")
                )
                headers = {
                    key: value for key, value in response.headers.items()
                    if key.lower() not in excluded
                }
                for key, value in list(headers.items()):
                    if key.lower() == "content-type":
                        parts = [p.strip() for p in value.split(";") if not p.strip().lower().startswith("charset=")]
                        headers[key] = "; ".join(parts) + "; charset=utf-8"
                captures.finish(
                    capture_token, status_code=response.status_code,
                    headers=json.dumps(headers, ensure_ascii=False), body=body,
                )
            except ValueError as error:
                log.warning("抓取真实响应失败: %s", error)
                captures.finish(capture_token, error=str(error))
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
            req_body, req_trunc, req_size = flows._truncate_body(req_raw, req_ct)
            if resp:
                resp_ct = resp.headers.get("content-type", "")
                try:
                    resp_raw = resp.get_content(strict=False) or b""
                except Exception:
                    resp_raw = resp.raw_content or b""
                resp_body, resp_trunc, resp_size = flows._truncate_body(resp_raw, resp_ct)
            else:
                resp_body, resp_trunc, resp_size = "", False, 0

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
                req_body_size=req_size,
                resp_body_size=resp_size,
            )
            # 把原始字节存数据库，供下载/复制完整 body 用
            try:
                if req_raw:
                    models.insert_flow_body(rec.id, "req", req_raw, req_ct)
                if resp and resp_raw:
                    models.insert_flow_body(rec.id, "resp", resp_raw, resp_ct)
            except Exception as e:
                log.warning(f"保存 body 失败 flow_id={rec.id}: {e}")
            flows.buffer.add(rec)
        except Exception as e:
            log.warning(f"记录流量失败: {e}")

    def error(self, flow: http.HTTPFlow) -> None:
        token = flow.metadata.get("custom_capture_token")
        if token:
            message = f"真实请求失败：{flow.error.msg if flow.error else '未知网络错误'}"
            log.warning("%s", message)
            captures.finish(token, error=message)
