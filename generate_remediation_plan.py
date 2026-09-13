"""Generate the audit remediation plan as a printable PDF.

Produces ``Leap_ERP_Audit_Remediation_Plan.pdf`` in the repo root, in the same
house style as the role-access reference so the two read as a set.

The content mirrors docs/remediation/2026-09-09-architecture-audit-plan.md.
That file stays the source of truth; this renders it for circulation.

Run with:  python generate_remediation_plan.py
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

REPO = Path(__file__).resolve().parent
LOGO = REPO / 'static' / 'images' / 'leap_logo.jpg'
OUTPUT = REPO / 'Leap_ERP_Audit_Remediation_Plan.pdf'

AUDIT_DATE = '8 September 2026'
AUDIT_COMMIT = 'de67daa'

LEAP_RED = colors.HexColor('#C41E3A')
LEAP_DARK = colors.HexColor('#1A1A1A')
LEAP_GREY = colors.HexColor('#6C757D')
HEAD_BG = colors.HexColor('#F1F1F4')
BAND = colors.HexColor('#FAFAFB')
DONE_BG = colors.HexColor('#E8F5EE')
NEXT_BG = colors.HexColor('#FFF8E6')
NOTE_BG = colors.HexColor('#FBEEF0')

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_base = getSampleStyleSheet()
S = {
    'title': ParagraphStyle('title', parent=_base['Title'], fontName='Helvetica-Bold',
                            fontSize=25, leading=29, textColor=LEAP_DARK, alignment=TA_CENTER),
    'subtitle': ParagraphStyle('subtitle', parent=_base['Normal'], fontName='Helvetica',
                               fontSize=12, leading=17, textColor=LEAP_GREY, alignment=TA_CENTER),
    'h1': ParagraphStyle('h1', parent=_base['Heading1'], fontName='Helvetica-Bold',
                         fontSize=15.5, leading=19, textColor=LEAP_RED,
                         spaceBefore=13, spaceAfter=6),
    'h2': ParagraphStyle('h2', parent=_base['Heading2'], fontName='Helvetica-Bold',
                         fontSize=11, leading=14, textColor=LEAP_DARK,
                         spaceBefore=10, spaceAfter=3),
    'body': ParagraphStyle('body', parent=_base['Normal'], fontName='Helvetica',
                           fontSize=9.5, leading=13.5, textColor=LEAP_DARK,
                           alignment=TA_LEFT, spaceAfter=6),
    'note': ParagraphStyle('note', parent=_base['Normal'], fontName='Helvetica-Oblique',
                           fontSize=8.8, leading=12.5, textColor=LEAP_GREY, spaceAfter=6),
    'mono': ParagraphStyle('mono', parent=_base['Normal'], fontName='Courier',
                           fontSize=8, leading=11, textColor=LEAP_DARK, spaceAfter=6),
    'cell': ParagraphStyle('cell', parent=_base['Normal'], fontName='Helvetica',
                           fontSize=8, leading=10.5, textColor=LEAP_DARK),
    'cellb': ParagraphStyle('cellb', parent=_base['Normal'], fontName='Helvetica-Bold',
                            fontSize=8, leading=10.5, textColor=LEAP_DARK),
    'cellhead': ParagraphStyle('cellhead', parent=_base['Normal'], fontName='Helvetica-Bold',
                               fontSize=7.8, leading=9.5, textColor=LEAP_DARK),
}


def P(text, style='body'):
    return Paragraph(text, S[style])


def cell(text, bold=False):
    return Paragraph(text, S['cellb' if bold else 'cell'])


def head_cell(text):
    return Paragraph(text, S['cellhead'])


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 7.5)
    canvas.setFillColor(LEAP_GREY)
    canvas.drawString(MARGIN, 10 * mm, 'Leap Networks ERP — Audit Remediation Plan')
    canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f'Page {canvas.getPageNumber()}')
    canvas.setStrokeColor(colors.HexColor('#E3E3E7'))
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
    canvas.restoreState()


def _cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(LEAP_RED)
    canvas.rect(0, PAGE_H - 8 * mm, PAGE_W, 8 * mm, stroke=0, fill=1)
    canvas.restoreState()


def grid(rows, widths, *, header=True, zebra=True, marks=None):
    style = [
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#DDDDE2')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [('BACKGROUND', (0, 0), (-1, 0), HEAD_BG),
                  ('LINEBELOW', (0, 0), (-1, 0), 0.8, LEAP_GREY)]
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if i % 2 == (0 if header else 1):
                style.append(('BACKGROUND', (0, i), (-1, i), BAND))
    for (r, c0, c1), colour in (marks or {}).items():
        style.append(('BACKGROUND', (c0, r), (c1, r), colour))
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    table.setStyle(TableStyle(style))
    return table


def callout(story, kind, title, paragraphs, bg=NOTE_BG, rule=LEAP_RED):
    inner = [Paragraph(f'<b>{title}</b>', S['cellb'])]
    for text in paragraphs:
        inner.append(Spacer(1, 2 * mm))
        inner.append(Paragraph(text, S['cell']))
    box = Table([[inner]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg),
        ('LINEBEFORE', (0, 0), (0, -1), 2.5, rule),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
    ]))
    story.append(box)
    story.append(Spacer(1, 4 * mm))


# ────────────────────────────── content ──────────────────────────────

VERIFIED = [
    ('F01 media route has no object authorization', 'yes',
     '<font face="Courier">erp_leap/urls.py:51-57</font> — login_required and nothing else'),
    ('F05 destructive actions accept GET', 'yes',
     'All three delete views, zero <font face="Courier">require_POST</font>'),
    ('F07 deploy reloads 311 project records', 'yes, with the audit’s own check',
     '<b>Correction — see below</b>'),
    ('F21 view modules oversized', 'yes',
     '5,231 / 5,075 / 4,573 lines — matches an independent review'),
    ('F22 suite not green', 'yes',
     '13 errors, all <font face="Courier">UNIQUE accounts_role.name</font>'),
]

PHASE0 = [
    ('0.1', 'Make the suite green — F22',
     'get_or_create at three sites in kpis/tests.py. Their setUp errored, so every '
     'assertion in those classes silently never ran.',
     'DONE', 'S'),
    ('0.2', 'Stop the deploy lying — F07',
     'Remove the fixture replay from build.sh and stop swallowing initialisation '
     'failures with || echo.',
     'NEXT', 'S'),
    ('0.3', 'Close the GET deletes — F05',
     'require_POST on the three document delete views; CSRF-protected forms in place '
     'of delete anchors.',
     '', 'S'),
    ('0.4', 'Align the runtime — F08',
     'Dockerfile says Python 3.11, Django needs 3.12. That container build cannot '
     'succeed as declared.',
     '', 'S'),
    ('0.5', 'Turn the audit probes into tests',
     'The ten reproduced probes become tests asserting denial, so every later fix is '
     'demonstrable and stays fixed.',
     '', 'M'),
]

PHASE2 = [
    ('2.1', 'F06', 'Rollback loses files',
     'Capture the old key, delete after commit, outbox for durability', 'M'),
    ('2.2', 'F11', 'Approval replaced by an unapproved M5',
     'POST-only transition, stage gate, approved snapshot', 'M'),
    ('2.3', 'F12', 'Posted totals trusted; partial saves',
     'Validate the whole submission, derive totals server-side, atomic write', 'M'),
    ('2.4', 'F13', 'Quotation review unscoped and repeatable',
     'Shared visibility policy; one locked extracted→converted transition', 'M'),
    ('2.5', 'F14/F15', 'Leave and timesheet races',
     'Lock the parent row before reading state; unique (year, month)', 'M'),
    ('2.6', 'F19', 'Missing rate silently becomes 1:1',
     'Explicit conversion result carrying a missing-rate state', 'S'),
    ('2.7', 'F20', 'Export formula injection',
     'Shared helper forcing untrusted text to string cells', 'S'),
    ('2.8', 'F16', 'Attendance is self-reported',
     'Validate types, reject inactive users, serialise aggregation', 'M'),
]

PHASE3 = [
    ('3.1', 'F17', 'Email on disposable daemon threads',
     'Transactional outbox, bounded worker, send after commit', 'M'),
    ('3.2', 'F18', 'Slow integrations occupy both workers',
     'Durable jobs with deadlines for AI extraction and document generation', 'L'),
    ('3.3', 'F21', 'Domain rules inside view modules',
     'Per-domain policies, selectors, services, renderers; import-boundary tests', 'L'),
    ('3.4', 'F23', 'Production config fallbacks',
     'Fail fast on missing DATABASE_URL and storage config', 'S'),
]

IN_FLIGHT = [
    ('#220', 'Shared front-end CSRF/fetch helper', 'Merge — no production behaviour change'),
    ('#221', 'PO pagination characterization tests', 'Merge — tests only'),
    ('#222', 'PO PDF extracted from views; duplication and geometry fixes',
     'Merge when ready to see it — shifts the supplier PDF ~2 mm'),
    ('#212', 'PMO department (milestones, delivery board, importer)',
     '<b>Hold</b> — new module with new views and file handling while F01 is open'),
]


def cover(story):
    if LOGO.exists():
        story.append(Spacer(1, 20 * mm))
        img = Image(str(LOGO), width=52 * mm, height=52 * mm * 0.42)
        img.hAlign = 'CENTER'
        story.append(img)
    story.append(Spacer(1, 15 * mm))
    story.append(P('Architecture Audit — Remediation Plan', 'title'))
    story.append(Spacer(1, 5 * mm))
    story.append(P('Twenty-three findings, sequenced into work that can be<br/>'
                   'started, verified and demonstrated one at a time', 'subtitle'))
    story.append(Spacer(1, 22 * mm))
    story.append(grid([
        [cell('System', True), cell('Leap Networks ERP')],
        [cell('Audit', True), cell(f'External engineering audit, {AUDIT_DATE}')],
        [cell('Reviewed', True), cell(f'Branch <font face="Courier">main</font> at '
                                      f'<font face="Courier">{AUDIT_COMMIT}</font>')],
        [cell('Findings', True), cell('23 · 10 rated P1 · 10 reproduced by probes')],
        [cell('Plan issued', True), cell(date.today().strftime('%d %B %Y'))],
        [cell('Source of truth', True),
         cell('<font face="Courier">docs/remediation/'
              '2026-09-09-architecture-audit-plan.md</font>')],
    ], [40 * mm, CONTENT_W - 40 * mm], header=False, zebra=False))
    story.append(Spacer(1, 9 * mm))
    story.append(P('Everything in this plan is still fixable. Nothing here requires a '
                   'rewrite — the audit is explicit that a microservices migration is '
                   'not justified, and that the modular monolith should be kept.', 'note'))
    story.append(NextPageTemplate('body'))
    story.append(PageBreak())


def section_verified(story):
    story.append(P('1. What was verified before planning', 'h1'))
    story.append(P('The audit was not taken on trust. The highest-stakes findings were '
                   'reproduced locally first, because the sequencing depends on which are '
                   'actually live rather than theoretically possible.'))
    rows = [[head_cell('Finding'), head_cell('Verified'), head_cell('Result')]]
    for what, verified, result in VERIFIED:
        rows.append([cell(what), cell(verified), cell(result)])
    story.append(grid(rows, [64 * mm, 32 * mm, CONTENT_W - 96 * mm]))
    story.append(Spacer(1, 5 * mm))

    callout(story, 'correction', 'F07 needed correcting — better and worse than reported', [
        'The audit states a redeploy “can revert edited project fields”. Running its own '
        'acceptance check on a disposable database shows the fixture <b>fails to load at '
        'all</b>: it references a <font face="Courier">Project.epc</font> field that no '
        'longer exists. <font face="Courier">build.sh:72</font> ends in '
        '<font face="Courier">|| echo</font>, so the failure is swallowed and the deploy '
        'reports an initialisation that never happened.',
        '<b>Production data is not being reverted today.</b> The risk is latent, not '
        'active — which lowers F07 from “actively destroying data” to “loaded gun”.',
        '<b>But it is a loaded gun.</b> All 325 fixture objects carry explicit primary '
        'keys. Anyone who regenerates or repairs that fixture switches on silent '
        'overwriting of 311 projects on the very next deploy.',
    ])

    callout(story, 'ownership', 'F22’s thirteen errors originated here', [
        'Migration <font face="Courier">accounts/0032_create_original_roles</font> (#206, '
        '3 September) seeds the admin, manager and sales_rep roles. Three KPI test classes '
        'then called <font face="Courier">Role.objects.create(name=Role.SALES_REP)</font> '
        'and hit the unique constraint. Their setUp errored, so every assertion in those '
        'classes silently never executed.',
        'The audit found the symptom; the cause was introduced in this project. It is the '
        'first task in the plan, and it is now done.',
    ], bg=colors.HexColor('#FFF8E6'), rule=colors.HexColor('#B26B00'))


def section_sequencing(story):
    story.append(P('2. Sequencing', 'h1'))
    story.append(P('The audit’s phase order — access and data, then write consistency, '
                   'then structure — is sound and is followed. Three refinements:'))
    rows = [
        [head_cell('Refinement'), head_cell('Why')],
        [cell('<b>The suite is repaired first,<br/>not alongside</b>'),
         cell('Every fix lands blind while thirteen assertions silently do not run, and '
              'there is no CI. It is also the cheapest item in the plan.')],
        [cell('<b>Phase 1 is one piece of<br/>work, not five</b>'),
         cell('F01, F02, F03, F09 and F10 share a single root cause: there is no shared '
              'object-access policy, so each entry point invents its own. Fixing them '
              'individually reproduces the pattern that created them.')],
        [cell('<b>Structural work is<br/>deferred</b>'),
         cell('Work already in flight (#220, #221, #222) is F21, which is phase 3. It was '
              'the right answer to a different question, and the audit reorders it.')],
    ]
    story.append(grid(rows, [48 * mm, CONTENT_W - 48 * mm]))


def section_phase0(story):
    story.append(PageBreak())
    story.append(P('3. Phase 0 — restore the safety net, disarm what is latent', 'h1'))
    story.append(P('Small, low-risk, high-value. Nothing in this phase changes business '
                   'behaviour.'))
    rows = [[head_cell('#'), head_cell('Task'), head_cell('What'),
             head_cell('State'), head_cell('Size')]]
    marks = {}
    for i, (num, task, what, state, size) in enumerate(PHASE0, start=1):
        rows.append([cell(num, True), cell(task), cell(what), cell(f'<b>{state}</b>'), cell(size)])
        if state == 'DONE':
            marks[(i, 0, 4)] = DONE_BG
        elif state == 'NEXT':
            marks[(i, 0, 4)] = NEXT_BG
    story.append(grid(rows, [10 * mm, 44 * mm, CONTENT_W - 90 * mm, 20 * mm, 16 * mm],
                      marks=marks))
    story.append(Spacer(1, 4 * mm))
    callout(story, 'done', '0.1 is complete and its acceptance check has been run', [
        'Full suite from an empty migrated database: <font face="Courier">Ran 2503 tests '
        '… OK (skipped=1)</font> — zero errors, zero failures. The same 2,503 tests the '
        'audit counted, with its thirteen errors gone.',
        'The single skipped test is <font face="Courier">hr/tests.py:2960</font>, a '
        'concurrency test guarded by <font face="Courier">skipUnlessDBFeature'
        '(\'has_select_for_update\')</font>. SQLite has no row-level locking, so it runs '
        'only against PostgreSQL. Worth noting: <b>F14 and F15 are exactly this class of '
        'finding</b> and are unprovable locally for the same reason — which is the '
        'argument for a PostgreSQL job in 0.5.',
    ], bg=DONE_BG, rule=colors.HexColor('#1B6B45'))


def section_phase1(story):
    story.append(P('4. Phase 1 — access', 'h1'))
    story.append(P('<b>1.1 — a shared object-access policy (F01, F02, F03, F09, F10).</b> '
                   'One policy module plus per-domain scoped selectors, with every read, '
                   'mutation and export resolved through them.'))
    rows = [
        [head_cell('Finding'), head_cell('What changes')],
        [cell('<b>F01</b> media route'),
         cell('Private downloads routed through the owning model; R2 URLs signed only '
              'after authorization; public assets separated')],
        [cell('<b>F02</b> proposal export'),
         cell('Exports resolved through visible_proposals(user); detail and every export '
              'format must give the same authorization answer')],
        [cell('<b>F03</b> finance detail'),
         cell('project_schedule, project_cash_outflow and approve_margin scoped to the '
              'user’s region')],
        [cell('<b>F09</b> child routes'),
         cell('Module capability and object scope as separate mandatory checks on every '
              'child route, not only the list view')],
        [cell('<b>F10</b> KPI activity'),
         cell('kpis.activity enforced on every activity entry path, with an authorized '
              'user-ID scope passed into the builder')],
    ]
    story.append(grid(rows, [34 * mm, CONTENT_W - 34 * mm]))
    story.append(Spacer(1, 3 * mm))
    story.append(P('<b>Size: L.</b> The largest single item in the plan, and the one that '
                   'most reduces risk. <b>1.2 — sanitise rich text (F04)</b> is separate '
                   'work: a server-side allowlist on every write path, plus cleaning '
                   'existing content. Size M.', 'note'))


def section_phases23(story):
    story.append(PageBreak())
    story.append(P('5. Phase 2 — integrity of writes', 'h1'))
    rows = [[head_cell('#'), head_cell('Finding'), head_cell('Problem'),
             head_cell('Work'), head_cell('Size')]]
    for num, finding, problem, work, size in PHASE2:
        rows.append([cell(num, True), cell(f'<b>{finding}</b>'), cell(problem),
                     cell(work), cell(size)])
    story.append(grid(rows, [11 * mm, 18 * mm, 50 * mm, CONTENT_W - 95 * mm, 16 * mm]))

    story.append(P('6. Phase 3 — isolation and structure', 'h1'))
    rows = [[head_cell('#'), head_cell('Finding'), head_cell('Problem'),
             head_cell('Work'), head_cell('Size')]]
    for num, finding, problem, work, size in PHASE3:
        rows.append([cell(num, True), cell(f'<b>{finding}</b>'), cell(problem),
                     cell(work), cell(size)])
    story.append(grid(rows, [11 * mm, 18 * mm, 50 * mm, CONTENT_W - 95 * mm, 16 * mm]))


def section_inflight(story):
    story.append(P('7. Work already in flight', 'h1'))
    story.append(P('Four pull requests are open and unmerged. All predate the audit.'))
    rows = [[head_cell('PR'), head_cell('What'), head_cell('Recommendation')]]
    for pr, what, rec in IN_FLIGHT:
        rows.append([cell(f'<b>{pr}</b>'), cell(what), cell(rec)])
    story.append(grid(rows, [16 * mm, 68 * mm, CONTENT_W - 84 * mm]))
    story.append(Spacer(1, 2 * mm))
    story.append(P('#222 is stacked on #221 and needs retargeting to '
                   '<font face="Courier">dev</font> once #221 merges.', 'note'))


def section_judgement(story):
    story.append(P('8. How progress is judged', 'h1'))
    story.append(P('Not by findings closed. By these, in order:'))
    rows = [
        [head_cell('#'), head_cell('Condition')],
        [cell('1', True), cell('The suite runs green from an empty database, in CI, on every push')],
        [cell('2', True), cell('Every audit probe has become a test that expects denial')],
        [cell('3', True), cell('A redeploy provably preserves edited records')],
        [cell('4', True), cell('One policy decides object access, and a test enumerates module URLs against it')],
        [cell('5', True), cell('No financial approval can change without an explicit, recorded transition')],
    ]
    story.append(grid(rows, [10 * mm, CONTENT_W - 10 * mm]))
    story.append(Spacer(1, 4 * mm))
    callout(story, 'rule', 'The standing rule for this plan', [
        'A finding marked fixed without its acceptance check run is not fixed.',
    ])


def main():
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title='Leap ERP — Audit Remediation Plan', author='Leap Networks')
    frame = Frame(MARGIN, 18 * mm, CONTENT_W, PAGE_H - 36 * mm, id='body')
    doc.addPageTemplates([
        PageTemplate(id='cover', frames=[frame], onPage=_cover),
        PageTemplate(id='body', frames=[frame], onPage=_footer),
    ])

    story = []
    cover(story)
    section_verified(story)
    section_sequencing(story)
    section_phase0(story)
    section_phase1(story)
    section_phases23(story)
    section_inflight(story)
    section_judgement(story)
    doc.build(story)
    print(f'Wrote {OUTPUT}')


if __name__ == '__main__':
    main()
