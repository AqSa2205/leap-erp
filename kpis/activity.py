"""Registry of countable user actions across the ERP, for the Team Activity
review. Each metric is one grouped COUNT query over an existing model, keyed by
an actor FK and time-scoped by a date field. No new tables — reads the
created_by / handover fields already on the records.
"""
from dataclasses import dataclass

from django.apps import apps
from django.db.models import Count


MODULE_ORDER = ['Pipeline', 'Costing', 'Procurement', 'Proposals', 'Dev Tracking']


@dataclass(frozen=True)
class ActivityMetric:
    key: str
    module: str
    label: str
    model_path: str      # 'projects.Project'
    actor_field: str     # 'created_by'
    date_field: str      # 'created_at'
    headline: bool = False

    def _base(self, start, end, user_id=None):
        Model = apps.get_model(self.model_path)
        qs = Model.objects.filter(**{f'{self.actor_field}__isnull': False})
        if user_id is not None:
            qs = qs.filter(**{self.actor_field: user_id})
        if start is not None:
            qs = qs.filter(**{f'{self.date_field}__date__gte': start,
                              f'{self.date_field}__date__lt': end})
        return qs

    def counts(self, start, end):
        """{user_id: count} grouped by actor over the window (None = all-time)."""
        rows = self._base(start, end).values(self.actor_field).annotate(n=Count('id'))
        return {r[self.actor_field]: r['n'] for r in rows}

    def count_for(self, start, end, user_id):
        return self._base(start, end, user_id=user_id).count()


ACTIVITY_METRICS = [
    # Pipeline
    ActivityMetric('projects_created', 'Pipeline', 'Pipelines created',
                   'projects.Project', 'created_by', 'created_at', headline=True),
    ActivityMetric('status_changes', 'Pipeline', 'Status changes made',
                   'projects.ProjectHistory', 'changed_by', 'changed_at'),
    ActivityMetric('documents_uploaded', 'Pipeline', 'Documents uploaded',
                   'projects.Document', 'uploaded_by', 'uploaded_at'),
    ActivityMetric('project_revisions', 'Pipeline', 'Proposal revisions',
                   'projects.ProjectRevision', 'created_by', 'created_at'),
    # Costing
    ActivityMetric('boms_created', 'Costing', 'BOMs created',
                   'costing.CostingSheet', 'created_by', 'created_at', headline=True),
    ActivityMetric('boms_handed_to_sales', 'Costing', 'Handed to sales',
                   'costing.CostingSheet', 'handed_over_by', 'handed_over_at'),
    ActivityMetric('costing_started', 'Costing', 'Costing started',
                   'costing.CostingSheet', 'costing_started_by', 'costing_started_at'),
    ActivityMetric('sales_finalised', 'Costing', 'Sales finalised',
                   'costing.CostingSheet', 'finalized_by', 'finalized_at', headline=True),
    ActivityMetric('handed_to_finance', 'Costing', 'Handed to finance',
                   'costing.CostingSheet', 'finance_review_by', 'finance_review_at', headline=True),
    ActivityMetric('finance_approved', 'Costing', 'Finance approved',
                   'costing.CostingSheet', 'finance_approved_by', 'finance_approved_at'),
    # Procurement
    ActivityMetric('pos_created', 'Procurement', 'POs created',
                   'procurement.PurchaseOrder', 'created_by', 'created_at', headline=True),
    ActivityMetric('po_scm_approvals', 'Procurement', 'PO SCM approvals',
                   'procurement.PurchaseOrder', 'scm_approved_by', 'scm_approved_at'),
    ActivityMetric('po_pm_approvals', 'Procurement', 'PO PM approvals',
                   'procurement.PurchaseOrder', 'pm_approved_by', 'pm_approved_at'),
    ActivityMetric('po_coo_approvals', 'Procurement', 'PO COO approvals',
                   'procurement.PurchaseOrder', 'coo_approved_by', 'coo_approved_at'),
    ActivityMetric('po_ceo_approvals', 'Procurement', 'PO CEO approvals',
                   'procurement.PurchaseOrder', 'ceo_approved_by', 'ceo_approved_at'),
    ActivityMetric('delivery_notes', 'Procurement', 'Delivery notes',
                   'procurement.DeliveryNote', 'created_by', 'created_at'),
    ActivityMetric('inventory_reports', 'Procurement', 'Inventory reports',
                   'procurement.InventoryReport', 'created_by', 'created_at'),
    # Proposals
    ActivityMetric('tech_proposals', 'Proposals', 'Technical proposals',
                   'proposals.TechnicalProposal', 'created_by', 'created_at', headline=True),
    ActivityMetric('pqds', 'Proposals', 'Prequalification docs',
                   'proposals.PrequalificationDocument', 'created_by', 'created_at'),
    # Dev Tracking
    ActivityMetric('stacks_created', 'Dev Tracking', 'Task stacks created',
                   'devtracking.TaskStack', 'created_by', 'created_at'),
    ActivityMetric('tasks_completed', 'Dev Tracking', 'Tasks completed',
                   'devtracking.DevTask', 'developer', 'completed_at', headline=True),
]


def headline_metrics():
    return [m for m in ACTIVITY_METRICS if m.headline]
