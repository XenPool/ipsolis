"""SCIM 2.0 filter grammar (RFC 7644 §3.4.2.2) — parser + evaluator.

A tokenizer + recursive-descent parser turns a SCIM ``filter=`` string into a
small AST, and ``evaluate()`` tests a user-resource dict against it. ip·Solis
derives users from ``orders.user_email`` (+ the SCIM identity projection), so
only the attributes it can actually produce are resolvable — ``userName`` / ``id``
/ ``emails[.value]`` (→ email), ``displayName`` / ``name.formatted`` (→ name),
``active``, ``externalId``. A filter over an unknown attribute is valid grammar
but simply never matches (returns no results) rather than erroring.

Supported: comparison ops ``eq ne co sw ew gt ge lt le`` + presence ``pr``;
logical ``and`` / ``or`` / ``not``; parenthesised grouping. Values may be a
quoted string, number, ``true`` / ``false``, or ``null``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class SCIMFilterError(ValueError):
    """Raised on a malformed filter — surfaced as HTTP 400 scimType=invalidFilter."""


# ── AST ──────────────────────────────────────────────────────────────────────

@dataclass
class Compare:
    attr: str
    op: str
    value: Any


@dataclass
class Present:
    attr: str


@dataclass
class Not:
    node: Any


@dataclass
class And:
    left: Any
    right: Any


@dataclass
class Or:
    left: Any
    right: Any


# ── Tokenizer ────────────────────────────────────────────────────────────────

_COMPARE_OPS = {"eq", "ne", "co", "sw", "ew", "gt", "ge", "lt", "le"}
_TOKEN_RE = re.compile(
    r"""
      \s+                         # whitespace (skipped)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | "(?P<string>(?:[^"\\]|\\.)*)"   # double-quoted string with escapes
    | (?P<word>[A-Za-z0-9_.:$\-]+)    # attr paths / operators / keywords / numbers
    """,
    re.VERBOSE,
)


def _tokenize(s: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    n = len(s)
    while pos < n:
        m = _TOKEN_RE.match(s, pos)
        if not m or m.end() == pos:
            raise SCIMFilterError(f"Unexpected character at position {pos}: {s[pos:pos+12]!r}")
        pos = m.end()
        if m.lastgroup is None:
            continue  # whitespace
        kind = m.lastgroup
        if kind == "lparen":
            tokens.append(("(", "("))
        elif kind == "rparen":
            tokens.append((")", ")"))
        elif kind == "string":
            tokens.append(("value", _unescape(m.group("string"))))
        else:  # word — classify as keyword / operator / literal / attr
            w = m.group("word")
            lw = w.lower()
            if lw in ("and", "or", "not"):
                tokens.append((lw, lw))
            elif lw == "pr":
                tokens.append(("pr", "pr"))
            elif lw in _COMPARE_OPS:
                tokens.append(("op", lw))
            elif lw == "true":
                tokens.append(("value", True))
            elif lw == "false":
                tokens.append(("value", False))
            elif lw == "null":
                tokens.append(("value", None))
            elif _is_number(w):
                tokens.append(("value", float(w) if "." in w or "e" in lw else int(w)))
            else:
                tokens.append(("attr", w))
    return tokens


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _is_number(w: str) -> bool:
    try:
        float(w)
        return True
    except ValueError:
        return False


# ── Recursive-descent parser (precedence: or < and < not < primary) ──────────

class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]]):
        self.toks = tokens
        self.i = 0

    def _peek(self) -> tuple[str, Any] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self) -> tuple[str, Any]:
        if self.i >= len(self.toks):
            raise SCIMFilterError("Unexpected end of filter")
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self) -> Any:
        node = self._parse_or()
        if self._peek() is not None:
            raise SCIMFilterError(f"Trailing tokens in filter near {self._peek()!r}")
        return node

    def _parse_or(self) -> Any:
        node = self._parse_and()
        while (t := self._peek()) and t[0] == "or":
            self._next()
            node = Or(node, self._parse_and())
        return node

    def _parse_and(self) -> Any:
        node = self._parse_not()
        while (t := self._peek()) and t[0] == "and":
            self._next()
            node = And(node, self._parse_not())
        return node

    def _parse_not(self) -> Any:
        if (t := self._peek()) and t[0] == "not":
            self._next()
            return Not(self._parse_primary())
        return self._parse_primary()

    def _parse_primary(self) -> Any:
        t = self._peek()
        if t is None:
            raise SCIMFilterError("Expected expression")
        if t[0] == "(":
            self._next()
            node = self._parse_or()
            close = self._next()
            if close[0] != ")":
                raise SCIMFilterError("Expected ')'")
            return node
        if t[0] == "attr":
            return self._parse_comparison()
        raise SCIMFilterError(f"Expected attribute or '(', got {t[1]!r}")

    def _parse_comparison(self) -> Any:
        attr = self._next()[1]
        t = self._next()
        if t[0] == "pr":
            return Present(attr)
        if t[0] != "op":
            raise SCIMFilterError(f"Expected operator after {attr!r}, got {t[1]!r}")
        val = self._next()
        if val[0] != "value":
            raise SCIMFilterError(f"Expected value after '{t[1]}', got {val[1]!r}")
        return Compare(attr, t[1], val[1])


def parse_filter(filter_str: str) -> Any:
    """Parse a SCIM filter string into an AST. Raises ``SCIMFilterError``."""
    if not filter_str or not filter_str.strip():
        raise SCIMFilterError("Empty filter")
    return _Parser(_tokenize(filter_str)).parse()


# ── Evaluation ───────────────────────────────────────────────────────────────

# Map a SCIM attribute path (last segment, lowercased) to a resource key.
_ATTR_MAP = {
    "username": "email",
    "id": "email",
    "emails": "email",
    "value": "email",          # emails.value → value
    "displayname": "name",
    "formatted": "name",        # name.formatted → formatted
    "active": "active",
    "externalid": "external_id",
}


def _resolve(attr: str, resource: dict) -> Any:
    key = _ATTR_MAP.get(attr.split(".")[-1].split(":")[-1].lower())
    return resource.get(key) if key else None


def _cmp_strings(op: str, actual: Any, expected: Any) -> bool:
    a = "" if actual is None else str(actual)
    e = "" if expected is None else str(expected)
    al, el = a.lower(), e.lower()
    if op == "eq":
        return al == el
    if op == "ne":
        return al != el
    if op == "co":
        return el in al
    if op == "sw":
        return al.startswith(el)
    if op == "ew":
        return al.endswith(el)
    if op == "gt":
        return a > e
    if op == "ge":
        return a >= e
    if op == "lt":
        return a < e
    if op == "le":
        return a <= e
    return False


def _eval_compare(node: Compare, resource: dict) -> bool:
    actual = _resolve(node.attr, resource)
    expected = node.value
    # Boolean attribute (active) — eq/ne against a real bool.
    if isinstance(expected, bool) or isinstance(actual, bool):
        a = bool(actual)
        e = bool(expected)
        if node.op == "eq":
            return a == e
        if node.op == "ne":
            return a != e
        return False
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            a = float(actual)
        except (TypeError, ValueError):
            return False
        e = float(expected)
        return {
            "eq": a == e, "ne": a != e, "gt": a > e, "ge": a >= e,
            "lt": a < e, "le": a <= e, "co": str(int(e)) in str(actual),
            "sw": str(actual).startswith(str(e)), "ew": str(actual).endswith(str(e)),
        }.get(node.op, False)
    # Unknown/unresolvable attribute → never matches (except ne, which is
    # vacuously true only for eq-style; keep it strict: no match).
    if actual is None:
        return node.op == "ne"  # "not equal to X" is true when we have no value
    return _cmp_strings(node.op, actual, expected)


def evaluate(node: Any, resource: dict) -> bool:
    """Test a user-resource dict against a parsed filter AST."""
    if isinstance(node, And):
        return evaluate(node.left, resource) and evaluate(node.right, resource)
    if isinstance(node, Or):
        return evaluate(node.left, resource) or evaluate(node.right, resource)
    if isinstance(node, Not):
        return not evaluate(node.node, resource)
    if isinstance(node, Present):
        return _resolve(node.attr, resource) not in (None, "")
    if isinstance(node, Compare):
        return _eval_compare(node, resource)
    return False


def simple_email_equality(node: Any) -> str | None:
    """Fast-path: if the whole filter is ``userName|id|emails eq "<email>"``,
    return the lowercased email so the caller can do an indexed single lookup
    instead of scanning every user. Returns ``None`` otherwise.
    """
    if isinstance(node, Compare) and node.op == "eq" and isinstance(node.value, str):
        if _ATTR_MAP.get(node.attr.split(".")[-1].split(":")[-1].lower()) == "email":
            return node.value.strip().lower()
    return None
