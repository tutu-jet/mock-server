"""Request selectors for the CaseListItem variants observed in captured traffic."""
import fnmatch
import re
from urllib.parse import parse_qs, urlsplit


REQUEST_MATCHES = {
    "": "完整 URL 通配",
    "case-my": "CaseListItem-My：auth = ME，无 pointer",
    "case-all": "CaseListItem-All：auth = ALL，无 pointer",
    "case-detail": "CaseListItem-Detail：有非空 pointer",
}

_EQUALITY = re.compile(r"\s*([A-Za-z_]\w*)\s+eq\s+'((?:[^']|'')*)'\s*")


def _filter_values(expression: str) -> dict[str, str] | None:
    """Only accept unambiguous AND-connected string equalities, never OR substrings."""
    values = {}
    offset = 0
    while offset < len(expression):
        match = _EQUALITY.match(expression, offset)
        if not match or match[1] in values:
            return None
        values[match[1]] = match[2].replace("''", "'")
        offset = match.end()
        if offset == len(expression):
            return values
        conjunction = re.match(r"and\s+", expression[offset:])
        if not conjunction:
            return None
        offset += conjunction.end()
    return None


def request_matches(rule, url: str, method: str) -> bool:
    if rule.method not in ("*", method) or not fnmatch.fnmatch(url, rule.url_pattern):
        return False
    if not rule.request_match:
        return True
    parsed = urlsplit(url)
    if parsed.path != "/backend/odata/mobile/CaseListItem":
        return False
    filters = parse_qs(parsed.query, keep_blank_values=True).get("$filter", [])
    if len(filters) != 1:
        return False
    values = _filter_values(filters[0])
    if values is None:
        return False
    if rule.request_match == "case-detail":
        return bool(values.get("pointer"))
    if "pointer" in values:
        return False
    expected = {"case-my": "ME", "case-all": "ALL"}.get(rule.request_match)
    return expected is not None and values.get("auth") == expected


def rule_identity(rule) -> tuple[str, str, str]:
    return rule.url_pattern, rule.method, rule.request_match
