import json
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations

SEED_DIR = os.path.join(os.path.dirname(__file__),'seed_data')
SEED_JSON= os.path.join(SEED_DIR, 'telecom_batch2.json')
SEED_IMAGES_DIR = os.path.join(SEED_DIR, 'dept_template_images')


def _seed_images():
    if not os.path.isdir(SEED_IMAGES_DIR):
        return
    for dept in os.listdir(SEED_IMAGES_DIR):
        dept_dir = os.path.join(SEED_IMAGES_DIR, dept)
        if not os.path.isdir(dept_dir):
            continue
        for filename in os.listdir(dept_dir):
            key= f'proposal_templates/{dept}/{filename}'
            try:
                if default_storage.exists(key):
                    continue
                with open(os.path.join(dept_dir, filename), 'rb') as f:
                    default_storage.save(key, ContentFile(f.read()))
            except Exception as exc:
                print(f'  [0013_seed_new_telecom_headings] WARNING: '
                      f'failed to upload {key}: {exc}')

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
        existing = SectionHeading.objects.filter(name=heading_name).first()
        if existing is not None and not existing.dept_templates.exists():
            heading_name = f"{row['heading']} (Telecom)"

        heading, _ = SectionHeading.objects.get_or_create(
            name=heading_name, defaults={'order': row['heading_order']})
        SectionHeadingTemplate.objects.get_or_create(
            heading=heading, department=row['department'],
            defaults={'content': row['content']})

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('proposals', '0012_alter_sectionheadingtemplate_department')]
    operations = [migrations.RunPython(seed, noop)]
                
                
                      