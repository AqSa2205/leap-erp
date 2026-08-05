from django.urls import path

from . import views

app_name = 'vendor_email'

urlpatterns = [
    path('', views.triage, name='triage'),
    path('resolve/<int:pk>/', views.resolve, name='resolve'),
    path('simulate/', views.simulate, name='simulate'),
    
]