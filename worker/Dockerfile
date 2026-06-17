# Worker (Celery) image for a single host.
#
# Runs the Celery worker AND beat in one container for MVP simplicity. The
# rootless Docker socket is bind-mounted in at runtime (see docker-compose.yml).
# The --uid must match the host UID that owns the rootless socket.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY shared ./shared
COPY backend ./backend
COPY worker ./worker

# Create a non-root user whose UID matches the rootless Docker socket owner.
# IMPORTANT: if your host UID is not 1000, change this AND the socket path in
# docker-compose.yml / .env accordingly.
RUN useradd --create-home --uid 1000 worker
USER worker

# WORKER_HOST_ID is read from the environment and selects this host's queue.
# Beat is colocated to publish heartbeats and run the reaper.
CMD ["celery", "-A", "worker.celery_app", "worker", \
     "--beat", "--loglevel=info", "--concurrency=4"]