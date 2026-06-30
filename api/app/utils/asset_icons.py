"""Built-in monochrome line icons for asset-type tile logos.

These are bundled, license-free (hand-authored) SVGs covering typical IT asset
types. The asset-type form offers them in a picker; selecting one stores its
SVG as a ``data:`` URL in ``asset_types.logo`` — exactly the same shape an
uploaded logo produces — so storage, the catalog tile preview, and the portal
rendering path all work unchanged.

Icons are single-tone (slate ``#64748b``) so they read acceptably on both light
and dark tile backgrounds. They are served as ``<img>`` (data URL), which cannot
inherit ``currentColor``, hence the baked stroke colour.
"""
from __future__ import annotations

import base64

# Shared stroke colour — a mid slate that stays legible on light and dark tiles.
_STROKE = "#64748b"

# Each entry: (id, human label, inner SVG markup). The inner markup is wrapped
# by ``_svg()`` with a common 24x24 viewBox and stroke style.
_ICONS: list[tuple[str, str, str]] = [
    ("desktop", "Desktop / VDI",
     '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/>'),
    ("laptop", "Laptop",
     '<rect x="5" y="5" width="14" height="10" rx="1"/><path d="M3 19h18l-1.5-2.5h-15L3 19Z"/>'),
    ("server", "Server",
     '<rect x="3" y="4" width="18" height="7" rx="1"/><rect x="3" y="13" width="18" height="7" rx="1"/>'
     '<path d="M7 7.5h.01M7 16.5h.01"/>'),
    ("vm", "Virtual machine",
     '<rect x="3" y="4" width="14" height="11" rx="1"/><path d="M9 19h10a2 2 0 0 0 2-2V9"/>'
     '<path d="M8 8.5l3 2-3 2v-4Z"/>'),
    ("database", "Database",
     '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'
     '<path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>'),
    ("mailbox", "Mailbox / Email",
     '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/>'),
    ("application", "Application",
     '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6.5 6.5h.01M9 6.5h.01"/>'),
    ("license", "License / Key",
     '<circle cx="8" cy="8" r="4"/><path d="m11 11 8 8M16 16l2-2M14 18l2-2"/>'),
    ("phone", "Smartphone",
     '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>'),
    ("monitor", "Monitor",
     '<rect x="2" y="4" width="20" height="13" rx="1"/><path d="M9 21h6M12 17v4"/>'),
    ("cloud", "Cloud service",
     '<path d="M7 18a4 4 0 0 1-.5-7.97A5.5 5.5 0 0 1 17 9.5a3.5 3.5 0 0 1 .5 6.96"/>'
     '<path d="M7 18h10.5"/>'),
    ("storage", "Storage / Disk",
     '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="4"/>'
     '<path d="M12 10v.01"/>'),
]


def _svg(inner: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="' + _STROKE + '" '
        'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        + inner + "</svg>"
    )


def _data_url(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + b64


def builtin_asset_icons() -> list[dict[str, str]]:
    """Return the bundled icons as ``{id, name, data_url}`` for the picker."""
    return [
        {"id": icon_id, "name": name, "data_url": _data_url(_svg(inner))}
        for icon_id, name, inner in _ICONS
    ]
