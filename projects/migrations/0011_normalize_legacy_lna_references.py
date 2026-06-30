from django.db import migrations


def normalize(apps, schema_editor):
    """Convert legacy LNA references (LNA-2289, LNA02158-R03, …) into the
    name-bearing 'LNA <number> - <name> (R03)' format so renaming a project
    reflects in its reference. Refs with no parseable LNA number are left as-is.
    """
    from projects.models import parse_lna_reference, build_lna_reference
    Project = apps.get_model('projects', 'Project')
    Region = apps.get_model('projects', 'Region')

    lna = Region.objects.filter(code='LNA').first()
    if not lna:
        return

    # Track refs in use so we never violate the unique constraint — if a target
    # already exists (very unlikely), the project is left as-is rather than
    # erroring the deploy. Nothing is ever deleted.
    taken = set(Project.objects.exclude(region=lna)
                .values_list('proposal_reference', flat=True))

    for project in Project.objects.filter(region=lna):
        ref = project.proposal_reference or ''
        parsed = parse_lna_reference(ref)
        if not parsed:
            taken.add(ref)
            continue  # unparseable (e.g. demo refs) — leave untouched
        number, revision = parsed
        new_ref = build_lna_reference(number, project.project_name, revision)
        if new_ref == ref or new_ref in taken:
            # No change, or would collide with an existing ref — leave untouched.
            taken.add(ref)
            continue
        project.proposal_reference = new_ref
        project.save(update_fields=['proposal_reference'])
        taken.add(new_ref)


def noop(apps, schema_editor):
    # Non-reversible: the original legacy formats aren't recoverable.
    pass


class Migration(migrations.Migration):
    dependencies = [('projects', '0010_alter_document_document_type')]
    operations = [migrations.RunPython(normalize, noop)]
