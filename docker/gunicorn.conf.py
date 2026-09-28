"""
Gunicorn configuration.

One worker with threads rather than several processes: the in-memory station index and the
route cache then exist exactly once, so every request benefits from every earlier one.
Planning is CPU-light (tens of ms) and the upstream routing call is I/O, which threads
overlap fine. Scale out with more containers behind a proxy, and switch CACHE_URL to Redis
so they share the route cache.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_class = "gthread"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'

# Behind a reverse proxy on the same host; trust X-Forwarded-* from it.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
