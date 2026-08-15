FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY seeding_upload ./seeding_upload
ENV PYTHONUNBUFFERED=1
EXPOSE 8090
CMD ["uvicorn", "seeding_upload.main:app", "--host", "0.0.0.0", "--port", "8090"]