"""Page geometry for the procurement documents, in millimetres.

One place that knows how big the page is and how much of it can be written on.
Everything that lays out a table should derive its widths from
``CONTENT_WIDTH_MM`` rather than carrying its own total.

This exists because three different assumptions about the usable width had
grown up side by side in the purchase order: the page header table summed to
180mm, the info block to 169mm, and the item table to 185mm — five millimetres
wider than the frame it was being drawn into. Nothing caught it, because
nothing derived from anything.

Deliberately plain numbers with no imports. The values are needed by the
column definitions, which have no business importing a PDF library, and by the
document builder, which does.
"""

#: A4 portrait, the size every procurement document is issued at.
PAGE_WIDTH_MM = 210.0
PAGE_HEIGHT_MM = 297.0

#: Margins used by ``pdf_common.a4_portrait_document``. The footer is drawn
#: inside the bottom margin rather than the content frame.
MARGIN_MM = 15.0

#: What a table may actually occupy. Derived, not asserted — change a margin
#: and every table that reads this follows.
CONTENT_WIDTH_MM = PAGE_WIDTH_MM - 2 * MARGIN_MM


def distribute(weights, total_mm=None):
    """Share ``total_mm`` out across ``weights``, preserving their proportions.

    The weights are the relative sizes somebody chose for the columns; this
    turns them into millimetres that fit the page. The final column takes
    whatever rounding leaves over, so the result sums to ``total_mm`` exactly
    rather than to within a floating-point hair of it — a table one part in a
    million too wide is still too wide.
    """
    if total_mm is None:
        total_mm = CONTENT_WIDTH_MM
    if not weights:
        return []
    scale = float(sum(weights))
    widths, running = [], 0.0
    for index, weight in enumerate(weights):
        if index == len(weights) - 1:
            widths.append(round(total_mm - running, 4))
        else:
            width = round(weight / scale * total_mm, 4)
            running += width
            widths.append(width)
    return widths
