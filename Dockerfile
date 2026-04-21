FROM python:3.11-slim

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python deps in separate steps for better error isolation
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

RUN mkdir -p data/cache logs && chown -R appuser:appuser /app

USER appuser

ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8
EXPOSE 7860

CMD ["python", "run.py"]
