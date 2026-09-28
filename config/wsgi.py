import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# Pre-load station index, city table, USA boundary and provider clients so the first
# request is fast. Only runs when the app is served, never for management commands.
from apps.api.warmup import warm_up  # noqa: E402

warm_up()
