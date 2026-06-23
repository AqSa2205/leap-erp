"""Central file-cleanup signals.

Django never deletes a FileField's stored file when its row is deleted or when
the field is replaced — you must do it explicitly. The app did that only in a
handful of single-row delete views, so cascade deletes, bulk QuerySet deletes,
and file replacements leaked files into R2 (the "orphan" problem).

This wires two signals onto every file-bearing model:
  * post_delete -> delete the file(s) when a row is removed. Registering a
    receiver disables Django's fast-delete path, so this also fires for
    cascade-deleted and queryset.delete()'d instances — exactly the paths the
    per-view cleanup missed.
  * pre_save -> when an existing row's file is being replaced (or cleared),
    delete the previous file. With AWS_S3_FILE_OVERWRITE=False a replacement
    writes a new key and would otherwise abandon the old object.

Existing per-view file.delete() calls stay in place; deleting an already-gone
file is a harmless no-op, so the signals are idempotent.
"""
import logging

from django.apps import apps
from django.db.models.signals import post_delete, pre_save

logger = logging.getLogger(__name__)


def _file_fields_by_model():
    """{(app_label, model_name): [field, ...]} derived from the storage-report
    registry so the two never drift."""
    from dashboard.storage_report import FILE_CLASSES
    out = {}
    for _label, app_label, model_name, field in FILE_CLASSES:
        out.setdefault((app_label, model_name), []).append(field)
    return out


def _delete_file(fieldfile):
    if fieldfile and fieldfile.name:
        try:
            fieldfile.delete(save=False)
        except Exception:
            logger.exception('storage cleanup: could not delete %s',
                             getattr(fieldfile, 'name', '?'))


def _make_post_delete(fields):
    def handler(sender, instance, **kwargs):
        for name in fields:
            _delete_file(getattr(instance, name, None))
    return handler


def _make_pre_save(fields):
    def handler(sender, instance, **kwargs):
        if not instance.pk:
            return  # brand-new row: nothing to replace
        try:
            old = sender.objects.only(*fields).get(pk=instance.pk)
        except sender.DoesNotExist:
            return
        for name in fields:
            old_f = getattr(old, name, None)
            new_f = getattr(instance, name, None)
            new_name = new_f.name if new_f else None
            if old_f and old_f.name and old_f.name != new_name:
                _delete_file(old_f)
    return handler


def register_file_cleanup():
    """Connect the cleanup signals. Idempotent (dispatch_uid-guarded)."""
    for (app_label, model_name), fields in _file_fields_by_model().items():
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue  # app/model not installed
        uid = f'storage_cleanup:{app_label}.{model_name}'
        # weak=False: the handlers are closures that would otherwise be GC'd.
        post_delete.connect(_make_post_delete(tuple(fields)), sender=model,
                            dispatch_uid=uid + ':post_delete', weak=False)
        pre_save.connect(_make_pre_save(tuple(fields)), sender=model,
                         dispatch_uid=uid + ':pre_save', weak=False)
