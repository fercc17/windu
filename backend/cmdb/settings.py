"""
Django settings for IS-CMDB project.
"""
import os
import sys
from pathlib import Path
import environ

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent

# Vendored reusable logic from the folded-in apps (pure domain modules reused by
# the Standup board: standup_dashboard.domain.{models,coloring,roles} etc.)
_LIBS_DIR = BASE_DIR.parent / 'libs'
if _LIBS_DIR.is_dir() and str(_LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(_LIBS_DIR))

# Environment variables
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ['localhost', '127.0.0.1', '0.0.0.0', '10.45.156.196']),
)

# Read .env file if exists
env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(env_file)

# Dev-only consolidated tokens (repo-root .env.dev; gitignored). The real
# environment injects these via Juju secrets instead. Loaded after .env so app/DB
# config there wins; .env.dev only supplies the integration tokens.
dev_secrets = BASE_DIR.parent / '.env.dev'  # repo root: windu/.env.dev
if dev_secrets.exists():
    environ.Env.read_env(str(dev_secrets))

# Security
SECRET_KEY = env('SECRET_KEY', default='dev-insecure-key-change-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env('ALLOWED_HOSTS')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'rest_framework',
    'django_tables2',
    'django_filters',
    'drf_spectacular',
    'cmdb.apps.environments',
    'cmdb.apps.netbox',
    'cmdb.apps.maintenance',
    'cmdb.apps.storage',
    'cmdb.apps.dora',
    'cmdb.apps.changes',
    'cmdb.apps.api',
    # Unified domains merged from jira-analysis + standup-dashboard
    'cmdb.apps.jira',
    'cmdb.apps.pagerduty',
    'cmdb.apps.standup',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'cmdb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'cmdb' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            # Make CMDB formatting filters (e.g. `utclocal`) available in every
            # template without a per-file {% load cmdb_format %}.
            'builtins': ['cmdb.apps.environments.templatetags.cmdb_format'],
        },
    },
]

WSGI_APPLICATION = 'cmdb.wsgi.application'

# Database
DATABASES = {
    'default': env.db('DATABASE_URL', default='postgresql://cmdb:cmdb@localhost:5432/cmdb')
}

# Redis
REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']  # vendored Pragma tokens/fonts live here

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# DRF Spectacular (OpenAPI schema)
SPECTACULAR_SETTINGS = {
    'TITLE': 'IS-CMDB API',
    'DESCRIPTION': 'Configuration Management Database for Canonical IS infrastructure',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# django-tables2
DJANGO_TABLES2_TEMPLATE = 'django_tables2/bootstrap4.html'

# Map Django message levels onto Bootstrap alert variants (ERROR -> danger, so
# validation errors render in red rather than the unstyled 'alert-error').
from django.contrib.messages import constants as _message_constants  # noqa: E402
MESSAGE_TAGS = {
    _message_constants.DEBUG: 'secondary',
    _message_constants.ERROR: 'danger',
}

# is-infrastructure (canonical/infrastructure-services) source for the CIA-fallback
# "who edited this file" lookup. INFRA_REPO_PATH points at a local clone; defaults
# to the vendored checkout at the repo root.
INFRA_REPO_PATH = env('INFRA_REPO_PATH', default=str(BASE_DIR / 'infrastructure-services'))
INFRA_GITHUB_REPO = env('INFRA_GITHUB_REPO', default='canonical/infrastructure-services')

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'cmdb': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}
