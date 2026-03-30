from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from .models import User, Role, PasswordResetRequest
from .forms import (
    CustomAuthenticationForm, CustomUserCreationForm,
    CustomUserChangeForm, UserProfileForm
)
from .decorators import admin_required
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse
import secrets
import threading


class CustomLoginView(LoginView):
    """Custom login view"""
    form_class = CustomAuthenticationForm
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True


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


class UserCreateView(AdminRequiredMixin, CreateView):
    """Create new user (admin only)"""
    model = User
    form_class = CustomUserCreationForm
    template_name = 'accounts/user_form.html'
    success_url = reverse_lazy('accounts:user_list')

    def form_valid(self, form):
        messages.success(self.request, 'User created successfully.')
        return super().form_valid(form)


class UserUpdateView(AdminRequiredMixin, UpdateView):
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
# PASSWORD RESET (Admin-controlled)
# ═══════════════════════════════════════════════════════════════

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

    # Send email in background
    subject = '[Leap ERP] Password Reset Request'
    body = (
        f'Hi {user.get_full_name() or user.username},\n\n'
        f'A password reset has been initiated for your Leap ERP account by the administrator.\n\n'
        f'Click the link below to set your new password:\n'
        f'{reset_url}\n\n'
        f'This link will expire in 7 days.\n\n'
        f'Your new password will take effect immediately after you submit it.\n\n'
        f'If you did not expect this, please contact your administrator.\n\n'
        f'— Leap ERP System'
    )

    def _send():
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
        except Exception:
            pass

    threading.Thread(target=_send).start()

    messages.success(request, f'Password reset link sent to {user.email}')
    return redirect('accounts:user_list')


@login_required
def send_reset_link_all(request):
    """Super admin sends reset links to ALL active users with email."""
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can do this.')
        return redirect('accounts:user_list')

    if request.method != 'POST':
        return redirect('accounts:user_list')

    users = User.objects.filter(is_active=True).exclude(email='').exclude(email__isnull=True)
    sent_count = 0

    for user in users:
        token = secrets.token_urlsafe(48)
        PasswordResetRequest.objects.create(
            user=user,
            token=token,
            status='pending_user',
            created_by=request.user,
        )

        reset_url = request.build_absolute_uri(f'/accounts/reset-password/{token}/')
        subject = '[Leap ERP] Password Reset Request'
        body = (
            f'Hi {user.get_full_name() or user.username},\n\n'
            f'A password reset has been initiated for your Leap ERP account by the administrator.\n\n'
            f'Click the link below to set your new password:\n'
            f'{reset_url}\n\n'
            f'This link will expire in 7 days.\n\n'
            f'Your new password will take effect immediately after you submit it.\n\n'
            f'— Leap ERP System'
        )

        def _send(email, subj, msg):
            try:
                send_mail(subj, msg, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
            except Exception:
                pass

        threading.Thread(target=_send, args=(user.email, subject, body)).start()
        sent_count += 1

    messages.success(request, f'Password reset links sent to {sent_count} users.')
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
    pending = requests.filter(status='pending_approval')
    return render(request, 'accounts/reset_requests.html', {
        'requests': requests,
        'pending_count': pending.count(),
    })


@login_required
def approve_reset(request, pk):
    """Super admin approves a password reset — actually changes the password."""
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can approve resets.')
        return redirect('accounts:reset_requests')

    reset_req = get_object_or_404(PasswordResetRequest, pk=pk)

    if reset_req.status != 'pending_approval':
        messages.error(request, 'This request is not pending approval.')
        return redirect('accounts:reset_requests')

    # Apply the password
    user = reset_req.user
    user.password = reset_req.new_password_hash
    user.save()

    reset_req.status = 'approved'
    reset_req.save()

    # Notify user
    if user.email:
        subject = '[Leap ERP] Password Reset Approved'
        body = (
            f'Hi {user.get_full_name() or user.username},\n\n'
            f'Your password reset has been approved. You can now login with your new password.\n\n'
            f'— Leap ERP System'
        )

        def _send():
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
            except Exception:
                pass

        threading.Thread(target=_send).start()

    messages.success(request, f'Password reset approved for {user.username}.')
    return redirect('accounts:reset_requests')


@login_required
def reject_reset(request, pk):
    """Super admin rejects a password reset."""
    if not request.user.is_super_admin_user:
        messages.error(request, 'Only super admins can reject resets.')
        return redirect('accounts:reset_requests')

    reset_req = get_object_or_404(PasswordResetRequest, pk=pk)
    reset_req.status = 'rejected'
    reset_req.save()

    messages.success(request, f'Password reset rejected for {reset_req.user.username}.')
    return redirect('accounts:reset_requests')
