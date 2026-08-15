FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY seeding_upload_relay ./seeding_upload_relay
ENV PYTHONUNBUFFERED=1
EXPOSE 8091
CMD ["uvicorn", "seeding_upload_relay.main:app", "--host", "0.0.0.0", "--port", "8091"]
