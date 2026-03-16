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
