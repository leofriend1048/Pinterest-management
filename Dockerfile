FROM python:3.11-slim

WORKDIR /app

# System deps (SSL certs for HTTPS API calls)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps (separate layer — changes rarely, good for build cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Cloud Run mounts a persistent volume at /data — SQLite lives there
    DB_PATH=/data/pambs.db \
    LOG_LEVEL=INFO

EXPOSE 8080

# Production: long-running scheduler + health server on $PORT
CMD ["python", "-m", "pinterest_buyer.main"]
