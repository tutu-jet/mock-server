"""集中的设计 token。改色/改字体来这一处。"""

# 强调色：iris (Radix) 偏靛蓝，配深色舒服
ACCENT = "iris"
GRAY = "slate"
RADIUS = "large"   # Radix: none/small/medium/large/full
SCALING = "100%"

# HTTP method 配色（card method badge 用）
METHOD_COLORS: dict[str, str] = {
    "GET":    "blue",
    "POST":   "green",
    "PUT":    "amber",
    "DELETE": "red",
    "PATCH":  "purple",
    "*":      "gray",
}

# 状态码 -> 语义色
def status_color(code: int) -> str:
    if code <= 0:
        return "gray"
    if code < 300:
        return "green"
    if code < 400:
        return "blue"
    if code < 500:
        return "amber"
    return "red"
