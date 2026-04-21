FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (HF requirement)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data dirs
RUN mkdir -p data/cache logs && chown -R appuser:appuser /app

USER appuser

# HuggingFace Spaces expects port 7860
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
EXPOSE 7860

CMD ["python", "run.py"]
