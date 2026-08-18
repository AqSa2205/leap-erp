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
            if default_storage.exists(key):
                continue
            with open(os.path.join(dept_dir, filename), 'rb') as f:
                default_storage.save(key, ContentFile(f.read()))


def seed(apps, schema_editor):
    SectionHeading = apps.get_model('proposals', 'SectionHeading')
    SectionHeadingTemplate = apps.get_model('proposals', 'SectionHeadingTemplate')

    if not os.path.exists(SEED_JSON):
        return
    with open(SEED_JSON, encoding='utf-8') as f:
        rows = json.load(f)

    _seed_images()

    for row in rows:
        heading, _ = SectionHeading.objects.get_or_create(
            name=row['heading'], defaults={'order': row['heading_order']})
        SectionHeadingTemplate.objects.get_or_create(
            heading=heading, department=row['department'],
            defaults={'content': row['content']})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('proposals', '0010_sectionheadingtemplate')]
    operations = [migrations.RunPython(seed, noop)]
