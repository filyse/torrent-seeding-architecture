FROM debian:trixie-slim
WORKDIR /app
# 2.1.2 нет на PyPI (индекс кончается на 2.1.1). Колесо — релиз GitHub под
# Python 3.13 из Trixie, amd64. python3-libtorrent 2.0.11 рядом не ставить:
# import возьмёт тот модуль, который раньше в sys.path.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python-is-python3 \
    python3-pip \
    libssl3 \
    python3-fastapi \
    python3-uvicorn \
    python3-httpx \
    python3-pytest \
    ca-certificates \
    && pip3 install --no-cache-dir --break-system-packages \
        https://github.com/arvidn/libtorrent/releases/download/v2.1.2/libtorrent-2.1.2-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl \
    && rm -rf /var/lib/apt/lists/*
# context = корень репозитория (нужен пакет upload/seeding_upload).
COPY engine /app/engine
COPY upload/seeding_upload /app/engine/seeding_upload
RUN sed -i 's/\r$//' /app/engine/docker-entrypoint.sh \
    && chmod +x /app/engine/docker-entrypoint.sh
# Метка времени сборки образа (обновляется при каждой реальной пересборке — слой идёт после COPY).
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /app/BUILD_TIME
ENV PYTHONPATH=/app/engine
ENV SEEDING_DATA_ROOT=/data
ENV SEEDING_ENGINE_BACKEND=libtorrent
EXPOSE 8081
ENTRYPOINT ["/app/engine/docker-entrypoint.sh"]
CMD ["python3", "-m", "uvicorn", "seeding_engine.main:app", "--host", "0.0.0.0", "--port", "8081"]
