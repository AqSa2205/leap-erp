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
    path('api/attendance/', include('attendance.urls')),
    path('attendance/', include('attendance.ui_urls')),
    path('drafts/', include('drafts.urls')),
]

# Whitenoise serves STATIC_URL in production but not MEDIA_URL, so route media
# through Django with a login gate. Uploaded docs are private (vendor quotes,
# contracts) — they should not be reachable without an authenticated session.
#
# When object storage (Cloudflare R2) is on, files live on R2, not local disk —
# serving from MEDIA_ROOT would 404 (e.g. proposal images wouldn't render). In
# that case redirect the /media/<path> URL to the storage backend's URL. Keeping
# the canonical /media/... URL means stored content (TinyMCE HTML, DOCX export)
# stays stable regardless of backend.
if getattr(settings, 'USE_R2', False):
    from django.core.files.storage import default_storage
    from django.http import Http404
    from django.shortcuts import redirect

    @login_required
    def _media_redirect(request, path):
        if not default_storage.exists(path):
            raise Http404('Media not found')
        return redirect(default_storage.url(path))

    media_view = _media_redirect
    media_kwargs = {}
else:
    media_view = login_required(static_serve)
    media_kwargs = {'document_root': settings.MEDIA_ROOT}

urlpatterns += [
    re_path(
        r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'),
        media_view,
        media_kwargs,
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
