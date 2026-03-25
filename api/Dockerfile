FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY db /app/db
COPY api /app/api
RUN pip install --no-cache-dir /app/db && pip install --no-cache-dir /app/api
EXPOSE 8000
CMD ["sh", "-c", "cd /app/db && alembic upgrade head && exec uvicorn seeding_api.main:app --host 0.0.0.0 --port 8000"]
