from django.urls import path
from . import views

app_name = 'drafts'

urlpatterns = [
    path('save/', views.save_draft, name='save'),
    path('check/', views.check_drafts, name='check'),
    path('<int:pk>/discard/', views.discard_draft, name='discard'),
]