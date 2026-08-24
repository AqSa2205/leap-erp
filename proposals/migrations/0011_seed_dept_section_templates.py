import json
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations

SEED_DIR = os.path.join(os.path.dirname(__file__), 'seed_data')
SEED_JSON = os.path.join(SEED_DIR, 'dept_section_templates.json')
SEED_IMAGES_DIR = os.path.join(SEED_DIR, 'dept_template_images')


def _seed_images():
    """Copy the bundled AI/Telecom/Procurement template images into whatever
    storage backend is active (local disk in dev, Cloudflare R2 in
    production — default_storage abstracts over both, same as the rest of
    the app's image handling). Skips any file already present, so this is
    safe to run against an environment that already has some/all of these."""
    if not os.path.isdir(SEED_IMAGES_DIR):
        return
    for dept in os.listdir(SEED_IMAGES_DIR):
        dept_dir = os.path.join(SEED_IMAGES_DIR, dept)
        if not os.path.isdir(dept_dir):
            continue
        for filename in os.listdir(dept_dir):
            key = f'proposal_templates/{dept}/{filename}'
            try:
                if default_storage.exists(key):
                    continue
                with open(os.path.join(dept_dir, filename), 'rb') as f:
                    default_storage.save(key, ContentFile(f.read()))
            except Exception as exc:
                # A transient storage/network blip on ONE image must not
                # fail the whole migration — and therefore the deploy. The
                # text content this migration seeds is far more important
                # than any single image, and a missing image degrades
                # gracefully at export time (docx_export._read_image_bytes)
                # rather than corrupting the document.
                print(f'  [0011_seed_dept_section_templates] WARNING: '
                      f'failed to upload {key}: {exc}')


# Human label for a disambiguated heading name (see seed() below) — kept
# local to this migration rather than imported from the live model, since
# migrations must stay self-contained and safe to run against any past or
# future version of the app's code.
DEPT_LABELS = {'ai': 'AI', 'telecom': 'Telecom', 'procurement': 'Security'}


def seed(apps, schema_editor):
    SectionHeading = apps.get_model('proposals', 'SectionHeading')
    SectionHeadingTemplate = apps.get_model('proposals', 'SectionHeadingTemplate')

    if not os.path.exists(SEED_JSON):
        return
    with open(SEED_JSON, encoding='utf-8') as f:
        rows = json.load(f)

    _seed_images()

    for row in rows:
        heading_name = row['heading']

        # SectionHeading.name is unique, and get_or_create() matches by name
        # alone — so if an environment already has a heading with this exact
        # name, we need to know whether it's "ours" (already carries at
        # least one department template — meaning this same seed data, or
        # an earlier row in this run for a different department, already
        # claimed it) or a genuinely independent heading (no department
        # template at all — e.g. a real, already-in-use generic heading
        # someone added by hand before this feature existed). Blindly
        # reusing the latter would silently weld this department's content
        # onto it and make it vanish from the plain generic list for
        # everyone. Checking "has any dept template" rather than a
        # this-run-only set keeps this correct AND idempotent: re-running
        # seed() finds the heading this migration already created (it has
        # our template on it) and reuses it rather than re-disambiguating.
        existing = SectionHeading.objects.filter(name=heading_name).first()
        if existing is not None and not existing.dept_templates.exists():
            dept_label = DEPT_LABELS.get(row['department'], row['department'])
            heading_name = f"{row['heading']} ({dept_label})"

        heading, _ = SectionHeading.objects.get_or_create(
            name=heading_name, defaults={'order': row['heading_order']})
        SectionHeadingTemplate.objects.get_or_create(
            heading=heading, department=row['department'],
            defaults={'content': row['content']})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('proposals', '0010_sectionheadingtemplate')]
    operations = [migrations.RunPython(seed, noop)]
