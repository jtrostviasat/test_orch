# Backend (FastAPI + Uvicorn) image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy the application code (shared + backend packages).
COPY shared ./shared
COPY backend ./backend

EXPOSE 8000

# Reload is intentionally OFF here; use docker-compose override for dev reload.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]