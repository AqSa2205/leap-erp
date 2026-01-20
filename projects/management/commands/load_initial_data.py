from django.core.management.base import BaseCommand
from accounts.models import Role
from projects.models import Region, ProjectStatus


class Command(BaseCommand):
    help = 'Load initial data for regions, statuses, and roles'

    def handle(self, *args, **options):
        self.stdout.write('Loading initial data...')

        # Create Roles
        roles_data = [
            {'name': 'admin', 'description': 'Full system access'},
            {'name': 'manager', 'description': 'Regional management access'},
            {'name': 'sales_rep', 'description': 'Sales representative access'},
        ]
        for role_data in roles_data:
            role, created = Role.objects.get_or_create(
                name=role_data['name'],
                defaults={'description': role_data['description']}
            )
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  Role: {role.name} - {status}')

        # Create Regions
        regions_data = [
            {'name': 'United Kingdom', 'code': 'UK', 'currency': 'GBP'},
            {'name': 'Leap Networks Arabia', 'code': 'LNA', 'currency': 'SAR'},
            {'name': 'Pacific Asia', 'code': 'PA', 'currency': 'USD'},
            {'name': 'Global', 'code': 'GLB', 'currency': 'USD'},
        ]
        for region_data in regions_data:
            region, created = Region.objects.get_or_create(
                code=region_data['code'],
                defaults={
                    'name': region_data['name'],
                    'currency': region_data['currency']
                }
            )
            status = 'Created' if created else 'Already exists'
            self.stdout.write(f'  Region: {region.name} - {status}')

        # Create Project Statuses
        statuses_data = [
            {'name': 'IP', 'category': 'active', 'color': '#0d6efd', 'order': 1},
            {'name': 'Open', 'category': 'active', 'color': '#0dcaf0', 'order': 2},
            {'name': 'Submitted', 'category': 'active', 'color': '#6f42c1', 'order': 3},
            {'name': 'Hold', 'category': 'active', 'color': '#ffc107', 'order': 4},
            {'name': 'Hot Lead', 'category': 'hot_lead', 'color': '#fd7e14', 'order': 5},
            {'name': 'Won', 'category': 'won', 'color': '#198754', 'order': 6},
            {'name': 'Closed', 'category': 'won', 'color': '#20c997', 'order': 7},
            {'name': 'Lost', 'category': 'lost', 'color': '#dc3545', 'order': 8},
            {'name': 'Awarded', 'category': 'ongoing', 'color': '#198754', 'order': 9},
            {'name': 'Ongoing', 'category': 'ongoing', 'color': '#0d6efd', 'order': 10},
        ]
        for status_data in statuses_data:
            status, created = ProjectStatus.objects.get_or_create(
                name=status_data['name'],
                defaults={
                    'category': status_data['category'],
                    'color': status_data['color'],
                    'order': status_data['order']
                }
            )
            result = 'Created' if created else 'Already exists'
            self.stdout.write(f'  Status: {status.name} - {result}')

        self.stdout.write(self.style.SUCCESS('Initial data loaded successfully!'))
