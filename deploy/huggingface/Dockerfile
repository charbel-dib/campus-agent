# Hugging Face Space (Docker SDK) for the public demo of the campus agent.
# It builds the main branch of the GitHub repository, so the Space only holds this file and its README.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Public demo: one copy of the school per visitor, quotas, budget for the whole demo
    CAMPUS_SANDBOX=1 \
    CAMPUS_SANDBOX_TTL_HOURS=6 \
    CAMPUS_VISITOR_MESSAGE_LIMIT=30 \
    CAMPUS_VISITOR_BUDGET_USD=0.15 \
    CAMPUS_SESSION_BUDGET_USD=0.15 \
    CAMPUS_DAILY_BUDGET_USD=2 \
    CAMPUS_RATE_LIMIT_PER_MINUTE=12 \
    CAMPUS_MODEL=claude-haiku-5-5 \
    CAMPUS_FRAME_ANCESTORS="'self' https://huggingface.co" \
    CAMPUS_RUNTIME_DIR=/home/user/data

# Spaces run the container as user 1000
RUN useradd --create-home --uid 1000 user
WORKDIR /app

ADD https://github.com/charbel-dib/ensta_chatbot/archive/refs/heads/main.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz -C /tmp \
    && cp -r /tmp/ensta_chatbot-main/campus_agent /tmp/ensta_chatbot-main/requirements-agent.txt /app/ \
    && pip install --no-cache-dir -r requirements-agent.txt \
    && rm -rf /tmp/source.tar.gz /tmp/ensta_chatbot-main

USER user
EXPOSE 7860
# --proxy-headers: the Space proxy forwards the visitor's address, used by the rate limiter.
CMD ["uvicorn", "campus_agent.web:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
