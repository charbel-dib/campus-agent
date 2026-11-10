# Campus agent: web service and demo interface.
#   docker build -t campus-agent .
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -e CAMPUS_SECRET_KEY=... campus-agent
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAMPUS_RUNTIME_DIR=/data \
    CAMPUS_DAILY_BUDGET_USD=2 \
    CAMPUS_SESSION_BUDGET_USD=0.25

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY campus_agent ./campus_agent
# Security fixes of the base image's system packages and a current pip. setuptools and wheel are only
# needed to build: they are removed, with every other copy left in the image (the build log prints where),
# because the versions shipped with Python carry published vulnerabilities that the image scan reports.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir . \
 && pip uninstall -y setuptools wheel \
 && rm -rf /usr/local/lib/python3.11/ensurepip \
 && find / -xdev \( -name 'setuptools' -o -name 'setuptools-*' \) -not -path '/proc/*' -print -prune -exec rm -rf {} +

# Run as an unprivileged user; the school database and certificates live in /data.
RUN useradd --create-home --uid 10001 campus && mkdir -p /data && chown campus /data
USER campus
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/api/health', timeout=2)"

# One worker: the rate limiter and the SQLite file are per process. Scale with a shared store first.
# The port comes from $PORT when the host sets it (Render does). Forwarded addresses are trusted because
# the service only runs behind the host's proxy; they key the rate limiter, nothing else.
CMD ["sh", "-c", "exec uvicorn campus_agent.web:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
