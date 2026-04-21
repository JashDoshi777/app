FROM python:3.11-slim

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (HF requirement)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install dependencies — copy requirements and install in a single layer
# Adding cache bust to force reinstall
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir smartapi-python pyotp websocket-client && \
    python -c "from SmartApi import SmartConnect; print('SmartAPI OK')" && \
    python -c "import psycopg2; print('psycopg2 OK')" && \
    python -c "import fastapi; print('FastAPI OK')"

# Copy application code
COPY . .

# Create data dirs and set permissions
RUN mkdir -p data/cache logs && chown -R appuser:appuser /app

# Verify static files exist
RUN ls -la web/static/css/style.css web/static/js/app.js web/templates/index.html

USER appuser

# HuggingFace Spaces expects port 7860
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
EXPOSE 7860

CMD ["python", "run.py"]
