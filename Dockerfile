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
COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt

COPY campus_agent ./campus_agent

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
