from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'actor', 'verb', 'level', 'is_read', 'email_sent', 'created_at')
    list_filter = ('level', 'is_read', 'email_sent', 'created_at')
    search_fields = ('verb', 'description', 'recipient__username')
    raw_id_fields = ('recipient', 'actor')
    readonly_fields = ('created_at',)
