from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from .models import Notification


@login_required
def check_unread(request):
    """Return unread notification count for polling."""
    count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()
    return JsonResponse({'unread_count': count})


@login_required
def recent_notifications(request):
    """Return the last 15 notifications as JSON for the dropdown."""
    notifications = (
        Notification.objects
        .filter(recipient=request.user)
        .select_related('actor')
        .order_by('-created_at')[:15]
    )
    data = []
    for n in notifications:
        data.append({
            'id': n.pk,
            'actor': str(n.actor) if n.actor else 'System',
            'verb': n.verb,
            'description': n.description,
            'level': n.level,
            'target_url': n.target_url,
            'is_read': n.is_read,
            'created_at': n.created_at.strftime('%d %b %Y %H:%M'),
        })
    return JsonResponse({'notifications': data})


@login_required
@require_POST
def mark_read(request, pk):
    """Mark a single notification as read."""
    Notification.objects.filter(pk=pk, recipient=request.user).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def mark_all_read(request):
    """Mark all unread notifications as read for the current user."""
    Notification.objects.filter(
        recipient=request.user, is_read=False
    ).update(is_read=True)
    return JsonResponse({'ok': True})


class NotificationListView(LoginRequiredMixin, ListView):
    """Full paginated history page."""
    model = Notification
    template_name = 'notifications/notification_list.html'
    context_object_name = 'notifications'
    paginate_by = 30

    def get_queryset(self):
        return (
            Notification.objects
            .filter(recipient=self.request.user)
            .select_related('actor')
            .order_by('-created_at')
        )
