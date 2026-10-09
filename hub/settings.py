"""Settings come from the environment ox provides. Under ox (OX_ENV set)
DJANGO_SECRET_KEY is required; locally a random key is made per process."""

import os
import secrets
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
UNDER_OX = bool(os.environ.get("OX_ENV"))

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if UNDER_OX:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is not set; set it with ox vars before deploying")
    SECRET_KEY = secrets.token_urlsafe(50)

DEBUG = False
ALLOWED_HOSTS = [h for h in (os.environ.get("PUBLIC_HOST", ""), "127.0.0.1", "localhost") if h]
CSRF_TRUSTED_ORIGINS = [u for u in (os.environ.get("PUBLIC_URL", ""),) if u]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_COOKIE_SECURE = UNDER_OX
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024

INSTALLED_APPS = ["django.contrib.staticfiles", "river", "ledger"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "hub.cors.ZooCors",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "hub.urls"
WSGI_APPLICATION = "hub.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True, "OPTIONS": {}}]


def database(var: str) -> dict:
    # Unset stays unusable instead of guessing a database: queries fail and
    # the probe reports the variable as not set.
    url = os.environ.get(var)
    if not url:
        return {"ENGINE": "django.db.backends.dummy"}
    db = dj_database_url.parse(url, conn_max_age=60, conn_health_checks=True)
    db["OPTIONS"] = {"connect_timeout": 5, "options": "-c statement_timeout=5000"}
    return db


DATABASES = {"default": database("DATABASE_URL"), "ledger": database("LEDGER_DATABASE_URL")}
DATABASE_ROUTERS = ["hub.routers.LedgerRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Celery reads CELERY_* from here (hub/celery.py). The owner sets
# CELERY_BROKER_URL = ${REDIS_URL}, the private redis.
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_TIME_LIMIT = 60
CELERY_TASK_SOFT_TIME_LIMIT = 45
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_timeout": 5, "socket_connect_timeout": 5}
CELERY_REDIS_SOCKET_TIMEOUT = 5
CELERY_REDIS_SOCKET_CONNECT_TIMEOUT = 5
CELERY_BEAT_SCHEDULE = {
    "beat-tick": {"task": "river.tasks.beat_tick", "schedule": 60.0},
}
