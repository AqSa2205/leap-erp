from django.urls import path

from . import api

app_name = 'attendance'

urlpatterns = [
    path('checkin/', api.checkin, name='checkin'),
]
