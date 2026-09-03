"""Import the milestone sheets from the projects overview workbook.

Dry-run by default. The plan is printed and nothing is written until --apply
is passed, because the thing worth avoiding is a half-matched import that
creates projects the ERP already has under a different spelling.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from pmo.models import ProjectMilestone
from pmo.workbook_import import plan_workbook
from projects.models import Project


class Command(BaseCommand):
    help = 'Import per-project milestone sheets from the projects overview workbook.'

    def add_arguments(self, parser):
        parser.add_argument('workbook', help='Path to the .xlsx file')
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually write the milestones. Without this, only the plan is printed.')
        parser.add_argument(
            '--replace', action='store_true',
            help='Delete existing milestones on a matched project before importing. '
                 'Without it, a project that already has milestones is skipped.')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is required to read the workbook.')

        workbook = openpyxl.load_workbook(options['workbook'], data_only=True, read_only=True)
        projects = list(Project.objects.all())
        results = plan_workbook(workbook, projects)

        if not results:
            self.stdout.write(self.style.WARNING('No milestone sheets found in that workbook.'))
            return

        matched = [r for r in results if r['project']]
        unmatched = [r for r in results if not r['project']]

        for result in results:
            self._report(result)

        self.stdout.write('')
        self.stdout.write(f'{len(results)} milestone sheets: '
                          f'{len(matched)} matched, {len(unmatched)} unmatched.')

        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                'Dry run — nothing written. Re-run with --apply once the matches look right.'))
            return

        created = skipped = 0
        with transaction.atomic():
            for result in matched:
                project = result['project']
                existing = project.milestones.count()
                if existing and not options['replace']:
                    skipped += 1
                    continue
                if existing:
                    project.milestones.all().delete()
                created += self._write(project, result['activities'])

        self.stdout.write(self.style.SUCCESS(
            f'Wrote {created} milestones. Skipped {skipped} project(s) that already had some '
            f'(use --replace to overwrite).'))

    def _report(self, result):
        if result['project']:
            self.stdout.write(self.style.SUCCESS(
                f"  {result['sheet']}  ->  {result['project'].proposal_reference} "
                f"({len(result['activities'])} activities)"))
        else:
            self.stdout.write(self.style.ERROR(
                f"  {result['sheet']}  ->  UNMATCHED: {result['reason']}"))
        for problem in result['problems']:
            self.stdout.write(self.style.WARNING(f'      weights: {problem}'))

    def _write(self, project, activities):
        """Build the tree from sheet order, not from the typed numbering.

        The typed S.No column is unreliable — the MASCO sheet has two rows both
        labelled 1.1 — so a child belongs to whichever parent it follows.
        """
        count = 0
        parent = None
        parent_order = 0
        child_order = 0
        for entry in activities:
            if entry['level'] == 'parent':
                parent_order += 1
                child_order = 0
                parent = ProjectMilestone.objects.create(
                    project=project, order=parent_order, activity=entry['activity'],
                    weightage=entry['weightage'],
                    completion_date=entry['completion_date'],
                    invoice_prerequisite=entry['invoice_prerequisite'])
            else:
                child_order += 1
                ProjectMilestone.objects.create(
                    project=project, parent=parent, order=child_order,
                    activity=entry['activity'], weightage=entry['weightage'],
                    completed_fraction=entry['completed_fraction'],
                    completion_date=entry['completion_date'],
                    invoice_prerequisite=entry['invoice_prerequisite'])
            count += 1
        return count
