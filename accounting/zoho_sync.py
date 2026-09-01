"""Recording Zoho Books' chart of accounts against the ERP's.

Shared by the `sync_zoho_accounts` command and the Zoho connection screen. The
screen is the one that matters in production: the web service runs on a plan
with no shell, so a management command cannot be run there at all. Anything
that has to happen on the live system has to be reachable from the UI.

This never touches the ERP's own chart. That chart is the structure the finance
team designed and Zoho's data is coded *into* it — overwriting it from Zoho
would destroy the very thing being built. Rows land in `ZohoAccountMap`, and an
unmapped one is a worklist item rather than a failure: a transaction that
quietly vanishes is far worse than one that shows up asking where it belongs.
"""
from .models import Account, ZohoAccountMap


def upsert_accounts(records):
    """Record every Zoho account, matched on Zoho's own account id.

    Matched on id rather than code because codes are editable in Zoho and ids
    are not — so an edit in Zoho updates a row here instead of orphaning it and
    silently creating a second one.
    """
    created = updated = 0
    for record in records:
        _, was_created = ZohoAccountMap.objects.update_or_create(
            zoho_account_id=str(record.get('account_id')),
            defaults={
                'zoho_account_code': str(record.get('account_code') or '').strip(),
                'zoho_account_name': str(record.get('account_name') or '').strip(),
                'zoho_account_type': str(record.get('account_type') or '').strip(),
                'zoho_parent_name': str(record.get('parent_account_name') or '').strip(),
                'zoho_is_active': bool(record.get('is_active', True)),
            })
        created += was_created
        updated += not was_created
    return created, updated


def automap_by_code():
    """Fill blank mappings where the account codes match exactly.

    A suggestion engine, and a conservative one: it never overwrites a mapping
    finance has already made, and only acts on an exact code match.

    Worth knowing before relying on it: this organisation never switched
    Account Codes on in Zoho, so every code arrives blank and this proposes
    nothing. The name-based matching on the mapping screen is what actually
    moves the worklist — see accounting.mapping.
    """
    by_code = {a.code: a for a in Account.objects.postable()}
    proposed = 0
    for row in ZohoAccountMap.objects.unmapped():
        if not row.zoho_account_code:
            continue
        target = by_code.get(row.zoho_account_code)
        if target is not None:
            row.account = target
            row.note = (row.note + '\nAuto-mapped on exact code match.').strip()
            row.save(update_fields=['account', 'note', 'last_seen_at'])
            proposed += 1
    return proposed


def mapping_counts():
    """Where the worklist stands, for a status panel."""
    everything = ZohoAccountMap.objects.all()
    return {
        'total': everything.count(),
        'mapped': everything.mapped().count(),
        'unmapped': everything.unmapped().count(),
        'ignored': everything.filter(is_ignored=True).count(),
    }
