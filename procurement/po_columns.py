"""The purchase-order item columns, defined once.

The PDF and the Excel export both render the same line items, and each used to
carry its own list of column headings. They had already drifted — the same
column was called "S.No." on one and "S No." on the other, and "Item
Description" against "Item Descriptions / Specification" — with nothing to
make that visible, because the two lists lived four thousand lines apart.

This table is the single place that knows which columns exist, in what order,
which carry pricing, and how wide they sit on the page.

**The wording differences are preserved, not resolved.** Both labels are
recorded here and the pairs that disagree are marked, because changing what a
supplier sees on a purchase order is a business decision rather than a tidying
one. What has changed is that the disagreement is now in one table where
somebody can settle it, instead of being invisible.
"""
from dataclasses import dataclass

from .page_geometry import distribute


@dataclass(frozen=True)
class ItemColumn:
    """One column of the purchase-order item table.

    ``pdf`` or ``excel`` is None where that document does not carry the column
    — the Excel has a System column the PDF has never shown.

    Labels are format strings so the ones naming a currency can take it. Widths
    are millimetres; the unpriced PDF drops the two pricing columns and shares
    their width out between Description and Remarks, which is why each column
    carries both figures.
    """

    key: str
    pdf: str | None
    excel: str | None
    priced_only: bool = False
    #: Relative width, not millimetres. The actual widths are derived from
    #: page_geometry.CONTENT_WIDTH_MM so the table always fits its frame —
    #: these numbers only decide how the space is shared out.
    weight: float | None = None
    weight_unpriced: float | None = None
    pdf_centered: bool = False
    #: True where the PDF and Excel wordings differ. Recorded rather than
    #: reconciled — see the module docstring.
    labels_differ: bool = False


PO_ITEM_COLUMNS = [
    ItemColumn('serial_number', 'S.No.', 'S No.',
               weight=12, weight_unpriced=12, labels_differ=True),
    ItemColumn('system', None, 'System'),
    ItemColumn('make_model', 'Make/Model', 'Make/Model',
               weight=25, weight_unpriced=30),
    ItemColumn('description', 'Item Description', 'Item Descriptions / Specification',
               weight=55, weight_unpriced=80, labels_differ=True),
    ItemColumn('quantity', 'Qty', 'Quantity',
               weight=15, weight_unpriced=16, pdf_centered=True,
               labels_differ=True),
    ItemColumn('uom', 'UOM', 'UOM',
               weight=14, weight_unpriced=16),
    ItemColumn('rate_per_unit', 'Rate/Unit', 'Rate/unit ({currency})',
               priced_only=True, weight=22, labels_differ=True),
    ItemColumn('total_value', 'Total ({currency})', 'Total Value ({currency})',
               priced_only=True, weight=22, labels_differ=True),
    ItemColumn('remarks', 'Remarks', 'Remarks',
               weight=20, weight_unpriced=31),
]


def pdf_columns(unpriced=False):
    """The PDF's columns in order, as (label, width_mm, centered) triples.

    Widths are derived from the page geometry rather than carried here, so the
    table fills its frame exactly in both variants and cannot drift wider than
    the page. The unpriced copy drops the two pricing columns and its weights
    share the freed space out between Description and Remarks.
    """
    chosen = [c for c in PO_ITEM_COLUMNS
              if c.pdf is not None and not (unpriced and c.priced_only)]
    weights = [(c.weight_unpriced if unpriced else c.weight) for c in chosen]
    widths = distribute(weights)
    return [(c.pdf, w, c.pdf_centered) for c, w in zip(chosen, widths)]


def excel_headers(currency):
    """The Excel header row in order."""
    return [c.excel.format(currency=currency)
            for c in PO_ITEM_COLUMNS if c.excel is not None]


def divergent_labels():
    """Columns the two documents name differently.

    Exposed so the difference can be reported rather than discovered. Used by
    the tests to keep the count from growing quietly.
    """
    return [(c.key, c.pdf, c.excel) for c in PO_ITEM_COLUMNS if c.labels_differ]
