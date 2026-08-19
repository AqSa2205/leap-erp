from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Account, build_tree, subtree_counts


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
