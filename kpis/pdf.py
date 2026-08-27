"""PDF exports for the GM dashboard's Deadlines tab.

Two of them. `deadline_reliability_pdf` is the management view - every
milestone, every person. `deadline_person_pdf` is one person's own record,
meant to be sent to them, and it carries nobody else's figures.

Layout and branding follow costing.views.costing_pipeline_pdf - landscape A4,
Leap logo left, title block right, grey theme - so the exports look like they
came from the same system.

The figures are shaped in kpis.services; nothing here decides what a number
means, only how it is drawn.
"""

import io
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone

from accounts.permissions import require_capability

# Same greys the pipeline export uses.
HEAD_GREY = '#404040'
ACCENT_GREY = '#6c757d'
ROW_ALT = '#f2f2f2'
GRID_GREY = '#d5d5d5'
MUTED = '#666666'


def _reportlab():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
        KeepTogether,
    )
    return dict(
        colors=colors, A4=A4, landscape=landscape, mm=mm,
        getSampleStyleSheet=getSampleStyleSheet, ParagraphStyle=ParagraphStyle,
        TA_CENTER=TA_CENTER, TA_RIGHT=TA_RIGHT, ImageReader=ImageReader,
        SimpleDocTemplate=SimpleDocTemplate, Table=Table, TableStyle=TableStyle,
        Paragraph=Paragraph, Spacer=Spacer, Image=Image, KeepTogether=KeepTogether,
    )


def _logo_path():
    from django.contrib.staticfiles.finders import find as find_static
    path = find_static('images/leap_logo.jpg')
    if not path:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'static', 'images', 'leap_logo.jpg')
    return path if path and os.path.exists(path) else None


def _styles(rl):
    base = rl['getSampleStyleSheet']()
    P, colors = rl['ParagraphStyle'], rl['colors']
    head_grey = colors.HexColor(HEAD_GREY)
    return {
        'cell': P('cell', parent=base['Normal'], fontSize=7.5, leading=9),
        'head': P('head', parent=base['Normal'], fontSize=7.5, leading=9,
                  textColor=colors.white, alignment=rl['TA_CENTER']),
        'section': P('section', parent=base['Normal'], fontSize=11, leading=13,
                     textColor=head_grey, spaceAfter=1),
        'note': P('note', parent=base['Normal'], fontSize=7, leading=9,
                  textColor=colors.HexColor(MUTED)),
        'body': P('body', parent=base['Normal'], fontSize=8.5, leading=12,
                  textColor=colors.HexColor('#333333')),
        'title': P('t', parent=base['Title'], fontSize=20, textColor=head_grey,
                   alignment=rl['TA_RIGHT'], leading=24),
        'sub': P('sub', parent=base['Normal'], fontSize=9,
                 textColor=colors.HexColor(MUTED), alignment=rl['TA_RIGHT']),
        'tile_label': P('tl', parent=base['Normal'], fontSize=7, leading=9,
                        textColor=colors.HexColor(MUTED)),
        'tile_value': P('tv', parent=base['Normal'], fontSize=16, leading=19,
                        textColor=head_grey),
    }


def _header(rl, st, page_w, title, subtitles):
    """Leap logo left, title and scope right, over a dark rule."""
    Paragraph, Table, TableStyle = rl['Paragraph'], rl['Table'], rl['TableStyle']
    mm = rl['mm']
    block = [Paragraph(title, st['title'])]
    block += [Paragraph(line, st['sub']) for line in subtitles]

    logo_path = _logo_path()
    if logo_path:
        iw, ih = rl['ImageReader'](logo_path).getSize()
        logo_w = 48 * mm
        logo = rl['Image'](logo_path, width=logo_w, height=logo_w * ih / float(iw))
        table = Table([[logo, block]],
                      colWidths=[logo_w + 4 * mm, page_w - logo_w - 4 * mm])
    else:
        table = Table([[block]], colWidths=[page_w])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    rule = Table([['']], colWidths=[page_w], rowHeights=[2],
                 style=TableStyle([('BACKGROUND', (0, 0), (-1, -1),
                                    rl['colors'].HexColor(HEAD_GREY))]))
    return [table, rl['Spacer'](1, 3 * mm), rule, rl['Spacer'](1, 5 * mm)]


def _tile_strip(rl, st, page_w, tiles):
    Paragraph, Table, TableStyle = rl['Paragraph'], rl['Table'], rl['TableStyle']
    grid = [[Paragraph(label.upper(), st['tile_label']),
             Paragraph(value, st['tile_value']),
             Paragraph(sub, st['tile_label'])] for label, value, sub in tiles]
    table = Table([[Table([[c] for c in col]) for col in grid]],
                  colWidths=[page_w / len(tiles)] * len(tiles))
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, rl['colors'].HexColor(GRID_GREY)),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, rl['colors'].HexColor(GRID_GREY)),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return table


def _block(rl, st, page_w, spec, empty_text='Nothing in this period.'):
    """A titled table, kept with its heading so a title is never stranded at
    the foot of a page."""
    Paragraph, Table, TableStyle = rl['Paragraph'], rl['Table'], rl['TableStyle']
    colors = rl['colors']
    columns, rows = spec['columns'], spec['rows']

    grid = [[Paragraph(c, st['head']) for c in columns]]
    for row in rows:
        grid.append([Paragraph(str(c), st['cell']) for c in row])
    if not rows:
        grid.append([Paragraph(empty_text, st['cell'])]
                    + [Paragraph('', st['cell']) for _ in columns[1:]])

    # First column carries names and titles, so give it the slack.
    first = 0.20
    rest = (1 - first) / (len(columns) - 1) if len(columns) > 1 else 0
    widths = [first * page_w] + [rest * page_w] * (len(columns) - 1)

    table = Table(grid, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(HEAD_GREY)),
        ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor(ACCENT_GREY)),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(GRID_GREY)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(ROW_ALT)]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    heading = [Paragraph(spec['title'], st['section'])]
    if spec.get('note'):
        heading.append(Paragraph(spec['note'], st['note']))
    heading.append(rl['Spacer'](1, 2 * rl['mm']))
    return [rl['KeepTogether'](heading + [table]), rl['Spacer'](1, 6 * rl['mm'])]


