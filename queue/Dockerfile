FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY db /app/db
COPY queue /app/queue
RUN pip install --no-cache-dir /app/db && pip install --no-cache-dir /app/queue
ENV REDIS_URL=redis://redis:6379/0
CMD ["arq", "seeding_queue.worker.WorkerSettings"]
