"""Safe rendering for user-authored Terms & Conditions / PO-notes content.

The Terms editor (TinyMCE) lets internal users produce rich HTML that is later
rendered back into pages. Rendering that HTML with ``|safe`` unsanitised is a
stored-XSS vector (an editor could save ``<script>`` / ``<img onerror=…>`` that
then runs in any viewer's browser). These filters sanitise the HTML through a
strict allowlist that only permits the formatting the toolbar can produce, and
fall back to safe line-broken plain text for legacy (pre-TinyMCE) content.
"""
from django import template
from django.template.defaultfilters import truncatewords
from django.utils.html import linebreaks, strip_tags
from django.utils.safestring import mark_safe

import bleach
from bleach.css_sanitizer import CSSSanitizer

register = template.Library()

# Exactly the tags / attributes / CSS properties the Terms toolbar emits.
_ALLOWED_TAGS = ['p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u',
                 'ul', 'ol', 'li']
_ALLOWED_ATTRS = {'span': ['style'], 'p': ['style'], 'div': ['style'],
                  'li': ['style'], 'ul': ['style'], 'ol': ['style']}
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=[
    'color', 'background-color', 'font-size', 'text-decoration', 'font-weight',
    'font-style'])


def sanitize_terms_html(content):
    """Bleach-clean rich terms HTML to the toolbar's allowlist. Anything outside
    it (scripts, event handlers, arbitrary tags/styles) is stripped."""
    return bleach.clean(
        content or '', tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS,
        css_sanitizer=_CSS_SANITIZER, strip=True)


@register.filter
def render_terms(content):
    """Full display of terms/PO-notes. Rich HTML is sanitised; legacy plain text
    (no tags) is escaped and its newlines turned into <br> so it doesn't run on."""
    if not content:
        return ''
    if '<' in content:
        return mark_safe(sanitize_terms_html(content))
    return mark_safe(linebreaks(content, autoescape=True))


@register.filter
def terms_preview(content, words=15):
    """Short, safe preview snippet: tags stripped to plain text, truncated. The
    template autoescapes the result (no |safe needed), so it can't inject HTML."""
    return truncatewords(strip_tags(content or ''), words)
