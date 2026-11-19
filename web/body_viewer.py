"""Read-only JSON presentation; never rewrite stored response bodies."""
import json
import re


_FENCE = re.compile(
    r"^(?P<open>[ \t]*`{3,}[^\r\n]*\r?\n)(?P<body>.*?)"
    r"(?P<close>^[ \t]*`{3,}[ \t]*(?=\r?$))",
    re.MULTILINE | re.DOTALL,
)


def reading_text(value: str) -> str:
    """Indent JSON inside fences, keeping delimiters and other text intact."""
    def format_fence(match):
        content = match["body"].strip()
        if not content.startswith(("{", "[")):
            return match[0]
        try:
            formatted = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        except (ValueError, TypeError, RecursionError):
            return match[0]
        newline = "\r\n" if match["open"].endswith("\r\n") else "\n"
        return match["open"] + formatted.replace("\n", newline) + newline + match["close"]

    return _FENCE.sub(format_fence, value)


def reading_json(text: str):
    """Return a bounded presentation tree, or None for non-JSON bodies."""
    def node(value, depth=0):
        if depth >= 24:
            raise ValueError("JSON nesting is too deep for the reading tree")
        if isinstance(value, (dict, list)):
            is_object = isinstance(value, dict)
            items = value.items() if is_object else enumerate(value)
            children = []
            for key, child in items:
                child_node = node(child, depth + 1)
                if (is_object and key == "anonymized_content"
                        and isinstance(child, str) and child == value.get("content")):
                    child_node["duplicate_of"] = "content"
                children.append((str(key), child_node))
            return {"kind": "object" if is_object else "array",
                    "children": children,
                    "size": len(value)}
        if isinstance(value, str):
            return {"kind": "string", "text": reading_text(value), "raw": value,
                    "long": len(value) > 160 or "\n" in value or "\r" in value}
        return {"kind": "scalar", "text": json.dumps(value, ensure_ascii=False, indent=2)}

    try:
        return node(json.loads(text))
    except (ValueError, TypeError, RecursionError):
        return None
