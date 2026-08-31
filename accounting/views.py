from datetime import datetime
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .chart_import import (
    ChartImportError, apply as apply_chart, parse_rows, plan, read_grid,
)
from .mapping import certain_matches, index_accounts, suggest
from .models import (
    Account, Voucher, VoucherLine, ZohoAccountMap, build_tree, descendant_ids,
    subtree_counts,
)


def _can_view_accounting(user):
    """Finance owns the chart of accounts; super admin sees everything.

    Matches how the Finance section is gated today (role-based rather than
    capability-based) so the two sit together consistently in the sidebar.
    """
    return bool(getattr(user, 'is_super_admin_user', False)
                or getattr(user, 'is_finance_team_user', False))


@login_required
def chart_of_accounts(request):
    """Browsable Chart of Accounts — the imported GL master.

    Renders the whole chart as an indented tree. Searching or filtering
    narrows the rows; depth is recomputed over whatever survives the filter so
    the result never renders with orphaned indentation.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    accounts = Account.objects.all()

    search = (request.GET.get('q') or '').strip()
    if search:
        accounts = accounts.filter(Q(code__icontains=search) | Q(name__icontains=search))

    internal_type = (request.GET.get('type') or '').strip()
    if internal_type in dict(Account.INTERNAL_TYPE_CHOICES):
        accounts = accounts.filter(internal_type=internal_type)

    account_class = (request.GET.get('class') or '').strip()
    if account_class in Account.CLASS_LABELS:
        accounts = accounts.filter(code__startswith=account_class)

    postable = (request.GET.get('postable') or '').strip()
    if postable == '1':
        accounts = accounts.postable()
    elif postable == '0':
        accounts = accounts.headings()

    if (request.GET.get('inactive') or '') != '1':
        accounts = accounts.active()

    rows = build_tree(accounts.select_related('parent'))

    # "What's underneath" is counted over the WHOLE chart, not the filtered
    # rows, so a heading still reports its true size while you are filtering.
    counts = subtree_counts(Account.objects.all())
    for row in rows:
        row.counts = counts.get(row.pk, {'direct': 0, 'total': 0, 'postable': 0})

    # Totals describe the whole chart, not the filtered view, so the header
    # numbers stay a stable reference while you search.
    everything = Account.objects.all()
    context = {
        'rows': rows,
        'search': search,
        'selected_type': internal_type,
        'selected_class': account_class,
        'selected_postable': postable,
        'show_inactive': (request.GET.get('inactive') or '') == '1',
        'type_choices': Account.INTERNAL_TYPE_CHOICES,
        'class_choices': sorted(Account.CLASS_LABELS.items()),
        'total_count': everything.count(),
        'postable_count': everything.postable().count(),
        'heading_count': everything.headings().count(),
        'is_filtered': bool(search or internal_type or account_class or postable),
    }
    return render(request, 'accounting/chart_of_accounts.html', context)


@login_required
def account_detail(request, code):
    """One account, and everything filed beneath it.

    This is the drill-down half of the chart: a heading shows the accounts it
    groups, each of which can be drilled into in turn. A postable account has
    nothing underneath, so it shows its siblings instead — which is what you
    actually want when picking the right account to code something to.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    account = get_object_or_404(Account, code=code)

    # Breadcrumb, outermost class first.
    ancestors, node, guard = [], account.parent, 0
    while node is not None and guard < 12:
        ancestors.append(node)
        node = node.parent
        guard += 1
    ancestors.reverse()

    counts = subtree_counts(Account.objects.all())
    children = list(account.children.select_related('parent'))
    for child in children:
        child.counts = counts.get(child.pk, {'direct': 0, 'total': 0, 'postable': 0})

    siblings = []
    if not children and account.parent_id:
        siblings = list(Account.objects.filter(parent_id=account.parent_id)
                        .exclude(pk=account.pk))

    return render(request, 'accounting/account_detail.html', {
        'account': account,
        'ancestors': ancestors,
        'children': children,
        'siblings': siblings,
        'counts': counts.get(account.pk, {'direct': 0, 'total': 0, 'postable': 0}),
    })


def _parse_date(value):
    """Accept an ISO date from the filter form, ignoring anything unparseable
    rather than 500-ing on a hand-edited query string."""
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else None
    except (TypeError, ValueError):
        return None


