"""Render admin-authored markdown to sanitized HTML.

Used for the long-form ``help_text`` on asset definitions, surfaced to
end users in the portal at request time. Admins can write rich-but-safe
content (paragraphs, bold/italic, links, lists, code) without being able
to inject scripts or arbitrary attributes.
"""
from __future__ import annotations

import bleach
import markdown as md_lib

# Conservative allowlist — scripts, iframes, on* handlers, style attrs all rejected.
_ALLOWED_TAGS = frozenset({
    "p", "br", "strong", "em", "code", "pre", "blockquote",
    "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "hr",
})
_ALLOWED_ATTRS = {
    "a": ["href", "title"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def render_markdown(text: str | None) -> str:
    """Render ``text`` (admin-supplied markdown) to safe HTML.

    Empty/None input returns an empty string. Output is suitable for
    inlining into a Jinja2 template with ``| safe`` because every tag
    and attribute has been allowlisted.
    """
    if not text or not text.strip():
        return ""
    raw_html = md_lib.markdown(
        text,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html",
    )
    cleaned = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    # External links open in new tab without leaking referrer
    return bleach.linkify(
        cleaned,
        callbacks=[
            lambda attrs, new: {
                **attrs,
                (None, "target"): "_blank",
                (None, "rel"): "noopener noreferrer",
            }
        ],
        skip_tags=["pre", "code"],
    )
