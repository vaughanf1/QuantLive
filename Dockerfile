FROM python:3.12-slim

WORKDIR /app

# Install system dependencies needed by scipy/numpy at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head || echo 'Migration failed, starting anyway'; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