@login_required
def account_ledger(request, code):
    """Every transaction against one account, with a running balance.

    This is what turns the chart from a directory of names into an accounting
    system. A heading has no postings of its own, so opening it shows the
    combined ledger of every account beneath it.

    Only POSTED vouchers count by default — a draft has not been checked for
    balance, so including it silently would make the running balance wrong.
    Drafts can be shown deliberately via ?drafts=1.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    account = get_object_or_404(Account, code=code)
    account_ids = descendant_ids(account)
    is_rollup = len(account_ids) > 1

    date_from = _parse_date(request.GET.get('from'))
    date_to = _parse_date(request.GET.get('to'))
    include_drafts = request.GET.get('drafts') == '1'

    lines = (VoucherLine.objects
             .filter(account_id__in=account_ids)
             .select_related('voucher', 'account', 'partner', 'project'))
    if not include_drafts:
        lines = lines.filter(voucher__status=Voucher.STATUS_POSTED)

    # Opening balance is everything BEFORE the window — without it a filtered
    # ledger would start from zero and every running balance below would be
    # wrong.
    opening_debit = opening_credit = Decimal('0')
    if date_from:
        prior = lines.filter(voucher__date__lt=date_from).aggregate(
            d=Sum('debit'), c=Sum('credit'))
        opening_debit = prior['d'] or Decimal('0')
        opening_credit = prior['c'] or Decimal('0')
        lines = lines.filter(voucher__date__gte=date_from)
    if date_to:
        lines = lines.filter(voucher__date__lte=date_to)

    lines = lines.order_by('voucher__date', 'voucher_id', 'order', 'id')

    opening = account.signed_balance(opening_debit, opening_credit)
    running = opening
    rows, period_debit, period_credit = [], Decimal('0'), Decimal('0')
    for line in lines:
        movement = (line.debit - line.credit)
        if account.natural_side == 'credit':
            movement = -movement
        running += movement
        period_debit += line.debit
        period_credit += line.credit
        rows.append({'line': line, 'balance': running})

    return render(request, 'accounting/account_ledger.html', {
        'account': account,
        'rows': rows,
        'is_rollup': is_rollup,
        'rollup_count': len(account_ids),
        'opening': opening,
        'closing': running,
        'period_debit': period_debit,
        'period_credit': period_credit,
        'date_from': request.GET.get('from', ''),
        'date_to': request.GET.get('to', ''),
        'include_drafts': include_drafts,
        'is_filtered': bool(date_from or date_to or include_drafts),
    })


# ── Zoho account mapping ────────────────────────────────────────────────────

ZOHO_STATE_FILTERS = {
    'unmapped': 'Needs mapping',
    'mapped': 'Mapped',
    'ignored': 'Ignored',
    'all': 'All',
}
ZOHO_PAGE_SIZE = 50


def _zoho_rows(request):
    """The mapping worklist, narrowed by whatever the filters say."""
    rows = ZohoAccountMap.objects.select_related('account')

    state = (request.GET.get('state') or 'unmapped').strip()
    if state not in ZOHO_STATE_FILTERS:
        state = 'unmapped'
    if state == 'unmapped':
        rows = rows.unmapped()
    elif state == 'mapped':
        rows = rows.mapped()
    elif state == 'ignored':
        rows = rows.filter(is_ignored=True)

    zoho_type = (request.GET.get('type') or '').strip()
    if zoho_type:
        rows = rows.filter(zoho_account_type=zoho_type)

    search = (request.GET.get('q') or '').strip()
    if search:
        rows = rows.filter(Q(zoho_account_name__icontains=search)
                           | Q(zoho_account_code__icontains=search)
                           | Q(account__name__icontains=search)
                           | Q(account__code__icontains=search))

    return rows, state, zoho_type, search


@login_required
def zoho_mapping(request):
    """Point each Zoho account at an ERP account.

    Zoho has no account codes for this organisation, so every one of these
    arrived unmapped and names are the only signal. The screen is built around
    that: suggestions are computed for the rows actually on screen, the ones
    that can only mean one thing are offered as a single bulk action, and
    everything else is a decision a person makes with the candidates in front
    of them.

    Counts in the header are always over the whole table rather than the
    filtered page — "142 of 322 mapped" is the number somebody is actually
    tracking, and it must not move when they type in the search box.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    rows, state, zoho_type, search = _zoho_rows(request)

    paginator = Paginator(rows, ZOHO_PAGE_SIZE)
    page = paginator.get_page(request.GET.get('page'))

    # Suggestions are per-page on purpose: the close-match search is O(rows x
    # accounts) and computing it for all 322 on every keystroke would make the
    # page feel broken for no benefit, since only these are visible.
    postable = list(Account.objects.postable().order_by('code'))
    index = index_accounts(postable)

    # Attached to the row rather than handed over as a parallel dict: a
    # template cannot index a dict by a variable key without a custom filter,
    # and a whole templatetags module to look up something the row already
    # implies is not a trade worth making.
    for row in page.object_list:
        row.suggestion = suggest(row.zoho_account_name, index)

    everything = ZohoAccountMap.objects.all()
    unmapped_all = list(everything.unmapped())
    ready = certain_matches(unmapped_all, index)

    context = {
        'page': page,
        'rows': page.object_list,
        'accounts': postable,
        'state': state,
        'state_choices': sorted(ZOHO_STATE_FILTERS.items()),
        'selected_type': zoho_type,
        'type_choices': sorted(
            t for t in everything.values_list('zoho_account_type', flat=True).distinct() if t),
        'search': search,
        'total_count': everything.count(),
        'mapped_count': everything.mapped().count(),
        'unmapped_count': len(unmapped_all),
        'ignored_count': everything.filter(is_ignored=True).count(),
        'ready_count': len(ready),
        'querystring': urlencode({k: v for k, v in (
            ('state', state), ('type', zoho_type), ('q', search)) if v}),
    }
    return render(request, 'accounting/zoho_mapping.html', context)


