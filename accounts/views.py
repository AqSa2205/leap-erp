import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.password_validation import validate_password
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q

from .models import User, Role, RolePermission, PasswordResetRequest, PermissionChangeLog
from accounts.permissions import capabilities_by_module, capability_codenames
from .forms import (
    CustomAuthenticationForm, CustomUserCreationForm,
    CustomUserChangeForm, UserProfileForm
)
from .decorators import admin_required
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse, HttpResponse
import logging
import secrets

logger = logging.getLogger(__name__)


class CustomLoginView(LoginView):
    """Custom login view"""
    form_class = CustomAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        # Respect an explicit ?next=; otherwise send the user to a page they can
        # actually access (siloed roles like AI team can't open the dashboard).
        from accounts.permissions import landing_url_for
        return landing_url_for(self.request.user)


class CustomLogoutView(LogoutView):
    """Custom logout view"""
    next_page = 'accounts:login'


@login_required
def profile_view(request):
    """User profile view"""
    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('accounts:profile')
    else:
        form = UserProfileForm(instance=request.user)

    return render(request, 'accounts/profile.html', {'form': form})


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """User management — super_admin only.

    erp_admin owns the rest of the Administration section but deliberately not
    this or the permission grid. Holding both would let that role grant itself
    anything, which would make erp_admin indistinguishable from super_admin
    and leave no tier that can actually restrain it.
    """
    def test_func(self):
        return self.request.user.is_super_admin_user


def filter_users(request):
    """Users matching the list page's search + region filters. Shared by the
    list view and the PDF export so both always agree on what's shown."""
    queryset = User.objects.select_related('role', 'region').all()
    search = request.GET.get('search')
    if search:
        queryset = queryset.filter(
            Q(username__icontains=search) | Q(email__icontains=search) |
            Q(first_name__icontains=search) | Q(last_name__icontains=search))
    region = request.GET.get('region')
    if region:
        queryset = queryset.filter(region__code=region)
    return queryset


class UserListView(AdminRequiredMixin, ListView):
    """List all users (admin only)"""
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        return filter_users(self.request)

    def get_context_data(self, **kwargs):
        from projects.models import Region
        ctx = super().get_context_data(**kwargs)
        ctx['regions'] = Region.objects.filter(is_active=True).order_by('name')
        ctx['selected_region'] = self.request.GET.get('region', '')
        return ctx


@login_required
def user_export_pdf(request):
    """Export the (optionally region-filtered) user list to PDF — username,
    full name, email, role and region, with a per-region count. Super admin only."""
    if not request.user.is_super_admin_user:
        messages.error(request, 'Admin access required.')
        return redirect('accounts:reset_requests')

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from django.utils import timezone
    from collections import Counter
    from projects.models import Region
    import io

    users = list(filter_users(request).order_by('region__name', 'username'))

    region_code = request.GET.get('region') or ''
    region_obj = Region.objects.filter(code=region_code).first() if region_code else None
    scope = str(region_obj) if region_obj else 'All Regions'

    # Per-region counts — answers "how many users from each region".
    counts = Counter((u.region.code if u.region else '-') for u in users)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm)
    styles = getSampleStyleSheet()
    cell = ParagraphStyle('cell', fontSize=8, leading=10)
    head = ParagraphStyle('head', fontSize=8, leading=10, textColor=colors.white, fontName='Helvetica-Bold')

    elements = [Paragraph(f'User Register - {scope}', styles['Title'])]
    summary = f'Total users: {len(users)}'
    if not region_obj:
        breakdown = ', '.join(f'{code}: {n}' for code, n in sorted(counts.items()))
        if breakdown:
            summary += f'  |  By region - {breakdown}'
    elements.append(Paragraph(summary, styles['Normal']))
    elements.append(Paragraph(f'Generated {timezone.localtime():%d %b %Y %H:%M}', styles['Normal']))
    elements.append(Spacer(1, 10))

    data = [[Paragraph(h, head) for h in ['#', 'Username', 'Full Name', 'Email', 'Role', 'Region']]]
    for i, u in enumerate(users, 1):
        data.append([
            Paragraph(str(i), cell),
            Paragraph(u.username, cell),
            Paragraph(u.get_full_name() or '-', cell),
            Paragraph(u.email or '-', cell),
            Paragraph(str(u.role) if u.role else 'Not assigned', cell),
            Paragraph(u.region.code if u.region else '-', cell),
        ])

    table = Table(data, repeatRows=1, colWidths=[22, 78, 100, 140, 100, 50])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F4E79')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F5F5')]),
    ]))
    elements.append(table)
    if not users:
        elements.append(Paragraph('No users match this filter.', styles['Normal']))
    doc.build(elements)

    buffer.seek(0)
    slug = (region_obj.code if region_obj else 'all').lower()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="users_{slug}.pdf"'
    return response


