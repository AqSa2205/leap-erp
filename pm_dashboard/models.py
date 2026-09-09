from django.conf import settings
from django.db import models


def default_responsibility_columns():
    """Starting columns for a fresh Responsibility Matrix — a RACI table.
    Fully editable per-project: headers can be renamed/added/removed; each
    column keeps a stable `key` so a row's cells survive a rename."""
    return [
        {'key': 'c1', 'name': 'Task / Activity'},
        {'key': 'c2', 'name': 'Responsible'},
        {'key': 'c3', 'name': 'Accountable'},
        {'key': 'c4', 'name': 'Consulted'},
        {'key': 'c5', 'name': 'Informed'},
        {'key': 'c6', 'name': 'Notes'},
    ]


def default_communication_columns():
    """Starting columns for a fresh Communication Matrix."""
    return [
        {'key': 'c1', 'name': 'Communication Item'},
        {'key': 'c2', 'name': 'Frequency'},
        {'key': 'c3', 'name': 'Owner'},
        {'key': 'c4', 'name': 'Client Audience'},
        {'key': 'c5', 'name': 'Channel'},
        {'key': 'c6', 'name': 'Deliverable'},
        {'key': 'c7', 'name': 'Notes'},
    ]


def sanitize_grid(columns_raw, rows_raw, fallback_columns):
    """Coerce posted columns/rows JSON into the stored grid shape, dropping
    anything malformed. Mirrors costing._sanitize_remark_grid's contract.

    columns -> [{"key": "c1", "name": "..."}, ...]  (keys forced unique,
                blank names allowed — the header is free-text)
    rows    -> [{"cells": {key: {"text": str}}}, ...]  (only cells whose key
                maps to a surviving column are kept; all-empty rows dropped)
    """
    columns, seen_keys = [], set()
    for idx, col in enumerate(columns_raw if isinstance(columns_raw, list) else []):
        if not isinstance(col, dict):
            continue
        key = str(col.get('key') or '').strip() or f'c{idx + 1}'
        while key in seen_keys:
            key = f'{key}_{idx}'
        seen_keys.add(key)
        name = str(col.get('name') or '').strip()
        columns.append({'key': key, 'name': name})

    if not columns:
        columns = list(fallback_columns)
        seen_keys = {c['key'] for c in columns}

    rows = []
    for row in rows_raw if isinstance(rows_raw, list) else []:
        cells_raw = (row or {}).get('cells') if isinstance(row, dict) else None
        cells = {}
        if isinstance(cells_raw, dict):
            for key, data in cells_raw.items():
                if key not in seen_keys or not isinstance(data, dict):
                    continue
                cells[key] = {'text': str(data.get('text') or '')}
        if any((c.get('text') or '').strip() for c in cells.values()):
            rows.append({'cells': cells})
    return columns, rows


class ResponsibilityMatrix(models.Model):
    """Who does what on a project — a RACI-style grid. Columns are fully
    customizable per project (same JSON-grid shape as
    costing.ClientRemarkTemplate) rather than a fixed schema, since this is
    a first version and the exact columns a PM wants are still unclear."""
    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE,
        related_name='responsibility_matrix',
    )
    columns = models.JSONField(default=default_responsibility_columns)
    rows = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Responsibility Matrix — {self.project.project_name}"


class CommunicationMatrix(models.Model):
    """The project's communication plan, shared with the client as a PDF
    report. Same customizable JSON-grid shape as ResponsibilityMatrix."""
    project = models.OneToOneField(
        'projects.Project', on_delete=models.CASCADE,
        related_name='communication_matrix',
    )
    columns = models.JSONField(default=default_communication_columns)
    rows = models.JSONField(default=list)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Communication Matrix — {self.project.project_name}"