@login_required
@require_POST
def zoho_mapping_save(request):
    """Save the mappings edited on one page of the worklist.

    Only touches rows whose select actually changed, so re-submitting a page
    after changing one line does not rewrite the note on forty others and bury
    the real history under noise.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    pks = request.POST.getlist('row')
    rows = {r.pk: r for r in ZohoAccountMap.objects.filter(pk__in=pks)}
    postable_ids = set(Account.objects.postable().values_list('pk', flat=True))

    changed = 0
    for pk in pks:
        row = rows.get(int(pk)) if str(pk).isdigit() else None
        if row is None:
            continue

        raw = (request.POST.get(f'account_{row.pk}') or '').strip()
        ignored = request.POST.get(f'ignore_{row.pk}') == 'on'

        account_id = None
        if raw.isdigit() and int(raw) in postable_ids:
            # Silently dropping a heading rather than erroring: the select only
            # offers postable accounts, so a value outside that set is a forged
            # or stale post, not a user mistake worth a message.
            account_id = int(raw)

        if row.account_id == account_id and row.is_ignored == ignored:
            continue

        row.account_id = account_id
        row.is_ignored = ignored and account_id is None
        row.note = _stamp(row.note, f'Mapped by {request.user.get_username()}')
        row.save(update_fields=['account', 'is_ignored', 'note', 'last_seen_at'])
        changed += 1

    if changed:
        messages.success(request, f'{changed} mapping(s) saved.')
    else:
        messages.info(request, 'Nothing changed.')
    return redirect(f"{reverse('accounting:zoho_mapping')}?{request.POST.get('back', '')}")


@login_required
@require_POST
def zoho_mapping_apply_certain(request):
    """Apply every unique exact name match in one go.

    Restricted to unmapped rows and to matches that can only mean one thing.
    A name that matches several ERP accounts is excluded here however obvious
    it looks — that is the case most likely to be wrong and least likely to be
    noticed. See accounting.mapping for why.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    index = index_accounts(Account.objects.postable())
    unmapped = list(ZohoAccountMap.objects.unmapped())
    matches = certain_matches(unmapped, index)

    rows = {r.pk: r for r in unmapped}
    applied = 0
    with transaction.atomic():
        for pk, account in matches.items():
            row = rows[pk]
            row.account = account
            row.note = _stamp(
                row.note,
                f'Auto-mapped on unique exact name match, confirmed by '
                f'{request.user.get_username()}')
            row.save(update_fields=['account', 'note', 'last_seen_at'])
            applied += 1

    if applied:
        messages.success(
            request,
            f'{applied} account(s) mapped on an exact name match. '
            f'Every one is reversible — they are marked in the notes.')
    else:
        messages.info(request, 'No unambiguous name matches left to apply.')
    return redirect(f"{reverse('accounting:zoho_mapping')}?{request.POST.get('back', '')}")


def _stamp(note, line):
    """Append an audit line, keeping whatever was already there."""
    stamped = f'{timezone.now():%Y-%m-%d %H:%M} — {line}'
    return f'{note}\n{stamped}'.strip() if note else stamped


# ── Chart of accounts import ────────────────────────────────────────────────

CHART_UPLOAD_SESSION_KEY = 'chart_import_pending'
CHART_UPLOAD_DIR = 'chart_imports'
MAX_CHART_UPLOAD_BYTES = 10 * 1024 * 1024


