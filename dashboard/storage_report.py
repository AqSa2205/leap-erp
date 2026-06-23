"""Shared storage-usage reporting (used by the admin page and the CLI command).

One scan of the active storage backend builds a {key: size} index; the DB's
referenced file names are then matched against it. This yields, in a single
pass and without per-file HEAD requests:
  * referenced usage per file class (count + bytes),
  * referenced-but-missing files (row points at an absent object),
  * orphans (objects in storage no DB row references — the residue of
    cascade / bulk deletes that never cleaned up their files).

Run against the production backend (R2) for meaningful numbers; locally it
only sees the dev media/ dir.
"""
import os

from django.apps import apps
from django.core.files.storage import default_storage


# (label, app_label, model_name, field_name)
FILE_CLASSES = [
    ('Costing revisions',     'costing',     'CostingSheetRevision', 'file'),
    ('Vendor quotes',         'costing',     'VendorQuote',          'file'),
    ('Project documents',     'projects',    'Document',             'file'),
    ('PQD attachments',       'proposals',   'PQDAttachment',        'file'),
    ('PO signature (SCM)',    'procurement', 'PurchaseOrder',        'scm_signature'),
    ('PO signature (PM)',     'procurement', 'PurchaseOrder',        'pm_signature'),
    ('PO signature (COO)',    'procurement', 'PurchaseOrder',        'coo_signature'),
    ('PO signature (CEO)',    'procurement', 'PurchaseOrder',        'ceo_signature'),
    ('Employee documents',    'hr',          'EmployeeDocument',     'file'),
    ('Vehicle documents',     'hr',          'VehicleDocument',      'file'),
    ('Asset handover forms',  'hr',          'AssetAssignment',      'handover_form'),
    ('Leave medical certs',   'hr',          'LeaveRecord',          'medical_certificate'),
    ('Company documents',     'company',     'CompanyDocument',      'file'),
]

# Cloudflare R2 free tier, for the % readout.
DEFAULT_QUOTA_GB = 10

# Prefixes whose objects are referenced from inside rich-text HTML (TinyMCE
# inline images), NOT via a FileField. They must never be flagged as orphans —
# the DB references them by URL inside content fields, so "no FileField points
# here" does not mean unused.
ORPHAN_EXCLUDE_PREFIXES = ('proposals/images/',)


def _build_size_index():
    """Scan the active backend once. Returns (index, backend_label, is_s3),
    where index maps storage key -> size in bytes, keyed the same way as
    FieldFile.name (storage location prefix stripped)."""
    if hasattr(default_storage, 'bucket'):  # django-storages S3/R2
        loc = (getattr(default_storage, 'location', '') or '').strip('/')
        index = {}
        for obj in default_storage.bucket.objects.all():
            key = obj.key
            if loc and key.startswith(loc + '/'):
                key = key[len(loc) + 1:]
            if key.endswith('/'):
                continue  # directory placeholder
            index[key] = obj.size
        return index, default_storage.__class__.__name__, True

    # Local filesystem
    index = {}
    root = getattr(default_storage, 'location', None)
    if root and os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, '/')
                try:
                    index[rel] = os.path.getsize(full)
                except OSError:
                    pass
    return index, default_storage.__class__.__name__, False


def _referenced_files():
    """Yield (label, name) for every file the DB references."""
    for label, app_label, model_name, field in FILE_CLASSES:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        qs = (model.objects
              .exclude(**{field: ''})
              .exclude(**{f'{field}__isnull': True})
              .only('pk', field))
        for obj in qs.iterator():
            f = getattr(obj, field)
            if f and f.name:
                yield label, f.name


def build_storage_report(quota_gb=DEFAULT_QUOTA_GB):
    """Compute the full storage report as a plain dict (template/JSON friendly)."""
    index, backend, is_s3 = _build_size_index()

    # Preserve FILE_CLASSES order in the output.
    classes = {}
    for label, *_ in FILE_CLASSES:
        classes.setdefault(label, {'label': label, 'count': 0, 'bytes': 0, 'missing': 0})

    referenced = set()
    for label, name in _referenced_files():
        referenced.add(name)
        row = classes[label]
        if name in index:
            row['count'] += 1
            row['bytes'] += index[name]
        else:
            row['missing'] += 1

    class_rows = list(classes.values())
    total_bytes = sum(r['bytes'] for r in class_rows)
    total_files = sum(r['count'] for r in class_rows)
    total_missing = sum(r['missing'] for r in class_rows)

    def _is_orphan(key):
        if key in referenced:
            return False
        return not key.startswith(ORPHAN_EXCLUDE_PREFIXES)

    orphans = sorted(
        ((k, s) for k, s in index.items() if _is_orphan(k)),
        key=lambda x: x[1], reverse=True)
    orphan_bytes = sum(s for _, s in orphans)

    quota_bytes = int(quota_gb * 1024 ** 3)
    return {
        'backend': backend,
        'is_s3': is_s3,
        'classes': class_rows,
        'total_bytes': total_bytes,
        'total_files': total_files,
        'total_missing': total_missing,
        'orphan_count': len(orphans),
        'orphan_bytes': orphan_bytes,
        'orphan_examples': [{'key': k, 'bytes': s} for k, s in orphans[:20]],
        'quota_gb': quota_gb,
        'quota_bytes': quota_bytes,
        'pct_used': (100 * total_bytes / quota_bytes) if quota_bytes else 0,
    }
