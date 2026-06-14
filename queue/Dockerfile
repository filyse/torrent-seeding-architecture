FROM debian:trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python-is-python3 \
    python3-sqlalchemy \
    python3-alembic \
    python3-asyncpg \
    python3-psycopg \
    python3-httpx \
    python3-redis \
    python3-click \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch v0.26.3 https://github.com/samuelcolvin/arq.git /opt/arq
COPY db /app/db
COPY queue /app/queue
ENV PYTHONPATH=/app/queue:/app/db:/opt/arq
ENV REDIS_URL=redis://redis:6379/0
CMD ["python3", "-m", "arq", "seeding_queue.worker.WorkerSettings"]
