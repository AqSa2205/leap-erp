from django.contrib import admin
from .models import DevTask, DevTaskUpdate, DevDigest, TaskStack


@admin.register(DevTask)
class DevTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'developer', 'status', 'priority', 'due_date', 'completed_at']
    list_filter = ['status', 'priority']
    search_fields = ['title', 'developer__username']


@admin.register(TaskStack)
class TaskStackAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'created_at']
    search_fields = ['name']


admin.site.register(DevTaskUpdate)
admin.site.register(DevDigest)
