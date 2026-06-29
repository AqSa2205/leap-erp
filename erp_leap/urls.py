"""
URL configuration for Leap Networks ERP project.
"""

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as static_serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('accounts/', include('accounts.urls')),
    path('projects/', include('projects.urls')),
    path('reports/', include('reports.urls')),
    path('database/', include('contacts.urls')),
    path('costing/', include('costing.urls')),
    path('notifications/', include('notifications.urls')),
    path('hr/', include('hr.urls')),
    path('manpower/', include('manpower.urls')),
    path('proposals/', include('proposals.urls')),
    path('procurement/', include('procurement.urls')),
    path('devtracking/', include('devtracking.urls')),
    path('kpis/', include('kpis.urls')),
    path('company/', include('company.urls')),
    path('finance/', include('finance.urls')),
]

# Whitenoise serves STATIC_URL in production but not MEDIA_URL, so route media
# through Django with a login gate. Uploaded docs are private (vendor quotes,
# contracts) — they should not be reachable without an authenticated session.
urlpatterns += [
    re_path(
        r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'),
        login_required(static_serve),
        {'document_root': settings.MEDIA_ROOT},
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
