"""PDF helpers shared by the procurement exports.

Moved out of views.py unchanged. These are used by the purchase-order,
delivery-note and inventory documents alike, so they belong beside those
documents rather than inside the module that happens to serve them over HTTP.

Nothing here knows about a request. Everything takes plain values and returns
flowables, fonts or strings.
"""

from decimal import Decimal, InvalidOperation

from .models import PurchaseOrder

# The number-words tables belong with _amount_in_words, the only thing
# that reads them.
_NUM_UNITS = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine']
_NUM_TEENS = ['Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
_NUM_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']
_NUM_SCALES = ['', 'Thousand', 'Million', 'Billion', 'Trillion']


def _amount_in_words(value, currency='SAR'):
    """Spell out a numeric amount for invoice/PO use.

    Example: Decimal('25420.50') with currency='SAR' →
    'Twenty Five Thousand Four Hundred Twenty SAR and Fifty Halalas Only'.

    Splits into integer SAR and 2-digit fractional Halalas, words each part
    in English, and appends ' Only' as is conventional on a payment doc.
    Returns an empty string for None / unparseable input.
    """
    if value is None:
        return ''
    try:
        d = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return ''

    # Operate on the absolute value so the sub-helpers never see a negative.
    sign = ''
    if d < 0:
        sign = 'Negative '
        d = -d

    integer_part = int(d)
    halala_part = int(((d - integer_part) * 100).to_integral_value())

    def _under_hundred(n):
        if n < 10:
            return _NUM_UNITS[n]
        if n < 20:
            return _NUM_TEENS[n - 10]
        t, u = divmod(n, 10)
        return _NUM_TENS[t] if u == 0 else f'{_NUM_TENS[t]} {_NUM_UNITS[u]}'

    def _under_thousand(n):
        if n < 100:
            return _under_hundred(n)
        h, r = divmod(n, 100)
        return f'{_NUM_UNITS[h]} Hundred' if r == 0 else f'{_NUM_UNITS[h]} Hundred {_under_hundred(r)}'

    def _spell(n):
        if n == 0:
            return 'Zero'
        parts, i = [], 0
        while n > 0:
            chunk = n % 1000
            if chunk:
                scale = _NUM_SCALES[i] if i < len(_NUM_SCALES) else ''
                parts.append(f'{_under_thousand(chunk)} {scale}'.strip())
            n //= 1000
            i += 1
        return ' '.join(reversed(parts))

    main_words = _spell(integer_part)
    base = f'{sign}{main_words} {currency}'
    if halala_part:
        # Fractional unit name depends on the currency (Halalas / Cents / Fils).
        singular, plural = PurchaseOrder.CURRENCY_FRACTIONS.get(
            currency, ('Halala', 'Halalas'))
        suffix = singular if halala_part == 1 else plural
        return f'{base} and {_under_hundred(halala_part)} {suffix} Only'
    return f'{base} Only'

def _make_numbered_canvas(draft=False, footer_left=None, footer_left2=None, footer_center=None):
    """Return a two-pass Canvas subclass that draws "Page X of Y" at the
    bottom of every page. Used by procurement PDF exports for consistent
    pagination across PO / DN / Inventory / Summary outputs.

    By default the page label is centred. When ``footer_left`` and/or
    ``footer_center`` are given (e.g. the PO export passes the company name
    and PO number), a three-part footer is drawn instead — ``footer_left``
    left-aligned, ``footer_center`` centred, and the page label right-aligned.
    ``footer_left2`` adds a second line beneath ``footer_left`` (e.g. the
    Material Requisition + revision), with the centre/right text vertically
    centred against the two-line left block.

    When ``draft=True`` a large diagonal "DRAFT" watermark is layered on
    every page so an unapproved export cannot be mistaken for a final,
    signed document.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas

    class NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_pages = []

        def showPage(self):
            self._saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            page_count = len(self._saved_pages)
            for state in self._saved_pages:
                self.__dict__.update(state)
                try:
                    page_w, page_h = self._pagesize
                    if draft:
                        self.saveState()
                        self.setFillColor(colors.HexColor('#C41E3A'))
                        try:
                            self.setFillAlpha(0.10)
                        except Exception:
                            pass
                        self.setFont('Helvetica-Bold', 120)
                        self.translate(page_w / 2, page_h / 2)
                        self.rotate(45)
                        self.drawCentredString(0, 0, 'DRAFT')
                        self.restoreState()
                    self.setFont('Helvetica', 8)
                    self.setFillColor(colors.HexColor('#6c757d'))
                    page_label = f'Page {self._pageNumber} of {page_count}'
                    if footer_left is not None or footer_left2 is not None or footer_center is not None:
                        # 3-part footer: company (left) · reference (center) · page (right).
                        # footer_left2 stacks a second line under the company name.
                        left_x, right_x = 15 * mm, page_w - 15 * mm
                        if footer_left2:
                            if footer_left:
                                self.drawString(left_x, 10 * mm, footer_left)
                            self.drawString(left_x, 6 * mm, footer_left2)
                        elif footer_left:
                            self.drawString(left_x, 8 * mm, footer_left)
                        mid_y = 8 * mm  # vertically centred against the (up to) two left lines
                        if footer_center:
                            self.drawCentredString(page_w / 2, mid_y, footer_center)
                        self.drawRightString(right_x, mid_y, page_label)
                    else:
                        self.drawCentredString(page_w / 2, 8 * mm, page_label)
                except Exception:
                    pass
                super().showPage()
            super().save()

    return NumberedCanvas

def _arabic_font():
    """Register (once) and return the Arabic-capable font name for PDF headers,
    or None if the TTF isn't present. The font lives at
    ``static/fonts/Amiri-Regular.ttf`` (OFL) — committed separately so it ships
    to production. Returning None lets callers fall back to the English-only
    header instead of crashing."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from django.contrib.staticfiles.finders import find as find_static
        if 'ArabicHeader' in pdfmetrics.getRegisteredFontNames():
            return 'ArabicHeader'
        path = find_static('fonts/Amiri-Regular.ttf')
        if not path:
            return None
        pdfmetrics.registerFont(TTFont('ArabicHeader', path))
        return 'ArabicHeader'
    except Exception:
        return None

def _shape_arabic(text):
    """Reshape Arabic to its joined presentation forms and apply the bidi
    algorithm, so it renders correctly (right-to-left, connected) in reportlab,
    which does neither on its own. Falls back to the raw text on any error."""
    try:
        import arabic_reshaper
        try:
            from bidi import get_display
        except ImportError:  # older python-bidi
            from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text

def _tinymce_html_to_reportlab_lines(html):
    # Converts TinyMCE-produced HTML into a list of (markup, max_pt) tuples,
    # one per block (paragraph/list item). markup is ReportLab Paragraph-
    # markup text; max_pt is the largest inline font-size (in points) used
    # within that line, or None if no explicit size was set - callers use
    # this to give large lines enough leading/line-height so they don't
    # collide with the paragraph that follows (a fixed base leading is too
    # tight for a much larger inline font-size override).
    #
    # Only handles the specific inline styles our TinyMCE toolbar can
    # actually produce - font-size, text color, background color
    # (highlight), bold, italic, underline, plus <ul>/<ol> lists. Each
    # flushed line is self-contained: any inline tags still open at a
    # <br>/<p> boundary are closed before the line ends and reopened for
    # whatever text follows, so every line is valid on its own.
    import re
    from html.parser import HTMLParser
    from xml.sax.saxutils import escape as xml_escape

    # Legacy terms are pre-TinyMCE plain text (newline-delimited). The HTML
    # parser below only breaks on block tags, so a bare '\n' would collapse the
    # whole thing into one run-on line — handle plain text explicitly instead.
    if html and '<' not in html:
        return [(xml_escape(line.strip()), None)
                for line in html.splitlines() if line.strip()]

    font_size_re = re.compile(r'font-size:\s*([\d.]+)pt')
    font_color_re = re.compile(r'(?<!background-)color:\s*(#[0-9a-fA-F]{6})')
    back_color_re = re.compile(r'background-color:\s*(#[0-9a-fA-F]{6})')

    class Converter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.lines = []  # list of (markup, max_pt)
            self.current = []
            self.open_tags = []  # list of (open_markup, close_markup, size_pt) currently open
            self.current_max_pt = None
            self.list_stack = []
            self.list_counters = []

        def flush_current(self, prefix=''):
            closers = ''.join(close for _, close, _ in reversed(self.open_tags))
            text = (''.join(self.current) + closers).strip()
            if text:
                self.lines.append((prefix + text, self.current_max_pt))
            openers = ''.join(open_ for open_, _, _ in self.open_tags)
            self.current = [openers] if openers else []
            self.current_max_pt = max(
                (pt for _, _, pt in self.open_tags if pt), default=None)

        def _note_size(self, pt):
            if pt and (self.current_max_pt is None or pt > self.current_max_pt):
                self.current_max_pt = pt

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ('p', 'div', 'br'):
                self.flush_current()
            elif tag in ('ul', 'ol'):
                self.list_stack.append(tag)
                self.list_counters.append(0)
            elif tag == 'li':
                self.flush_current()
            elif tag in ('strong', 'b'):
                self.current.append('<b>')
                self.open_tags.append(('<b>', '</b>', None))
            elif tag in ('em', 'i'):
                self.current.append('<i>')
                self.open_tags.append(('<i>', '</i>', None))
            elif tag == 'u':
                self.current.append('<u>')
                self.open_tags.append(('<u>', '</u>', None))
            elif tag == 'span':
                style = attrs.get('style', '')
                open_markup = ''
                close_markup = ''
                size_pt = None
                size_match = font_size_re.search(style)
                color_match = font_color_re.search(style)
                back_match = back_color_re.search(style)
                if size_match or color_match or back_match:
                    font_attrs = ''
                    if size_match:
                        size_pt = int(float(size_match.group(1)))
                        font_attrs += ' size="%d"' % size_pt
                        self._note_size(size_pt)
                    if color_match:
                        font_attrs += ' color="%s"' % color_match.group(1)
                    if back_match:
                        font_attrs += ' backColor="%s"' % back_match.group(1)
                    open_markup += '<font%s>' % font_attrs
                    close_markup = '</font>' + close_markup
                if 'text-decoration: underline' in style or 'text-decoration:underline' in style:
                    open_markup += '<u>'
                    close_markup = '</u>' + close_markup
                if open_markup:
                    self.current.append(open_markup)
                    self.open_tags.append((open_markup, close_markup, size_pt))
                else:
                    self.open_tags.append(('', '', None))
            else:
                self.open_tags.append(('', '', None))

        def handle_endtag(self, tag):
            if tag in ('p', 'div'):
                self.flush_current()
            elif tag in ('ul', 'ol'):
                if self.list_stack:
                    self.list_stack.pop()
                    self.list_counters.pop()
            elif tag == 'li':
                if self.list_stack and self.list_stack[-1] == 'ol':
                    self.list_counters[-1] += 1
                    prefix = '%d. ' % self.list_counters[-1]
                else:
                    prefix = '- '
                self.flush_current(prefix=prefix)
            elif tag in ('strong', 'b', 'em', 'i', 'u', 'span'):
                if self.open_tags:
                    _, close, _ = self.open_tags.pop()
                    if close:
                        self.current.append(close)

        def handle_data(self, data):
            self.current.append(xml_escape(data))

    parser = Converter()
    parser.feed(html or '')
    parser.open_tags = []  # don't auto-reopen on the final flush
    parser.current_max_pt = None
    parser.flush_current()
    return parser.lines

def _reportlab_style_for_line(base_style, max_pt):
    # Returns base_style unchanged if the line has no inline font-size
    # override larger than the base, otherwise a cloned style sized up so
    # the line has enough leading to not collide with what follows.
    if not max_pt or max_pt <= base_style.fontSize:
        return base_style
    from reportlab.lib.styles import ParagraphStyle
    return ParagraphStyle(
        base_style.name + '_big%d' % max_pt, parent=base_style,
        fontSize=max_pt, leading=int(max_pt * 1.25))


def a4_portrait_document(buf):
    """The standard portrait page frame for procurement documents.

    One definition, because there are two builds behind every one of these
    documents: the normal one, and a fallback that runs when decorating the
    canvas with page numbers fails. Both the purchase order and the delivery
    note previously wrote these margins out twice, so a margin changed in the
    primary silently did not reach the fallback — and the fallback only runs
    on an error path nobody exercises, which is where a difference like that
    survives longest.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=15 * mm, bottomMargin=15 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm)
