FROM debian:trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python-is-python3 \
    python3-fastapi \
    python3-uvicorn \
    python3-sqlalchemy \
    python3-alembic \
    python3-asyncpg \
    python3-psycopg \
    python3-httpx \
    python3-redis \
    python3-python-multipart \
    python3-click \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch v0.26.3 https://github.com/samuelcolvin/arq.git /opt/arq
COPY db /app/db
COPY api /app/api
ENV PYTHONPATH=/app/api:/app/db:/opt/arq
EXPOSE 8000
CMD ["sh", "-c", "cd /app/db && python3 -m alembic upgrade head && exec python3 -m uvicorn seeding_api.main:app --host 0.0.0.0 --port 8000"]
