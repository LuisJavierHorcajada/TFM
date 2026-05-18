FROM python:3.12-slim

# System dependencies for benchmarks
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Create temp directory for disk benchmarks
RUN mkdir -p /tmp/benchmark

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
