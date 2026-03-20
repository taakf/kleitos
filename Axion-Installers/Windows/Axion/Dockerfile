# =============================================================
# Axion by 4Labs — Portfolio Intelligence System
# Docker build for production deployment
# =============================================================
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies (required for lxml)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt1-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -- Install Python dependencies first (better layer caching) --
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -- Copy application code --
COPY . .

# Create data directories
RUN mkdir -p /data/db /data/logs /data/backups

# Default environment variables
ENV KLEITOS_DATA_DIR=/data \
    KLEITOS_DB_PATH=/data/db/kleitos.db \
    KLEITOS_LOG_LEVEL=INFO \
    KLEITOS_HOST=0.0.0.0 \
    KLEITOS_PORT=7777

# Expose the API port
EXPOSE 7777

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7777/api/v1/health || exit 1

# Graceful shutdown: uvicorn handles SIGTERM cleanly
STOPSIGNAL SIGTERM

# Run the application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "7777", "--log-level", "info"]
