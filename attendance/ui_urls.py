from django.urls import path

from . import views

app_name = 'attendance_ui'

urlpatterns = [
    path('devices/', views.manage, name='attendance_devices'),
    path('devices/tokens.csv', views.tokens_export, name='attendance_tokens_export'),
]
