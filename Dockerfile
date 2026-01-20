# Production-ready Dockerfile (works for local and Cloud Run)
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port 8080 for Cloud Run
EXPOSE 8080

# Run the application with gunicorn on PORT from environment (Cloud Run sets this to 8080)
CMD exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 4 --timeout 120 app:app
