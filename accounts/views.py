import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_POST
from django.db import transaction

from .models import User, Role, RolePermission, PasswordResetRequest, PermissionChangeLog
from accounts.permissions import capabilities_by_module, capability_codenames
from .forms import (
    CustomAuthenticationForm, CustomUserCreationForm,
    CustomUserChangeForm, UserProfileForm
)
from .decorators import admin_required
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse
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
    """Mixin to require super admin role for user management"""
    def test_func(self):
        return self.request.user.is_super_admin_user


class UserListView(AdminRequiredMixin, ListView):
    """List all users (admin only)"""
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        queryset = User.objects.select_related('role', 'region').all()
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                username__icontains=search
            ) | queryset.filter(
                email__icontains=search
            ) | queryset.filter(
                first_name__icontains=search
            ) | queryset.filter(
                last_name__icontains=search
            )
        return queryset


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

def _build_reset_email_html(user_name, reset_url):
    """Build a professional HTML email for password reset."""
    return f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- Header -->
    <tr>
        <td style="background:linear-gradient(135deg,#C41E3A,#a01830); padding:35px 40px; text-align:center;">
            <img src="https://leap-erp.onrender.com/static/images/leap_logo.jpg" alt="Leap Networks" style="max-width:180px; margin-bottom:15px;" />
            <h1 style="color:#ffffff; margin:0; font-size:22px; font-weight:700; letter-spacing:0.5px;">Password Reset</h1>
        </td>
    </tr>

    <!-- Body -->
    <tr>
        <td style="padding:40px;">
            <p style="color:#333; font-size:16px; margin:0 0 20px;">Hi <strong>{user_name}</strong>,</p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 25px;">
                A password reset has been initiated for your <strong>Leap Networks ERP</strong> account
                by the system administrator. Click the button below to set your new password.
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
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can send reset links.')
        return redirect('accounts:user_list')

    user = get_object_or_404(User, pk=pk)
    if not user.email:
        messages.error(request, f'{user.username} has no email address. Add one first.')
        return redirect('accounts:user_list')

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

    return redirect('accounts:user_list')


@login_required
def send_reset_link_all(request):
    """Super admin queues reset link emails for ALL active users with email.
    Emails are sent on background threads with proper error logging and
    DB connection cleanup."""
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can do this.')
        return redirect('accounts:user_list')

    if request.method != 'POST':
        return redirect('accounts:user_list')

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
    return redirect('accounts:user_list')


def reset_password_form(request, token):
    """User clicks link from email — sets new password (pending approval)."""
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

        if not password1 or len(password1) < 8:
            return render(request, 'accounts/reset_password.html', {
                'error': 'Password must be at least 8 characters.',
                'reset_req': reset_req,
                'token': token,
            })

        if password1 != password2:
            return render(request, 'accounts/reset_password.html', {
                'error': 'Passwords do not match.',
                'reset_req': reset_req,
                'token': token,
            })

        # Apply password immediately
        user = reset_req.user
        user.set_password(password1)
        user.save()

        reset_req.status = 'approved'
        reset_req.save()

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
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can view this.')
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
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can reject resets.')
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