class _RoleAccessContextMixin:
    """Adds a role -> department-access reference to the user form, so admins
    can see what each role grants when they pick one."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from accounts.permissions import default_modules_by_role
        labels = dict(Role.ROLE_CHOICES)
        ctx['role_access'] = [
            {'label': labels.get(name, name), 'modules': mods}
            for name, mods in sorted(
                default_modules_by_role().items(),
                key=lambda kv: labels.get(kv[0], kv[0]))
            if mods  # skip roles with no module access (e.g. AI doers)
        ]
        return ctx


class UserCreateView(_RoleAccessContextMixin, AdminRequiredMixin, CreateView):
    """Create new user (admin only)"""
    model = User
    form_class = CustomUserCreationForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('accounts:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User created successfully.')
        return super().form_valid(form)


class UserUpdateView(_RoleAccessContextMixin, AdminRequiredMixin, UpdateView):
    """Update user (admin only)"""
    model = User
    form_class = CustomUserChangeForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('accounts:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User updated successfully.')
        return super().form_valid(form)


class UserDeleteView(AdminRequiredMixin, DeleteView):
    """Delete user (admin only)"""
    model = User
    template_name = 'accounts/user_confirm_delete.html'
    success_url = reverse_lazy('accounts:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User deleted successfully.')
        return super().form_valid(form)


@login_required
def fix_admin_role(request):
    """One-time: set admin user to super_admin role."""
    if request.user.username != 'admin':
        return JsonResponse({'error': 'admin only'}, status=403)
    super_admin = Role.objects.get(name='super_admin')
    request.user.role = super_admin
    request.user.save()
    return JsonResponse({'status': 'done', 'role': 'Super Administrator'})


# ═══════════════════════════════════════════════════════════════
# PASSWORD RESET
# ═══════════════════════════════════════════════════════════════

def _build_reset_email_html(user_name, reset_url, self_requested=False):
    """Build a professional HTML email for password reset. self_requested
    distinguishes the self-service 'Forgot Password?' flow from an admin
    sending a reset link on the user's behalf — same template, one line of
    copy adjusted so the email doesn't falsely imply an admin acted."""
    initiator_text = (
        'You requested a password reset for your <strong>Leap Networks ERP</strong> account.'
        if self_requested else
        'A password reset has been initiated for your <strong>Leap Networks ERP</strong> '
        'account by the system administrator.'
    )
    return f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- Header -->
    <tr>
        <!-- bgcolor is the Outlook fallback: it renders mail through Word,
             which ignores CSS gradients, and without it this header loses its
             background entirely and the white heading below becomes invisible.
             Clients that do support the gradient paint over the flat colour. -->
        <td bgcolor="#C41E3A" style="background:linear-gradient(135deg,#C41E3A,#a01830); padding:35px 40px; text-align:center;">
            <img src="https://leap-erp.onrender.com/static/images/leap_logo.jpg" alt="Leap Networks" style="max-width:180px; margin-bottom:15px;" />
            <h1 style="color:#ffffff; margin:0; font-size:22px; font-weight:700; letter-spacing:0.5px;">Password Reset</h1>
        </td>
    </tr>

    <!-- Body -->
    <tr>
        <td style="padding:40px;">
            <p style="color:#333; font-size:16px; margin:0 0 20px;">Hi <strong>{user_name}</strong>,</p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 25px;">
                {initiator_text} Click the button below to set your new password.
            </p>

            <!-- CTA Button -->
            <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td align="center" style="padding:10px 0 30px;">
                <a href="{reset_url}" style="display:inline-block; background:#C41E3A; color:#ffffff; text-decoration:none; padding:14px 40px; border-radius:8px; font-size:16px; font-weight:700; letter-spacing:0.5px;">
                    Reset My Password
                </a>
            </td></tr>
            </table>

            <p style="color:#888; font-size:12px; line-height:1.6; margin:0 0 15px;">
                If the button doesn't work, copy and paste this link into your browser:
            </p>
            <p style="color:#C41E3A; font-size:12px; word-break:break-all; background:#f9f9f9; padding:12px; border-radius:6px; border:1px solid #eee; margin:0 0 25px;">
                {reset_url}
            </p>

            <!-- Info Box -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa; border-radius:8px; border-left:4px solid #C41E3A;">
            <tr><td style="padding:15px 20px;">
                <p style="color:#555; font-size:13px; margin:0; line-height:1.6;">
                    <strong>Please note:</strong><br>
                    &#8226; This link will expire in <strong>7 days</strong><br>
                    &#8226; Your new password takes effect <strong>immediately</strong><br>
                    &#8226; Password must be at least <strong>8 characters</strong>
                </p>
            </td></tr>
            </table>

            <p style="color:#999; font-size:12px; margin:25px 0 0;">
                If you did not request this reset, you can safely ignore this email. Your current password will remain unchanged.
            </p>
        </td>
    </tr>

    <!-- Footer -->
    <tr>
        <td style="background:#2a2a2a; padding:25px 40px; text-align:center;">
            <p style="color:#999; font-size:12px; margin:0 0 5px;">
                <strong style="color:#ccc;">Leap Networks</strong> &mdash; ERP System
            </p>
            <p style="color:#666; font-size:11px; margin:0;">
                This is an automated message. Please do not reply directly to this email.
            </p>
        </td>
    </tr>

