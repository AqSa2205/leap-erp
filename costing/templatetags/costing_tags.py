from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def mul(value, arg):
    """Multiply a numeric value by arg. Returns Decimal for precision."""
    try:
        return (Decimal(str(value)) * Decimal(str(arg))).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return value
