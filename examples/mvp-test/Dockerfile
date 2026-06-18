# MVP demo "test" image. Prints random INFO logs to stdout + /artifacts/run.log
# every 10s for ~1 minute. Use as the `framework_image` when submitting a test.
#   build:  docker build -t mvp-test:latest examples/mvp-test
FROM python:3.12-slim

# Unbuffered so each log line reaches `docker logs` (and the live stream) at once.
ENV PYTHONUNBUFFERED=1

COPY run_test.py /run_test.py

CMD ["python", "/run_test.py"]
