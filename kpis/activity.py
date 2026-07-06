"""Registry of countable user actions across the ERP, for the Team Activity
review. Each metric is one grouped COUNT query over an existing model, keyed by
an actor FK and time-scoped by a date field. No new tables — reads the
created_by / handover fields already on the records.
"""
from collections import defaultdict
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
        # Require BOTH the actor and the date to be set: an action only counts
        # if it actually happened (its date field is populated). This matters
        # when actor and date are set at different lifecycle moments — e.g.
        # DevTask.developer is set at assignment but completed_at only at
        # completion, so "tasks completed" must not count merely-assigned tasks.
        Model = apps.get_model(self.model_path)
        qs = Model.objects.filter(**{f'{self.actor_field}__isnull': False,
                                     f'{self.date_field}__isnull': False})
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


ACTIVITY_BY_KEY = {m.key: m for m in ACTIVITY_METRICS}


# ── Workflow cycle-time metrics ───────────────────────────────────────────────
# Average days a person's stage takes, tied to the handover timestamps on the
# costing sheet: pipeline → BOM handed to sales → sales hands to finance →
# finance budgets → procurement raises the PO. Attributed to the actor who
# COMPLETED the stage, windowed by that completion date.
@dataclass(frozen=True)
class CycleMetric:
    key: str
    department: str
    label: str
    model_path: str
    actor_field: str      # who completed the stage
    start_field: str      # start datetime (may traverse a FK, e.g. project__created_at)
    end_field: str        # completion datetime

    def per_actor_avg(self, start, end):
        """{user_id: avg_days} over records whose stage COMPLETED in the window."""
        Model = apps.get_model(self.model_path)
        qs = Model.objects.filter(**{
            f'{self.actor_field}__isnull': False,
            f'{self.start_field}__isnull': False,
            f'{self.end_field}__isnull': False,
        })
        if start is not None:
            qs = qs.filter(**{f'{self.end_field}__date__gte': start,
                              f'{self.end_field}__date__lt': end})
        acc = defaultdict(list)
        for actor_id, s, e in qs.values_list(self.actor_field, self.start_field, self.end_field):
            if s and e:
                days = (e - s).total_seconds() / 86400.0
                if days >= 0:
                    acc[actor_id].append(days)
        return {uid: round(sum(v) / len(v), 1) for uid, v in acc.items()}


PROC_CYCLE_KEY = 'cyc_procurement'

CYCLE_METRICS = [
    CycleMetric('cyc_proposal', 'proposal', 'Avg days: pipeline → sales',
                'costing.CostingSheet', 'handed_over_by', 'project__created_at', 'handed_over_at'),
    CycleMetric('cyc_sales', 'sales', 'Avg days: sales → finance',
                'costing.CostingSheet', 'finance_review_by', 'handed_over_at', 'finance_review_at'),
    CycleMetric('cyc_finance', 'finance', 'Avg days: budgeting',
                'costing.CostingSheet', 'finance_approved_by', 'finance_review_at', 'finance_approved_at'),
]

CYCLE_LABELS = {cm.key: cm.label for cm in CYCLE_METRICS}
CYCLE_LABELS[PROC_CYCLE_KEY] = 'Avg days: finance → PO'


def _procurement_cycle_avg(start, end):
    """Avg days from a project's finance approval (budget released) to the PO
    being raised, per PO creator — the procurement leg of the chain."""
    from procurement.models import PurchaseOrder
    from costing.models import CostingSheet
    approved = {}
    for pid, ts in (CostingSheet.objects
                    .filter(finance_approved_at__isnull=False, project__isnull=False)
                    .values_list('project_id', 'finance_approved_at')):
        if pid not in approved or ts < approved[pid]:
            approved[pid] = ts
    pos = PurchaseOrder.objects.filter(created_by__isnull=False, project__isnull=False)
    if start is not None:
        pos = pos.filter(po_date__gte=start, po_date__lt=end)
    acc = defaultdict(list)
    for uid, pid, po_date in pos.values_list('created_by', 'project_id', 'po_date'):
        ts = approved.get(pid)
        if not ts:
            continue
        days = (po_date - ts.date()).days
        if days >= 0:
            acc[uid].append(days)
    return {uid: round(sum(v) / len(v), 1) for uid, v in acc.items()}


def all_cycle_data(start, end):
    """{cycle_key: {user_id: avg_days}} for every cycle metric."""
    data = {cm.key: cm.per_actor_avg(start, end) for cm in CYCLE_METRICS}
    data[PROC_CYCLE_KEY] = _procurement_cycle_avg(start, end)
    return data


# ── Which activity a department owns ──────────────────────────────────────────
# Each department's Team Activity table shows only the actions it's responsible
# for (count columns) plus its stage cycle-time (avg-days column). Finance is
# budgeting-only (no BOM/proposal); procurement is POs/DNs + the finance→PO time.
DEPARTMENT_ACTIVITY = {
    'management':  {'metrics': ['projects_created', 'status_changes', 'documents_uploaded'], 'cycles': []},
    'proposal':    {'metrics': ['boms_created', 'tech_proposals', 'pqds', 'boms_handed_to_sales'], 'cycles': ['cyc_proposal']},
    'sales':       {'metrics': ['costing_started', 'sales_finalised', 'handed_to_finance'], 'cycles': ['cyc_sales']},
    'finance':     {'metrics': ['finance_approved'], 'cycles': ['cyc_finance']},
    'procurement': {'metrics': ['pos_created', 'po_scm_approvals', 'delivery_notes', 'inventory_reports'], 'cycles': [PROC_CYCLE_KEY]},
    'ai':          {'metrics': ['stacks_created', 'tasks_completed'], 'cycles': []},
    'other':       {'metrics': ['projects_created', 'documents_uploaded'], 'cycles': []},
}
