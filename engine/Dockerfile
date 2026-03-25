FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip
COPY engine /app/engine
RUN pip install --no-cache-dir /app/engine
ENV SEEDING_DATA_ROOT=/data
EXPOSE 8081 6881
CMD ["uvicorn", "seeding_engine.main:app", "--host", "0.0.0.0", "--port", "8081"]
