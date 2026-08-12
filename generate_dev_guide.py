import os
import re
from collections import defaultdict

ROOT = os.path.dirname(__file__)
OUT_MD = os.path.join(ROOT, 'developer_guide.md')
OUT_PDF = os.path.join(ROOT, 'developer_guide.pdf')

APPS = [
    name for name in os.listdir(ROOT)
    if os.path.isdir(os.path.join(ROOT, name)) and not name.startswith('.') and name not in ('venv', '.venv', 'static', 'staticfiles', '__pycache__')
]

KEY_FILES = ['models.py', 'views.py', 'urls.py', 'forms.py', 'admin.py', 'signals.py', 'tasks.py', 'tests.py']

FK_RE = re.compile(r"ForeignKey\(\s*['\"]?([A-Za-z0-9_.]+)['\"]?")
M2M_RE = re.compile(r"ManyToManyField\(\s*['\"]?([A-Za-z0-9_.]+)['\"]?")
O2O_RE = re.compile(r"OneToOneField\(\s*['\"]?([A-Za-z0-9_.]+)['\"]?")


def read_head(path, n=30):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception:
        return ''
    return ''.join(lines[:n])


def extract_docstring_and_summary(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            src = f.read()
    except Exception:
        return '', ''
    # module docstring
    doc = ''
    m = re.match(r"\s*([rR]?[\"\']{3})(.*?)(\1)", src, re.S)
    if m:
        doc = m.group(2).strip()
    summary = '\n'.join(src.splitlines()[:20])
    return doc, summary


def scan_app(app):
    app_path = os.path.join(ROOT, app)
    files = [f for f in os.listdir(app_path) if os.path.isfile(os.path.join(app_path,f))]
    data = {'app': app, 'path': app_path, 'files': files, 'docs': {}, 'relationships': []}
    for k in KEY_FILES:
        p = os.path.join(app_path, k)
        if os.path.exists(p):
            doc, head = extract_docstring_and_summary(p)
            data['docs'][k] = {'doc': doc, 'head': head}
            # scan for FKs
            fk_matches = FK_RE.findall(head)
            m2m_matches = M2M_RE.findall(head)
            o2o_matches = O2O_RE.findall(head)
            for t in fk_matches:
                data['relationships'].append(('FK', t))
            for t in m2m_matches:
                data['relationships'].append(('M2M', t))
            for t in o2o_matches:
                data['relationships'].append(('O2O', t))
    # templates existence
    templates_dir = os.path.join(app_path, 'templates')
    if os.path.exists(templates_dir):
        data['templates'] = [os.path.join(dp, f) for dp, dn, filenames in os.walk(templates_dir) for f in filenames]
    else:
        data['templates'] = []
    # static
    static_dir = os.path.join(app_path, 'static')
    data['static'] = os.path.exists(static_dir)
    return data


def build_markdown(app_scans):
    out = []
    out.append('# Developer Guide — Project Overview')
    out.append('This guide explains the purpose of each app folder, main files to inspect for bugs, how apps connect, and quick pointers like "if you want to fix a bug in HR go here".')
    out.append('\n')

    # App summaries
    for s in sorted(app_scans, key=lambda x: x['app']):
        out.append(f"## App: {s['app']}")
        out.append(f"Path: {s['path']}")
        # short description
        desc = s['docs'].get('models.py', {}).get('doc') or s['docs'].get('views.py', {}).get('doc') or ''
        if desc:
            out.append(f"**Primary purpose (from docstring):** {desc}")
        else:
            out.append('**Primary purpose:** (No module docstring found; inferred from filenames below)')
        out.append('\n')
        out.append('Files:')
        for f in sorted(s['files']):
            out.append(f'- {f}')
        out.append('\n')
        out.append('Key places to look when troubleshooting:')
        out.append('- models.py — database schema and model methods (data validation/business rules tied to persistence)')
        out.append('- views.py — request handling, business workflows, API endpoints and templates rendered')
        out.append('- forms.py — input validation and form-level logic')
        out.append('- urls.py — routing (which endpoint maps to which view)')
        out.append('- admin.py — admin UI customisations (if the bug appears in Django admin)')
        if s['templates']:
            out.append('- templates/ — HTML templates for UI (check for presentation/JS bugs)')
        if s['static']:
            out.append('- static/ — static assets (JS/CSS)')
        out.append('\n')
        # show short heads
        for k in KEY_FILES:
            if k in s['docs']:
                head = s['docs'][k]['head']
                out.append(f'### {k} (first lines)')
                out.append('```')
                out.append(head)
                out.append('```')
        # relationships
        if s['relationships']:
            out.append('Relationships referenced (from scanned first lines):')
            for rel, target in s['relationships']:
                out.append(f'- {rel} -> {target}')
        out.append('\n')
        # quick action guide
        out.append('Quick troubleshooting hints:')
        out.append(f'- If a DB/schema bug: inspect {s["path"]}\\models.py and migrations/ if present.')
        out.append(f'- If an endpoint or view logic bug: inspect {s["path"]}\\views.py and {s["path"]}\\urls.py')
        out.append(f'- If a form validation or input issue: inspect {s["path"]}\\forms.py')
        out.append(f'- If an admin-only issue: inspect {s["path"]}\\admin.py')
        out.append('\n---\n')

    # Cross-app relationships summary (aggregate from relationships)
    out.append('# Cross-app Relationships (inferred)')
    rel_map = defaultdict(set)
    for s in app_scans:
        for kind, target in s['relationships']:
            rel_map[s['app']].add((kind, target))
    for app, rels in rel_map.items():
        out.append(f'## {app}')
        for kind, target in sorted(rels):
            out.append(f'- {kind} -> {target}')
    out.append('\n')

    # High-level architecture — manual section with common sense mapping
    out.append('# High-level Architecture and How Apps Integrate')
    out.append('The Projects app is the central model: projects.Project is referenced widely (procurement, finance, delivery, inventory, proposals).')
    out.append('Procurement manages PurchaseOrder and PurchaseOrderItem; these reference projects and feed into delivery notes, inventory reports, and finance cash outflow rows.')
    out.append('Finance contains ProjectFinance, PaymentMilestone and CashOutflowRow — it depends on costing and projects. Costing produces CostingSheet / line items used by finance and procurement.')
    out.append('Contacts and Reports provide CRM and sales call data used to seed Projects and Proposals. Accounts stores User and Role definitions and permission logic used across the app.')
    out.append('\n')
    out.append('For a bug in HR:')
    out.append('- Start at hr/views.py for request-handling and API endpoints.')
    out.append('- Check hr/forms.py for validation rules that may reject input.')
    out.append('- Check hr/models.py for schema issues and business logic (signals, manager methods).')
    out.append('- Check templates/hr/ for UI problems and static/js for client-side bugs.')
    out.append('For database schema mismatches: examine migrations in each app (migrations/ folder) and db.sqlite3 or your configured DB. Run python manage.py makemigrations && migrate in a dev environment.')
    out.append('\n')
    out.append('# Recommended next actions')
    out.append('- I can expand each app section with deeper file-by-file explanations, extract model fields and relationships, and produce an ER diagram.')
    out.append('- Or generate this guide as a PDF now. (Will produce developer_guide.pdf)')

    return '\n'.join(out)


def md_to_pdf(md_path, pdf_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    styles = getSampleStyleSheet()
    normal = styles['Normal']
    h1 = ParagraphStyle('Heading1', parent=styles['Heading1'], fontSize=18, spaceAfter=8)
    h2 = ParagraphStyle('Heading2', parent=styles['Heading2'], fontSize=14, spaceAfter=6)
    h3 = ParagraphStyle('Heading3', parent=styles['Heading3'], fontSize=12, spaceAfter=4)
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            rightMargin=15*mm, leftMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    story = []
    with open(md_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('# '):
                story.append(Paragraph(line[2:].strip(), h1))
            elif line.startswith('## '):
                story.append(Paragraph(line[3:].strip(), h2))
            elif line.startswith('### '):
                story.append(Paragraph(line[4:].strip(), h3))
            elif line.startswith('```'):
                # skip code fences markers
                continue
            elif line.startswith('- '):
                story.append(Paragraph(line, normal))
            elif line.strip() == '':
                story.append(Spacer(1, 4))
            else:
                story.append(Paragraph(line, normal))
    doc.build(story)


if __name__ == '__main__':
    scans = []
    for app in APPS:
        # skip some non-app dirs
        if app in ('media', 'templates', 'static', 'staticfiles'):
            continue
        scans.append(scan_app(app))
    md = build_markdown(scans)
    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write(md)
    print('Wrote', OUT_MD)
    try:
        md_to_pdf(OUT_MD, OUT_PDF)
        print('Wrote', OUT_PDF)
    except Exception as e:
        print('PDF conversion failed:', e)
