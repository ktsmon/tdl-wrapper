# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies and tdl CLI
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install tdl CLI
# Get the latest version from GitHub releases
RUN curl -L https://github.com/iyear/tdl/releases/latest/download/tdl_Linux_64bit.tar.gz -o tdl.tar.gz \
    && tar -xzf tdl.tar.gz \
    && mv tdl /usr/local/bin/tdl \
    && chmod +x /usr/local/bin/tdl \
    && rm tdl.tar.gz

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY wsgi.py .
COPY config.example.yaml .

# Create directories for data persistence
RUN mkdir -p /data/downloads /data/exports /data/logs /data/db /data/tdl

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    TDL_DB_PATH=/data/db/tdl_wrapper.db \
    TDL_DATA_DIR=/data/tdl

# Expose web dashboard port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command (can be overridden in docker-compose)
CMD ["python", "-m", "src.cli", "web"]
