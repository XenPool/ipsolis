"""Parse PowerShell `param()` blocks into the script_modules.param_schema format.

Returns a list of dicts compatible with the rest of the system:
    [{"name": "VMName", "type": "string", "required": True, "default": "..."}]
"""

from __future__ import annotations

import re

_TYPE_MAP = {
    "string": "string",
    "int": "int",
    "int16": "int",
    "int32": "int",
    "int64": "int",
    "long": "int",
    "byte": "int",
    "double": "string",
    "bool": "bool",
    "boolean": "bool",
    "switch": "bool",
    "hashtable": "json",
    "array": "json",
    "object": "json",
    "pscredential": "string",
    "datetime": "string",
}


def parse_powershell_params(script: str) -> list[dict]:
    """Extract a parameter schema from the first `param( ... )` block in a PS script."""
    if not script:
        return []

    body = _extract_param_body(script)
    if body is None:
        return []
    return _parse_param_body(body)


def _extract_param_body(script: str) -> str | None:
    """Find the first top-level `param( ... )` and return its inner text."""
    lower = script.lower()
    pos = 0
    while True:
        idx = lower.find("param", pos)
        if idx == -1:
            return None
        # Word boundary on the left
        if idx > 0 and (script[idx - 1].isalnum() or script[idx - 1] == "_"):
            pos = idx + 5
            continue
        # Skip whitespace, expect '('
        j = idx + 5
        while j < len(script) and script[j] in " \t\r\n":
            j += 1
        if j >= len(script) or script[j] != "(":
            pos = idx + 5
            continue
        # Match parens (respecting strings)
        depth = 1
        k = j + 1
        in_str: str | None = None
        while k < len(script) and depth > 0:
            ch = script[k]
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return script[j + 1 : k]
            k += 1
        return None


def _split_top_level(body: str) -> list[str]:
    """Split a param body on commas at depth 0 (ignoring strings and brackets)."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str: str | None = None
    for ch in body:
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'"):
            in_str = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _parse_param_body(body: str) -> list[dict]:
    out: list[dict] = []
    for raw in _split_top_level(body):
        entry = _parse_single_param(raw)
        if entry:
            out.append(entry)
    return out


def _strip_leading_attributes(text: str) -> tuple[str, bool]:
    """Strip any leading `[Attr(...)]` attributes. Returns (rest, mandatory)."""
    mandatory = False
    s = text.lstrip()
    while s.startswith("["):
        # Match brackets with paren-awareness so [Parameter(Mandatory=$true)] is handled.
        depth_b = 1
        depth_p = 0
        i = 1
        in_str: str | None = None
        while i < len(s) and depth_b > 0:
            ch = s[i]
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == "(":
                depth_p += 1
            elif ch == ")":
                depth_p -= 1
            elif ch == "[":
                depth_b += 1
            elif ch == "]" and depth_p == 0:
                depth_b -= 1
                if depth_b == 0:
                    break
            i += 1
        attr = s[: i + 1]
        # Heuristic: looks like a *type* hint, not a Parameter attribute? Stop peeling.
        # Type hints have no parens and no '=' inside.
        inner = attr[1:-1].strip()
        looks_like_type = (
            "(" not in inner
            and "=" not in inner
            and not inner.lower().startswith("parameter")
            and not inner.lower().startswith("validate")
            and not inner.lower().startswith("allow")
            and not inner.lower().startswith("alias")
        )
        if looks_like_type:
            break
        if re.search(r"Mandatory\s*=\s*\$true", attr, re.IGNORECASE):
            mandatory = True
        s = s[i + 1 :].lstrip()
    return s, mandatory


def _parse_single_param(text: str) -> dict | None:
    s = text.strip()
    if not s:
        return None
    s, mandatory = _strip_leading_attributes(s)

    # Optional [Type] hint
    type_str = "string"
    if s.startswith("["):
        m = re.match(r"\[([^\[\]]+)\]\s*", s)
        if m:
            raw_type = m.group(1).strip().lower()
            # Strip [] suffix for arrays like [string[]]
            raw_type = raw_type.rstrip("[]")
            type_str = _TYPE_MAP.get(raw_type, "string")
            s = s[m.end() :]

    # Variable: $Name
    m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)\s*", s)
    if not m:
        return None
    name = m.group(1)
    s = s[m.end() :]

    # Optional default value
    default: str | None = None
    if s.startswith("="):
        raw = s[1:].strip()
        if (len(raw) >= 2) and raw[0] in ('"', "'") and raw[-1] == raw[0]:
            default = raw[1:-1]
        else:
            default = raw or None

    entry: dict = {"name": name, "type": type_str, "required": mandatory}
    if default:
        entry["default"] = default
    return entry
