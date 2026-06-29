from django.contrib import admin

from .models import ProjectFinance, PaymentMilestone


class PaymentMilestoneInline(admin.TabularInline):
    model = PaymentMilestone
    extra = 0


@admin.register(ProjectFinance)
class ProjectFinanceAdmin(admin.ModelAdmin):
    list_display = ['project', 'po_value', 'approved_margin', 'kickoff_date', 'updated_at']
    search_fields = ['project__project_name']
    inlines = [PaymentMilestoneInline]