@login_required
def chart_import(request):
    """Upload a chart-of-accounts revision, see what it would change, then apply.

    Finance owns this chart and revises it — this is already the second
    revision — so publishing one should not need a developer or a shell. The
    preview step is the whole safety story: every ledger entry is coded against
    this structure, and the difference between "4 new accounts" and "200
    renamed" is the difference between a routine revision and a wrong file.

    Nothing is ever deleted. Accounts absent from a new revision can be
    deactivated, which keeps their postings and history intact.
    """
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    if request.method != 'POST':
        request.session.pop(CHART_UPLOAD_SESSION_KEY, None)
        return render(request, 'accounting/chart_import.html',
                      {'current_count': Account.objects.count(),
                       'active_count': Account.objects.active().count()})

    upload = request.FILES.get('workbook')
    if not upload:
        messages.error(request, 'Choose a workbook to upload.')
        return redirect('accounting:chart_import')

    if upload.size > MAX_CHART_UPLOAD_BYTES:
        messages.error(request, 'That file is larger than 10 MB — this chart is '
                                'a few hundred rows, so that is almost certainly '
                                'the wrong file.')
        return redirect('accounting:chart_import')

    payload = upload.read()
    try:
        sheet_name, rows = read_grid(BytesIO(payload),
                                     sheet_name=(request.POST.get('sheet') or '').strip() or None)
        parsed, duplicates, bad_types = parse_rows(rows)
    except ChartImportError as exc:
        messages.error(request, str(exc))
        return redirect('accounting:chart_import')

    if not parsed:
        messages.error(
            request,
            f'No account rows found in sheet {sheet_name!r}. The importer expects '
            f'the finance layout — codes in column D, names in E, internal type '
            f'in F, data starting at row 3.')
        return redirect('accounting:chart_import')

    # Held in storage rather than the session so the confirm step works on the
    # bytes that were actually previewed — re-uploading between the two steps
    # would let the preview describe one file and the apply run another.
    stored = default_storage.save(
        f'{CHART_UPLOAD_DIR}/{uuid4().hex}', ContentFile(payload))
    request.session[CHART_UPLOAD_SESSION_KEY] = {
        'path': stored,
        'sheet': sheet_name,
        'filename': upload.name,
    }

    return render(request, 'accounting/chart_import.html', {
        'plan': plan(parsed),
        'sheet_name': sheet_name,
        'filename': upload.name,
        'duplicates': duplicates,
        'bad_types': bad_types,
        'current_count': Account.objects.count(),
        'active_count': Account.objects.active().count(),
    })


@login_required
@require_POST
def chart_import_apply(request):
    """Apply the revision that was previewed."""
    if not _can_view_accounting(request.user):
        raise PermissionDenied

    pending = request.session.get(CHART_UPLOAD_SESSION_KEY)
    if not pending:
        messages.error(request, 'Nothing to apply — upload a workbook first.')
        return redirect('accounting:chart_import')

    try:
        with default_storage.open(pending['path'], 'rb') as handle:
            sheet_name, rows = read_grid(handle, sheet_name=pending.get('sheet'))
        parsed, _duplicates, _bad = parse_rows(rows)
    except (ChartImportError, FileNotFoundError, OSError) as exc:
        messages.error(request, f'Could not re-read the uploaded workbook: {exc}. '
                                f'Please upload it again.')
        request.session.pop(CHART_UPLOAD_SESSION_KEY, None)
        return redirect('accounting:chart_import')

    deactivate = request.POST.get('deactivate_missing') == 'on'
    with transaction.atomic():
        result = apply_chart(parsed, deactivate_missing=deactivate)

    # The upload has served its purpose; leaving copies of the chart lying in
    # storage is a slow leak nobody would think to look for.
    try:
        default_storage.delete(pending['path'])
    except (OSError, NotImplementedError):
        pass
    request.session.pop(CHART_UPLOAD_SESSION_KEY, None)

    parts = [f"{result['created']} added", f"{result['updated']} updated"]
    if result['deactivated']:
        parts.append(f"{result['deactivated']} deactivated")
    messages.success(request, f"Chart of accounts updated from {pending['filename']}: "
                              f"{', '.join(parts)}.")
    if result['orphans']:
        messages.warning(
            request,
            f"{len(result['orphans'])} account(s) had no parent in the file and sit "
            f"at the top level: {', '.join(result['orphans'][:8])}"
            + ('…' if len(result['orphans']) > 8 else ''))
    return redirect('accounting:chart')
