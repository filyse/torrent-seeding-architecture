FROM debian:trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python-is-python3 \
    python3-libtorrent \
    python3-fastapi \
    python3-uvicorn \
    python3-httpx \
    python3-pytest \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY . /app/engine
RUN sed -i 's/\r$//' /app/engine/docker-entrypoint.sh \
    && chmod +x /app/engine/docker-entrypoint.sh
ENV PYTHONPATH=/app/engine
ENV SEEDING_DATA_ROOT=/data
ENV SEEDING_ENGINE_BACKEND=libtorrent
EXPOSE 8081
ENTRYPOINT ["/app/engine/docker-entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "seeding_engine.main:app", "--host", "0.0.0.0", "--port", "8081"]
