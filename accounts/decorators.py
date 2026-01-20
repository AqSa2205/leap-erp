from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.core.exceptions import PermissionDenied


def role_required(allowed_roles):
    """
    Decorator to check if user has one of the allowed roles.
    Usage: @role_required(['admin', 'manager'])
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.error(request, 'Please login to access this page.')
                return redirect('accounts:login')

            if request.user.role and request.user.role.name in allowed_roles:
                return view_func(request, *args, **kwargs)

            messages.error(request, 'You do not have permission to access this page.')
            raise PermissionDenied
        return wrapper
    return decorator


def admin_required(view_func):
    """Decorator to require admin role"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('accounts:login')

        if request.user.is_admin_user:
            return view_func(request, *args, **kwargs)

        messages.error(request, 'Admin access required.')
        raise PermissionDenied
    return wrapper


def manager_or_admin_required(view_func):
    """Decorator to require manager or admin role"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Please login to access this page.')
            return redirect('accounts:login')

        if request.user.is_admin_user or request.user.is_manager_user:
            return view_func(request, *args, **kwargs)

        messages.error(request, 'Manager or Admin access required.')
        raise PermissionDenied
    return wrapper
