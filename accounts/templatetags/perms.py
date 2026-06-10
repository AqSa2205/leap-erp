from django import template

register = template.Library()


@register.filter(name='can')
def can(user, codename):
    """Usage: {% if user|can:'costing.access' %}"""
    return bool(user and getattr(user, 'is_authenticated', False) and user.has_capability(codename))
