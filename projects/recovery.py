"""Recover a hard-deleted Project from a Render Point-in-Time-Recovery copy.

Shared by the `recover_project` management command and the super-admin browser
recovery page. Reads the deleted project from a read-only 'recovery' DB
connection (configured from RECOVERY_DATABASE_URL) and rebuilds it in the LIVE
('default') database: recreates the Project (same pk + fields), relinks every
surviving related row (SET_NULL: costing sheets/BOMs, POs, DNs, proposals…), and
optionally recreates the CASCADE-deleted children (history/revisions/finance).
"""
from django.conf import settings
from django.db import transaction

REC = 'recovery'
DST = 'default'


def recovery_available():
    return REC in settings.DATABASES


def _copy_instance(model, src_obj, dst_db):
    """Insert a copy of src_obj into dst_db keeping its pk, FK ids and the
    original auto_now/auto_now_add timestamps."""
    fields = list(model._meta.concrete_fields)
    data = {f.attname: getattr(src_obj, f.attname) for f in fields}
    obj = model(**data)
    obj.save(using=dst_db, force_insert=True)
    autos = {f.attname: getattr(src_obj, f.attname) for f in fields
             if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False)}
    if autos:
        model.objects.using(dst_db).filter(pk=obj.pk).update(**autos)
    return obj


def run_recovery(reference=None, pk=None, with_cascaded=True, commit=False):
    """Plan (and, if commit=True, apply) the recovery. Returns a report dict.

    Raises RuntimeError if the recovery DB isn't configured, ValueError if the
    project can't be found in the recovery copy.
    """
    if not recovery_available():
        raise RuntimeError("RECOVERY_DATABASE_URL is not configured.")
    from projects.models import Project

    src = Project.objects.using(REC)
    proj = (src.filter(pk=pk).first() if pk
            else src.filter(proposal_reference=reference).first())
    if not proj:
        raise ValueError("Project not found in the recovery database.")

    already = Project.objects.using(DST).filter(pk=proj.pk).exists()

    relinks, recreations = [], []
    for rel in Project._meta.related_objects:
        model = rel.related_model
        fk = rel.field.attname
        rec_pks = list(model.objects.using(REC).filter(**{fk: proj.pk})
                       .values_list('pk', flat=True))
        if not rec_pks:
            continue
        surviving = set(model.objects.using(DST).filter(pk__in=rec_pks)
                        .values_list('pk', flat=True))
        survivors = [p for p in rec_pks if p in surviving]
        lost = [p for p in rec_pks if p not in surviving]
        if survivors:
            relinks.append({'label': model._meta.label, 'model': model,
                            'fk': fk, 'pks': survivors})
        if lost:
            recreations.append({'label': model._meta.label, 'model': model,
                                'fk': fk, 'pks': lost, 'on_delete': rel.on_delete.__name__})

    report = {
        'pk': proj.pk,
        'reference': proj.proposal_reference,
        'name': proj.project_name,
        'already_exists': already,
        'relinks': [{'label': r['label'], 'count': len(r['pks'])} for r in relinks],
        'recreations': [{'label': r['label'], 'count': len(r['pks']),
                         'on_delete': r['on_delete']} for r in recreations],
        'with_cascaded': with_cascaded,
        'committed': False,
    }
    if not commit:
        return report

    with transaction.atomic(using=DST):
        if not already:
            _copy_instance(Project, proj, DST)
        for r in relinks:
            r['model'].objects.using(DST).filter(pk__in=r['pks']).update(**{r['fk']: proj.pk})
        if with_cascaded:
            for r in recreations:
                for p in r['pks']:
                    srcobj = r['model'].objects.using(REC).filter(pk=p).first()
                    if srcobj and not r['model'].objects.using(DST).filter(pk=p).exists():
                        _copy_instance(r['model'], srcobj, DST)
            # Finance payment milestones hang off ProjectFinance (not Project) —
            # pull them along so the budget detail is restored too.
            try:
                from finance.models import ProjectFinance, PaymentMilestone
                pf_pks = list(ProjectFinance.objects.using(DST)
                              .filter(project_id=proj.pk).values_list('pk', flat=True))
                for m in PaymentMilestone.objects.using(REC).filter(project_finance_id__in=pf_pks):
                    if not PaymentMilestone.objects.using(DST).filter(pk=m.pk).exists():
                        _copy_instance(PaymentMilestone, m, DST)
            except Exception:
                pass  # milestone model/name mismatch — non-fatal

    report['committed'] = True
    return report