def _respond(rl, story, filename, doc_title):
    buf = io.BytesIO()
    mm = rl['mm']  # noqa: F841 - used in the margin expressions below
    doc = rl['SimpleDocTemplate'](
        buf, pagesize=rl['landscape'](rl['A4']),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=12 * mm,
        title=doc_title)
    doc.build(story)
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{filename}.pdf"'
    return resp


def _scope_line(period, region):
    from .periods import label_for
    scope = label_for(period)
    if region is not None:
        scope += f' · {region.name}'
    return scope


def _generated_line():
    return 'Generated ' + timezone.localtime(
        timezone.now()).strftime('%d %b %Y at %H:%M')


@login_required
@require_capability('kpis.access')
def deadline_reliability_pdf(request):
    """The Deadlines tab as a PDF, honouring whatever the page was showing.

    Reads the same ?period / ?region / ?ms / ?outcome / ?by the tab does, so
    the export is of what the user is looking at rather than of some default
    that quietly answers a different question.

    Gated exactly like kpi_new(): this renders company-wide milestone and
    per-person performance, so it needs the same two decorators. Without them
    the URL answers to anyone who guesses it.
    """
    try:
        rl = _reportlab()
    except ImportError:
        messages.error(
            request,
            'reportlab is required for PDF export. Install with: pip install reportlab')
        return redirect('kpis:kpi_new')

    from .services import (
        build_deadline_reliability, reliability_drilldown,
        deadline_export_tables, deadline_export_summary,
    )
    from .views import _resolve_period, _resolve_region

    period = _resolve_period(request)
    region, _regions = _resolve_region(request)
    data = build_deadline_reliability(period, region=region)
    drill = reliability_drilldown(
        data, request.GET.get('ms'), request.GET.get('outcome'),
        request.GET.get('by'))

    st = _styles(rl)
    page_w = rl['landscape'](rl['A4'])[0] - 20 * rl['mm']

    story = _header(rl, st, page_w, 'Milestone Reliability',
                    [_scope_line(period, region), _generated_line()])
    story.append(_tile_strip(rl, st, page_w, deadline_export_summary(data)))
    story.append(rl['Spacer'](1, 6 * rl['mm']))
    for spec in deadline_export_tables(data, drill):
        story += _block(rl, st, page_w, spec)

    name = f'Milestone_Reliability_{period}'
    if region is not None:
        name += f'_{region.code}'
    if drill:
        name += f"_{drill['milestone'] or 'all'}_{drill['outcome']}"
    return _respond(rl, story, name, 'Milestone Reliability')


@login_required
@require_capability('kpis.access')
def deadline_person_pdf(request, user_id):
    """One person's own milestone record, for sending to them.

    Deliberately not the management export filtered down: this carries their
    work, their rates and one team-wide percentage for context, and names no
    other individual, so it can be forwarded without leaking colleagues'
    figures.

    It leads with the rule the dates are worked out by and ends with what to
    do about a wrong one, because a report someone is expected to correct has
    to say how.
    """
    try:
        rl = _reportlab()
    except ImportError:
        messages.error(
            request,
            'reportlab is required for PDF export. Install with: pip install reportlab')
        return redirect('kpis:kpi_new')

    from django.http import Http404
    from .services import (
        build_deadline_reliability, deadline_person_report,
        PERSON_REPORT_RULES, PERSON_REPORT_FOOTER,
    )
    from .views import _resolve_period, _resolve_region

    period = _resolve_period(request)
    region, _regions = _resolve_region(request)
    data = build_deadline_reliability(period, region=region)
    report = deadline_person_report(data, user_id)
    if report is None:
        # Nothing to send. A blank report with someone's name at the top reads
        # as an accusation of doing nothing, which is not what it means.
        raise Http404('That person moved no milestones in this period.')

    st = _styles(rl)
    page_w = rl['landscape'](rl['A4'])[0] - 20 * rl['mm']
    Paragraph, Spacer, mm = rl['Paragraph'], rl['Spacer'], rl['mm']

    story = _header(
        rl, st, page_w, report['name'],
        ['Milestone record · ' + _scope_line(period, region), _generated_line()])
    story.append(_tile_strip(rl, st, page_w, report['tiles']))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph('How these dates are worked out', st['section']))
    story.append(Paragraph(
        PERSON_REPORT_RULES.format(
            n=data['buffer_days'], s='' if data['buffer_days'] == 1 else 's'),
        st['body']))
    context = (f"Across the whole team in this period, {report['team_pct']} of "
               f"{report['team_judged']} milestones with a deadline were met.")
    if report['undated']:
        context += (f" {report['undated']} of your milestones had no submission "
                    "date recorded and are listed but not scored.")
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(context, st['body']))
    story.append(Spacer(1, 6 * mm))

    for spec in report['tables']:
        story += _block(rl, st, page_w, spec,
                        empty_text='No milestones in this period.')

    story.append(Paragraph('If something here is wrong', st['section']))
    story.append(Paragraph(PERSON_REPORT_FOOTER, st['body']))

    safe = ''.join(c if c.isalnum() else '_' for c in report['name']).strip('_')
    return _respond(rl, story, f'Milestone_Record_{safe}_{period}',
                    f"Milestone Record - {report['name']}")