</table>
</td></tr>
</table>
</body>
</html>'''

@login_required
def send_reset_link(request, pk):
    """Super admin generates a reset link and emails it to the user."""
    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
        messages.error(request, 'Administration access required.')
        return redirect('accounts:reset_requests')

    user = get_object_or_404(User, pk=pk)
    if not user.email:
        messages.error(request, f'{user.username} has no email address. Add one first.')
        return redirect('accounts:reset_requests')

    # Generate token
    token = secrets.token_urlsafe(48)
    PasswordResetRequest.objects.create(
        user=user,
        token=token,
        status='pending_user',
        created_by=request.user,
    )

    # Build reset URL
    reset_url = request.build_absolute_uri(f'/accounts/reset-password/{token}/')

    # Send email
    subject = 'Password Reset — Leap Networks ERP'
    user_name = user.get_full_name() or user.username
    html_body = _build_reset_email_html(user_name, reset_url)
    plain_body = (
        f'Hi {user_name},\n\n'
        f'A password reset has been initiated for your Leap Networks ERP account.\n\n'
        f'Reset your password: {reset_url}\n\n'
        f'This link expires in 7 days. Your new password takes effect immediately.\n\n'
        f'If you did not request this, please ignore this email.\n\n'
        f'Leap Networks ERP System'
    )

    try:
        from django.core.mail import EmailMultiAlternatives
        email = EmailMultiAlternatives(subject, plain_body, settings.DEFAULT_FROM_EMAIL, [user.email])
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=False)
        messages.success(request, f'Password reset link sent to {user.email}')
    except Exception as e:
        messages.error(request, f'Failed to send email: {e}')

    return redirect('accounts:reset_requests')


@login_required
def send_reset_link_all(request):
    """Super admin queues reset link emails for ALL active users with email.
    Emails are sent on background threads with proper error logging and
    DB connection cleanup."""
    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
        messages.error(request, 'Administration access required.')
        return redirect('accounts:reset_requests')

    if request.method != 'POST':
        return redirect('accounts:reset_requests')

    from notifications.services import send_email_in_background

    users = User.objects.filter(is_active=True).exclude(email='').exclude(email__isnull=True)
    queued = 0

    for user in users:
        token = secrets.token_urlsafe(48)
        PasswordResetRequest.objects.create(
            user=user,
            token=token,
            status='pending_user',
            created_by=request.user,
        )

        reset_url = request.build_absolute_uri(f'/accounts/reset-password/{token}/')
        user_name = user.get_full_name() or user.username
        subject = 'Password Reset — Leap Networks ERP'
        html_body = _build_reset_email_html(user_name, reset_url)
        plain_body = (
            f'Hi {user_name},\n\n'
            f'Reset your password: {reset_url}\n\n'
            f'This link expires in 7 days.\n\n'
            f'— Leap Networks ERP'
        )

        send_email_in_background(
            subject=subject,
            body=plain_body,
            to_email=user.email,
            html_body=html_body,
        )
        queued += 1

    messages.success(
        request,
        f'Queued password reset emails for {queued} user(s). Delivery happens in the background.'
    )
    return redirect('accounts:reset_requests')


def forgot_password(request):
    """Self-service entry point: an unauthenticated user requests a reset
    link by email. The response is always the same neutral message whether
    or not the email matches an active account with an email on file —
    revealing that difference would let an outsider enumerate valid
    usernames/emails. Reuses the same PasswordResetRequest + token-consuming
    reset_password_form flow as the admin-triggered send_reset_link, just
    with created_by left null (nobody sent it on the user's behalf)."""
    from .forms import ForgotPasswordForm

    form = ForgotPasswordForm(request.POST or None)
    submitted = False

    if request.method == 'POST' and form.is_valid():
        submitted = True
        email = form.cleaned_data['email']
        user = User.objects.filter(email__iexact=email, is_active=True).exclude(email='').first()
        if user:
            token = secrets.token_urlsafe(48)
            PasswordResetRequest.objects.create(user=user, token=token, status='pending_user')

            reset_url = request.build_absolute_uri(f'/accounts/reset-password/{token}/')
            user_name = user.get_full_name() or user.username
            subject = 'Password Reset — Leap Networks ERP'
            html_body = _build_reset_email_html(user_name, reset_url, self_requested=True)
            plain_body = (
                f'Hi {user_name},\n\n'
                f'You requested a password reset for your Leap Networks ERP account.\n\n'
                f'Reset your password: {reset_url}\n\n'
                f'This link expires in 7 days. Your new password takes effect immediately.\n\n'
                f'If you did not request this, please ignore this email.\n\n'
                f'Leap Networks ERP System'
            )
            try:
                from django.core.mail import EmailMultiAlternatives
                mail = EmailMultiAlternatives(subject, plain_body, settings.DEFAULT_FROM_EMAIL, [user.email])
                mail.attach_alternative(html_body, 'text/html')
                mail.send(fail_silently=False)
            except Exception:
                logger.exception('Failed to send self-service password reset email for user id %s', user.pk)

    return render(request, 'accounts/forgot_password.html', {'form': form, 'submitted': submitted})


def reset_password_form(request, token):
    """User clicks link from email — sets new password immediately."""
    reset_req = get_object_or_404(PasswordResetRequest, token=token)

    if reset_req.status not in ('pending_user',):
        return render(request, 'accounts/reset_password.html', {
            'error': 'This reset link has already been used or expired.',
            'reset_req': reset_req,
        })

    if reset_req.is_expired:
        reset_req.status = 'expired'
        reset_req.save()
        return render(request, 'accounts/reset_password.html', {
            'error': 'This reset link has expired. Please contact your administrator.',
            'reset_req': reset_req,
        })

    if request.method == 'POST':
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        if password1 != password2:
            return render(request, 'accounts/reset_password.html', {
                'error': 'Passwords do not match.',
                'reset_req': reset_req,
                'token': token,
            })

        try:
            validate_password(password1, user=reset_req.user)
        except ValidationError as exc:
            return render(request, 'accounts/reset_password.html', {
                'error': ' '.join(exc.messages),
                'reset_req': reset_req,
                'token': token,
            })

        with transaction.atomic():
            # Re-fetch under a row lock and re-check status inside the
            # transaction — closes the window where two concurrent
            # submissions of the same link could otherwise both "succeed".
            reset_req = PasswordResetRequest.objects.select_for_update().get(pk=reset_req.pk)
            if reset_req.status != 'pending_user':
                return render(request, 'accounts/reset_password.html', {
                    'error': 'This reset link has already been used or expired.',
                    'reset_req': reset_req,
                })

            user = reset_req.user
            user.set_password(password1)
            user.save()

            reset_req.status = 'approved'
            reset_req.save()

            # A user may have several unused reset links outstanding (e.g. an
            # admin re-sent one, or "forgot password" was used twice). Once
            # any one of them is consumed, the rest must stop working too.
            PasswordResetRequest.objects.filter(
                user=user, status='pending_user',
            ).exclude(pk=reset_req.pk).update(status='expired')

        return render(request, 'accounts/reset_password.html', {
            'success': True,
            'reset_req': reset_req,
        })

    return render(request, 'accounts/reset_password.html', {
        'reset_req': reset_req,
        'token': token,
    })


@login_required
def reset_requests_list(request):
    """Super admin views all pending reset requests."""
    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
        messages.error(request, 'Administration access required.')
        return redirect('dashboard:index')

    requests = PasswordResetRequest.objects.select_related('user', 'created_by').all()
    pending = requests.filter(status='pending_user')
    return render(request, 'accounts/reset_requests.html', {
        'requests': requests,
        'pending_count': pending.count(),
    })


@login_required
def reject_reset(request, pk):
    """Super admin cancels a pending reset request (before the user uses it)."""
    if not (request.user.is_super_admin_user or request.user.is_erp_admin_user):
        messages.error(request, 'Administration access required.')
        return redirect('accounts:reset_requests')

    reset_req = get_object_or_404(PasswordResetRequest, pk=pk)
    if reset_req.status != 'pending_user':
        messages.error(request, 'Only pending requests can be cancelled.')
        return redirect('accounts:reset_requests')
    reset_req.status = 'rejected'
    reset_req.save()

    messages.success(request, f'Password reset rejected for {reset_req.user.username}.')
    return redirect('accounts:reset_requests')


@login_required
def permission_matrix(request):
    """Super-admin-only grid of role x capability toggles.

    Hardcoded super_admin gate (NOT capability-gated) so the page can never be
    toggled away or used to lock everyone out.
    """
    if not request.user.is_super_admin_user:
        raise PermissionDenied

    roles = list(Role.objects.all())
    grant_map = {
        (g.role_id, g.codename): g.allowed
        for g in RolePermission.objects.all()
    }
    modules = []
    for module_label, caps in capabilities_by_module().items():
        rows = []
        for cap in caps:
            cells = [{
                'role': role,
                'allowed': grant_map.get((role.id, cap.codename), False),
                'locked': role.name == Role.SUPER_ADMIN,
            } for role in roles]
            rows.append({'cap': cap, 'cells': cells})
        modules.append({'label': module_label, 'rows': rows})

    return render(request, 'accounts/permission_matrix.html', {
        'roles': roles,
        'modules': modules,
    })


@login_required
@require_POST
def ajax_toggle_permission(request):
    if not request.user.is_super_admin_user:
        raise PermissionDenied
    try:
        payload = json.loads(request.body or '{}')
        role_id = int(payload['role'])
        codename = str(payload['codename'])
        allowed = bool(payload['allowed'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    if codename not in capability_codenames():
        return JsonResponse({'error': 'Unknown capability'}, status=400)

    role = Role.objects.filter(pk=role_id).first()
    if role is None:
        return JsonResponse({'error': 'Unknown role'}, status=400)
    if role.name == Role.SUPER_ADMIN:
        return JsonResponse({'error': 'Super Admin permissions are fixed'}, status=400)

    # One transaction so a grant change can never persist without its audit row.
    with transaction.atomic():
        RolePermission.objects.update_or_create(
            role=role, codename=codename, defaults={'allowed': allowed},
        )
        PermissionChangeLog.objects.create(
            actor=request.user, role=role, codename=codename, allowed=allowed,
        )
    return JsonResponse({'ok': True})


# Roles are split across stacked tables rather than crowded into one: twenty
# columns on a landscape page leaves each about 10mm, too narrow for a header
# anybody can read. Twelve and eight give the names room to sit above their
# columns, and the split follows how people already group the roles.
PERMISSION_PDF_GROUPS = [
    ('Commercial, finance and administration', [
        Role.SUPER_ADMIN, Role.ADMIN, Role.ERP_ADMIN, Role.MANAGER,
        Role.SALES_REP, Role.PROPOSAL_HEAD, Role.PROPOSAL_REP,
        Role.FINANCE_HEAD, Role.FINANCE_MANAGER, Role.FINANCE_REP,
        Role.PROCUREMENT_MGR, Role.PROCUREMENT_OFF,
    ]),
    ('Delivery, HR-scoped and technical', [
        Role.PROJECT_MANAGER, Role.SITE_MANAGER, Role.DOCUMENT_CONTROLLER,
        Role.DEVELOPER, Role.AI_HEAD, Role.AI_ENGINEER,
        Role.AI_JUNIOR_ENGINEER, Role.AI_INTERN,
    ]),
]


# Column headers for the export only. The twelve-role table gives each column
# about 17mm, which holds roughly twelve characters at header size — long
# enough for "Procurement" on its own line, not for "Representative", which
# ReportLab breaks mid-word into something unreadable. These are the standard
# short forms people already use, so nothing needs a legend to decode.
PERMISSION_PDF_ROLE_HEADERS = {
    Role.SUPER_ADMIN: 'Super Admin',
    Role.ADMIN: 'Admin',
    Role.SALES_REP: 'Sales Rep',
    Role.PROPOSAL_REP: 'Proposal Rep',
    Role.FINANCE_REP: 'Finance Rep',
    Role.AI_JUNIOR_ENGINEER: 'Junior AI Eng',
}


@login_required
def permission_matrix_pdf(request):
    """Export the permission grid exactly as it stands right now.

    Same hardcoded super_admin gate as the page it exports. A document setting
    out who can do what is as sensitive as the screen it came from, so gating
    the export any more loosely would hand out the whole access model.

    The ticks come from the live grants, not from the seeded defaults, which is
    the point of exporting: something datable to file, review, or hand to an
    auditor.

    A role listed in PERMISSION_PDF_GROUPS but missing from the database is
    skipped rather than crashing the export, and any role in the database that
    is not listed is appended to the last group — so a role added later still
    appears, instead of silently dropping out of the document that is supposed
    to be the complete picture.
    """
    if not request.user.is_super_admin_user:
        raise PermissionDenied

    from io import BytesIO

    from django.utils import timezone
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    roles_by_name = {r.name: r for r in Role.objects.all()}
    grant_map = {
        (g.role_id, g.codename): g.allowed
        for g in RolePermission.objects.all()
    }

    groups = [(label, [roles_by_name[n] for n in names if n in roles_by_name])
              for label, names in PERMISSION_PDF_GROUPS]
    listed = {r.name for _label, roles in groups for r in roles}
    unlisted = [r for name, r in roles_by_name.items() if name not in listed]
    if unlisted and groups:
        groups[-1][1].extend(unlisted)

    base = getSampleStyleSheet()
    st_title = ParagraphStyle('t', parent=base['Title'], fontName='Helvetica-Bold',
                              fontSize=17, leading=21, alignment=0,
                              textColor=colors.HexColor('#1A1A1A'))
    st_meta = ParagraphStyle('m', parent=base['Normal'], fontName='Helvetica',
                             fontSize=8.5, leading=12,
                             textColor=colors.HexColor('#6C757D'))
    st_group = ParagraphStyle('g', parent=base['Normal'], fontName='Helvetica-Bold',
                              fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=4,
                              textColor=colors.HexColor('#C41E3A'))
    st_mod = ParagraphStyle('mod', parent=base['Normal'], fontName='Helvetica-Bold',
                            fontSize=7.5, leading=9.5,
                            textColor=colors.HexColor('#1A1A1A'))
    st_cap = ParagraphStyle('cap', parent=base['Normal'], fontName='Helvetica',
                            fontSize=7, leading=9)
    st_head = ParagraphStyle('h', parent=base['Normal'], fontName='Helvetica-Bold',
                             fontSize=6.2, leading=7.6, alignment=1)
    st_note = ParagraphStyle('n', parent=base['Normal'], fontName='Helvetica-Oblique',
                             fontSize=7.5, leading=10,
                             textColor=colors.HexColor('#6C757D'))

    buf = BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(
        buf, pagesize=page, topMargin=13 * mm, bottomMargin=13 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
        title='Leap ERP - Role Permissions')
    content_w = page[0] - 24 * mm

    exported_by = request.user.get_full_name() or request.user.username
    story = [
        Paragraph('Role Permissions', st_title),
        Spacer(1, 2 * mm),
        Paragraph('Leap Networks ERP &middot; exported {0:%d %B %Y, %H:%M} by {1}'.format(
            timezone.localtime(), exported_by), st_meta),
        Spacer(1, 1.5 * mm),
        Paragraph(
            'A tick means the role holds that capability. Super Admin is always allowed and '
            'cannot be switched off. Capabilities marked <b>(pending)</b> are stored in the '
            'grid but not yet enforced in code &mdash; switching one off does not restrict '
            'anything yet.', st_note),
        Spacer(1, 4 * mm),
    ]

    modules = capabilities_by_module()

    for index, (group_label, group_roles) in enumerate(groups):
        if not group_roles:
            continue
        if index:
            story.append(PageBreak())
        story.append(Paragraph(group_label, st_group))

        label_w = 62 * mm
        col_w = (content_w - label_w) / len(group_roles)

        rows = [[Paragraph('Capability', st_head)]
                + [Paragraph(
                    PERMISSION_PDF_ROLE_HEADERS.get(
                        r.name, r.get_name_display()).replace(' ', '<br/>'), st_head)
                   for r in group_roles]]
        style = [
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#D8D8DE')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F0F0F3')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 2.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('FONTSIZE', (1, 1), (-1, -1), 8),
        ]

        for module_label, caps in modules.items():
            # A module header spanning the row, so the export reads in the same
            # blocks as the screen it came from.
            header_at = len(rows)
            rows.append([Paragraph(module_label, st_mod)] + [''] * len(group_roles))
            style.append(('SPAN', (0, header_at), (-1, header_at)))
            style.append(('BACKGROUND', (0, header_at), (-1, header_at),
                          colors.HexColor('#E8E8EE')))

            for cap in caps:
                at = len(rows)
                label = cap.label if cap.enforced else cap.label + ' (pending)'
                row = [Paragraph(label, st_cap)]
                for column, role in enumerate(group_roles, start=1):
                    # Super admin bypasses the grid in code, so it is shown as
                    # held regardless of what its stored row happens to say.
                    locked = role.name == Role.SUPER_ADMIN
                    allowed = locked or grant_map.get((role.id, cap.codename), False)
                    row.append('Y' if allowed else '.')
                    style.append((
                        'TEXTCOLOR', (column, at), (column, at),
                        colors.HexColor('#1B6B45' if allowed else '#B9B4B8')))
                if not cap.enforced:
                    style.append(('TEXTCOLOR', (0, at), (0, at),
                                  colors.HexColor('#8A5A00')))
                rows.append(row)

        table = Table(rows, colWidths=[label_w] + [col_w] * len(group_roles),
                      repeatRows=1)
        table.setStyle(TableStyle(style))
        story.append(table)

    doc.build(story)
    buf.seek(0)
    filename = 'leap-erp-permissions-{0:%Y-%m-%d}.pdf'.format(timezone.localtime())
    response = HttpResponse(buf.read(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="{0}"'.format(filename)
    return response
