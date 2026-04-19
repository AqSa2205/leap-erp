from django import template

register = template.Library()


@register.filter
def getattr_filter(obj, attr):
    return getattr(obj, attr, '')


@register.filter
def get_field(form, field_name):
    try:
        return form[field_name]
    except KeyError:
        return ''


@register.filter
def get_field_value(form, field_name):
    """Return just the value of a form field (not the rendered widget)."""
    try:
        return form[field_name].value() or ''
    except (KeyError, AttributeError):
        return ''


@register.filter
def get_item(d, key):
    """Dict item access by key in templates."""
    if d is None:
        return []
    try:
        return d.get(key, [])
    except (AttributeError, TypeError):
        return []
