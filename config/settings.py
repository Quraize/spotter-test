"""
Django settings for the Spotter Fuel Router API.

All environment-specific values come from the environment (or a .env file in
the project root) via django-environ. See .env.example for the full list.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    LOG_LEVEL=(str, "INFO"),
    ROUTING_PROVIDERS=(list, ["ors", "osrm"]),
    GEOCODING_PROVIDERS=(list, ["ors", "nominatim"]),
    ORS_API_KEY=(str, ""),
    ORS_BASE_URL=(str, "https://api.openrouteservice.org"),
    ORS_PROFILE=(str, "driving-hgv"),
    OSRM_BASE_URL=(str, "https://router.project-osrm.org"),
    NOMINATIM_BASE_URL=(str, "https://nominatim.openstreetmap.org"),
    HTTP_USER_AGENT=(str, "spotter-fuel-router/0.1 (assessment project)"),
    EXTERNAL_TIMEOUT_SECONDS=(float, 10.0),
    ROUTE_CACHE_SECONDS=(int, 60 * 60 * 24),
    API_THROTTLE_RATE=(str, "60/min"),
    SECURE_SSL_REDIRECT=(bool, False),
    SECURE_HSTS_SECONDS=(int, 0),
    MAP_TILE_URL=(
        str,
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer"
        "/tile/{z}/{y}/{x}",
    ),
    MAP_TILE_ATTRIBUTION=(
        str,
        "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors",
    ),
    MAP_TILE_SUBDOMAINS=(str, "abc"),
)
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.auth",  # required by DRF's permission classes even with no auth in use
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.stations",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Cache (routing responses). LocMem is per-process; swap for Redis via CACHE_URL.
# ---------------------------------------------------------------------------
CACHES = {
    "default": env.cache("CACHE_URL", default="locmemcache://spotter-fuel-router"),
}

# ---------------------------------------------------------------------------
# I18N / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Security (effective when DEBUG=False)
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# Off by default: TLS termination normally happens at the reverse proxy. Enable via env.
SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT")
SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS")
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": env("API_THROTTLE_RATE")},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.exception_handler",
}

if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

SPECTACULAR_SETTINGS = {
    "TITLE": "Spotter Fuel Router API",
    "DESCRIPTION": (
        "Plans a US road trip and picks the most cost-effective fuel stops along the route, "
        "assuming a 500 mile range and 10 mpg."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------------------
# Domain / external providers
# ---------------------------------------------------------------------------
VEHICLE_RANGE_MILES = 500.0
VEHICLE_MPG = 10.0
CORRIDOR_MILES = 10.0  # city-centroid geocoding needs slack; 5 mi drops too many stations
STOP_PENALTY_USD = 10.0  # driver time per stop; 0 = pure cost minimum (many tiny stops)

# Basemap for the HTML map page. Esri World Street Map needs no key. The OSM public tile
# server also works but only when the browser sends a Referer (see MAP_TILE_URL in .env.example).
MAP_TILES = {
    "url": env("MAP_TILE_URL"),
    "attribution": env("MAP_TILE_ATTRIBUTION"),
    "subdomains": env("MAP_TILE_SUBDOMAINS"),
}

# Ordered fallback chains; the first usable provider is primary.
ROUTING_PROVIDERS = env("ROUTING_PROVIDERS")
GEOCODING_PROVIDERS = env("GEOCODING_PROVIDERS")
ORS_API_KEY = env("ORS_API_KEY")
ORS_BASE_URL = env("ORS_BASE_URL")
ORS_PROFILE = env("ORS_PROFILE")  # driving-hgv (truck) or driving-car
OSRM_BASE_URL = env("OSRM_BASE_URL")
NOMINATIM_BASE_URL = env("NOMINATIM_BASE_URL")
HTTP_USER_AGENT = env("HTTP_USER_AGENT")
EXTERNAL_TIMEOUT_SECONDS = env("EXTERNAL_TIMEOUT_SECONDS")
ROUTE_CACHE_SECONDS = env("ROUTE_CACHE_SECONDS")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL")},
    "loggers": {
        "django": {"level": "INFO"},
        "httpx": {"level": "WARNING"},
    },
}
