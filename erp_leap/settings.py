"""
Django settings for erp_leap project.
Leap Networks Sales ERP System
"""

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Environment detection — production is auto-detected on Render even
# if DJANGO_ENV isn't set, so we don't fall through to insecure defaults.
ENVIRONMENT = os.environ.get('DJANGO_ENV', 'development')
IS_PRODUCTION = (
    ENVIRONMENT == 'production'
    or bool(os.environ.get('RENDER'))
    or bool(os.environ.get('RENDER_EXTERNAL_HOSTNAME'))
)


# Security settings — fail fast in production if SECRET_KEY is missing.
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured(
            'SECRET_KEY environment variable must be set in production. '
            'Refusing to start with an insecure fallback.'
        )
    # Local development only — never used in production due to the check above.
    SECRET_KEY = 'django-insecure-dev-only-do-not-use-in-production'

# DEBUG: defaults to False in production, True in local dev.
# Production is auto-detected (Render), so the dev default cannot leak.
_debug_default = 'False' if IS_PRODUCTION else 'True'
DEBUG = os.environ.get('DEBUG', _debug_default).lower() == 'true'
if IS_PRODUCTION:
    DEBUG = False  # Never allow DEBUG in production regardless of env var.

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Add Render host
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# For Render deployment - allow all .onrender.com hosts
if IS_PRODUCTION:
    ALLOWED_HOSTS.append('.onrender.com')
    CSRF_TRUSTED_ORIGINS = [
        'https://*.onrender.com',
    ]
    if RENDER_EXTERNAL_HOSTNAME:
        CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')


# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party apps
    "crispy_forms",
    "crispy_bootstrap5",
    "django_filters",
    "tinymce",
    # Local apps
    "accounts",
    "projects",
    "dashboard",
    "reports",
    "contacts",
    "costing",
    "notifications",
    "hr",
    "manpower",
    "proposals",
    "procurement",
    "devtracking",
    "kpis",
    "company",
    "finance",
    "attendance",
]

# ── Wi-Fi automatic attendance ────────────────────────────────────────────────
# Once-a-day model: the agent checks in at logon, and a single office
# connection during work hours marks the employee present for that day.
ATT_MAX_IDLE_SECONDS = 300      # idle longer than this → the check-in doesn't count
ATT_WORK_START = "06:00"        # local (Riyadh) work-window start (HH:MM)
ATT_WORK_END = "20:00"          # local work-window end
ATT_MIN_MINUTES_PRESENT = 1     # a single counted check-in = present for the day

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Serve static files in production
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "erp_leap.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "erp_leap.wsgi.application"


# Database configuration
# Uses DATABASE_URL in production (Render provides this automatically)
# Falls back to SQLite for local development
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
LANGUAGE_CODE = "en-us"
# Saudi Arabia (UTC+3). Datetimes are still stored in UTC (USE_TZ=True); this
# only controls how they're displayed, so timestamps read in local time instead
# of 3 hours behind.
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# File storage configuration.
# - Static: WhiteNoise in all envs (compressed manifest in production).
# - Default (uploads): Cloudflare R2 when USE_R2=True, else local filesystem.
#   R2 is required in production because Render's free disk is ephemeral and
#   user-uploaded documents would otherwise be wiped on every redeploy.
USE_R2 = os.environ.get('USE_R2', 'False').lower() == 'true'

if USE_R2:
    AWS_ACCESS_KEY_ID = os.environ['R2_ACCESS_KEY_ID']
    AWS_SECRET_ACCESS_KEY = os.environ['R2_SECRET_ACCESS_KEY']
    AWS_STORAGE_BUCKET_NAME = os.environ['R2_BUCKET_NAME']
    AWS_S3_ENDPOINT_URL = os.environ['R2_ENDPOINT_URL']
    AWS_S3_REGION_NAME = 'auto'
    AWS_S3_SIGNATURE_VERSION = 's3v4'
    AWS_S3_ADDRESSING_STYLE = 'virtual'
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = 3600
    _DEFAULT_FILE_STORAGE = 'storages.backends.s3.S3Storage'
else:
    _DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": _DEFAULT_FILE_STORAGE,
    },
}

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}


# Media files (uploads)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Raise Django's upload limits so PQD attachments (scanned PDFs, etc.)
# don't get rejected. Individual file size is enforced in the view.
DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024  # 200 MB request body
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB in memory, rest streams to disk
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000


# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Custom User Model
AUTH_USER_MODEL = "accounts.User"


# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# Login/Logout URLs
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"


# Email configuration (SendGrid SMTP)
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.sendgrid.net')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'apikey')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Leap ERP <notifications@leap-arabia.com>')


# AI digest (devtracking) — Anthropic-powered developer-progress reports.
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
DEVTRACKING_AI_MODEL = os.environ.get('DEVTRACKING_AI_MODEL', 'claude-sonnet-4-6')
# Supplier-quotation PDF extraction (procurement) — defaults to the digest model.
PROCUREMENT_AI_MODEL = os.environ.get('PROCUREMENT_AI_MODEL', 'claude-sonnet-4-6')

# GitHub PR status (devtracking) — used to fetch live PR state for code tasks.
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')


# Security settings for production
if IS_PRODUCTION:
    # Render terminates TLS at its proxy and forwards plain HTTP with an
    # X-Forwarded-Proto header. Without this, request.is_secure() is False, so
    # Django treats every request as insecure — which breaks CSRF on form POSTs
    # (e.g. login -> "CSRF verification failed") and loops SSL redirects.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
