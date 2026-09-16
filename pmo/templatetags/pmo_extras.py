from django import template

register = template.Library()


@register.filter
def matrix_cell_text(row, column_key):
    """row is {'cells': {key: {'text': str}, ...}} — pull one cell's text by
    the column's key, since Django templates can't do `row.cells.col.key`
    with a variable key."""
    cells = (row or {}).get('cells') or {}
    cell = cells.get(column_key) or {}
    return cell.get('text', '')
