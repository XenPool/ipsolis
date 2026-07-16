"""Air-gap guard: templates + static must not load assets from external origins.

The ipSolis runtime is air-gapped — the browser may load CSS/JS/fonts only from
the ipSolis host. This test scans the templates and static assets for external
*asset loaders* (script/link/img src+href, CSS url()/@import, fetch, the Monaco
AMD base path) and fails if any non-self origin appears.

It deliberately does NOT flag click-time ``<a href="https://…">`` links (they
fire on click, not on load) or the admin-configured ``{{ app_logo_url }}`` image
(a tenant setting, documented to be local in air-gap), or XML namespaces.
"""
from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_TEMPLATES = _APP / "templates"
_STATIC = _APP / "static"

# External asset-loader patterns (NOT click links). Each must not point off-host.
_PATTERNS = [
    re.compile(r"<script[^>]*\bsrc\s*=\s*['\"]https?://", re.I),
    re.compile(r"<link[^>]*\bhref\s*=\s*['\"]https?://", re.I),
    re.compile(r"<img[^>]*\bsrc\s*=\s*['\"]https?://", re.I),
    re.compile(r"url\(\s*['\"]?https?://", re.I),          # CSS url()
    re.compile(r"@import\s+['\"]?https?://", re.I),
    re.compile(r"\bfetch\(\s*['\"]https?://", re.I),
    re.compile(r"paths\s*:\s*\{[^}]*vs\s*:\s*['\"]https?://", re.I),  # Monaco AMD base
]

# Namespaces / non-network URLs to ignore.
_IGNORE = ("www.w3.org", "://localhost")  # w3 namespaces; localhost is same-host dev


def _iter_files():
    for base, exts in ((_TEMPLATES, {".html"}), (_STATIC, {".js", ".css"})):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in exts:
                yield p


def test_no_external_asset_origins():
    violations: list[str] = []
    for path in _iter_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if any(ig in line for ig in _IGNORE):
                continue
            if any(pat.search(line) for pat in _PATTERNS):
                violations.append(f"{path.relative_to(_APP.parent)}:{lineno}: {line.strip()[:120]}")

    assert not violations, (
        "External asset origins found (the runtime must be air-gapped — vendor "
        "these locally under /static/vendor and reference /static/...):\n  "
        + "\n  ".join(violations)
    )
