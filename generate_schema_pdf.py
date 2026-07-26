import ast
import os
import re
from collections import defaultdict

ROOT = os.path.dirname(__file__)
APPS_DIR = ROOT  # project root contains Django apps
OUT_MD = os.path.join(ROOT, 'schema_summary.md')
OUT_PDF = os.path.join(ROOT, 'schema_summary.pdf')

MODEL_BASE = ('models.Model',)

FIELD_CALL_RE = re.compile(r"models\.([A-Za-z_][A-Za-z0-9_]*)")


def parse_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except Exception:
        return []
    models = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            # Check bases for models.Model
            is_model = False
            for base in node.bases:
                if isinstance(base, ast.Attribute):
                    if getattr(base.value, 'id', None) == 'models' and getattr(base, 'attr', '') == 'Model':
                        is_model = True
                elif isinstance(base, ast.Name):
                    # could be imported alias; accept names ending with 'Model'
                    if base.id.endswith('Model'):
                        is_model = True
            if not is_model:
                continue
            class_name = node.name
            doc = ast.get_docstring(node) or ''
            fields = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    if len(item.targets) != 1:
                        continue
                    target = item.targets[0]
                    if not isinstance(target, ast.Name):
                        continue
                    field_name = target.id
                    value = item.value
                    if isinstance(value, ast.Call):
                        # Try to detect if it's a models.Field call
                        func = value.func
                        field_type = None
                        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'models':
                            field_type = func.attr
                        elif isinstance(func, ast.Name):
                            field_type = func.id
                        # gather keywords
                        kwargs = {}
                        for kw in value.keywords:
                            try:
                                if isinstance(kw.value, ast.Constant):
                                    kwargs[kw.arg] = kw.value.value
                                elif isinstance(kw.value, ast.Str):
                                    kwargs[kw.arg] = kw.value.s
                                elif isinstance(kw.value, ast.NameConstant):
                                    kwargs[kw.arg] = kw.value.value
                                else:
                                    # fallback to source substring
                                    kwargs[kw.arg] = ast.unparse(kw.value) if hasattr(ast, 'unparse') else '<expr>'
                            except Exception:
                                kwargs[kw.arg] = '<expr>'
                        # positional args (e.g., ForeignKey target)
                        args = []
                        for a in value.args:
                            try:
                                if isinstance(a, ast.Constant):
                                    args.append(a.value)
                                elif isinstance(a, ast.Str):
                                    args.append(a.s)
                                elif isinstance(a, ast.Name):
                                    args.append(a.id)
                                else:
                                    args.append(ast.unparse(a) if hasattr(ast, 'unparse') else '<expr>')
                            except Exception:
                                args.append('<expr>')
                        fields.append((field_name, field_type, args, kwargs))
            models.append((class_name, doc, fields, path))
    return models


def walk_and_parse(root):
    all_models = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip virtualenv, migrations, static, templates, .git
        skip = ('venv', '.venv', 'static', 'staticfiles', 'templates', '.git', '__pycache__')
        if any(part in skip for part in dirpath.split(os.sep)):
            continue
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            if fn == 'manage.py':
                continue
            path = os.path.join(dirpath, fn)
            try:
                models = parse_file(path)
                if models:
                    all_models.extend(models)
            except Exception:
                pass
    return all_models


def format_markdown(models):
    out = []
    out.append('# Project Schema Summary\n')
    out.append('Automatically generated schema overview for Django models.\n')
    grouped = defaultdict(list)
    for cls, doc, fields, path in models:
        app = os.path.relpath(path, ROOT).split(os.sep)[0]
        grouped[app].append((cls, doc, fields, path))
    for app in sorted(grouped.keys()):
        out.append(f'## App: {app}\n')
        for cls, doc, fields, path in sorted(grouped[app], key=lambda x:x[0]):
            out.append(f'### Model: {cls}  \n')
            out.append(f'*Defined in:* {path}  \n')
            if doc:
                out.append(f'*Description:* {doc}  \n')
            if not fields:
                out.append('_No declared fields found._\n')
                continue
            out.append('| Field | Type | Args | Kwargs |\n')
            out.append('|---|---|---|---|\n')
            for name, ftype, args, kwargs in fields:
                arg_str = ', '.join(map(str, args)) if args else ''
                kw_str = ', '.join(f'{k}={v}' for k, v in kwargs.items()) if kwargs else ''
                out.append(f'| {name} | {ftype or "?"} | {arg_str} | {kw_str} |\n')
            out.append('\n')
    return '\n'.join(out)


def write_md(md_text, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md_text)


def md_to_pdf(md_path, pdf_path):
    # Minimal conversion: render markdown as plain paragraphs in PDF
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
    except Exception as e:
        raise
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
            elif line.startswith('|'):
                # Render table rows as monospaced paragraph
                story.append(Paragraph(line.replace('|', '&#124;'), normal))
            elif line.strip() == '':
                story.append(Spacer(1, 4))
            else:
                story.append(Paragraph(line, normal))
    doc.build(story)


if __name__ == '__main__':
    print('Scanning project for Django models...')
    models = walk_and_parse(APPS_DIR)
    md = format_markdown(models)
    write_md(md, OUT_MD)
    print('Wrote', OUT_MD)
    try:
        md_to_pdf(OUT_MD, OUT_PDF)
        print('Wrote', OUT_PDF)
    except Exception as e:
        print('PDF conversion failed:', e)
        print('You can convert', OUT_MD, 'to PDF with pandoc or a markdown viewer.')
