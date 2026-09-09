from django.contrib import admin

from .models import CommunicationMatrix, ResponsibilityMatrix


@admin.register(ResponsibilityMatrix)
class ResponsibilityMatrixAdmin(admin.ModelAdmin):
    list_display = ('project', 'updated_by', 'updated_at')
    search_fields = ('project__project_name', 'project__serial_number')


@admin.register(CommunicationMatrix)
class CommunicationMatrixAdmin(admin.ModelAdmin):
    list_display = ('project', 'updated_by', 'updated_at')
    search_fields = ('project__project_name', 'project__serial_number')
