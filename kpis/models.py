from django.db import models
from django.conf import settings


class KPIEntry(models.Model):
    """Per-period management input for one KPI.

    Holds two optional numbers:
      * `target`       — the goal/threshold for the period (overrides the KPI's
                         default target; for goal-based auto KPIs like revenue,
                         this is the only place the goal lives).
      * `manual_value` — the actual value for KPIs with no ERP source (the team
                         types it). Ignored for auto KPIs, whose value is computed.

    One row per (period, kpi_key). The KPI itself is defined in code
    (`kpis.registry`), so `kpi_key` is a loose CharField, not an FK.
    """
    period = models.CharField(max_length=10, help_text="e.g. '2026-06', '2026-Q2', '2026'")
    kpi_key = models.CharField(max_length=60)
    target = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    manual_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='kpi_entries',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('period', 'kpi_key')
        ordering = ['period', 'kpi_key']
        verbose_name = 'KPI Entry'
        verbose_name_plural = 'KPI Entries'

    def __str__(self):
        return f'{self.kpi_key} @ {self.period}'
