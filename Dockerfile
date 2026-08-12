# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Build tools are only needed transiently for compiling the cryptography
# wheel on platforms without a prebuilt one; they are not needed at runtime,
# hence the separate builder stage below.
FROM base AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libffi-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir --prefix=/install .

FROM base
LABEL org.opencontainers.image.title="clinic-ai-assistant" \
      org.opencontainers.image.description="Local-first AI assistant for private clinics"

# Runs as a non-root user; the data directory is a mounted volume owned by
# this user so the container never needs root to read/write patient data.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin clinic
WORKDIR /app
COPY --from=builder /install /usr/local
COPY backend ./backend
COPY web ./web
COPY VERSION ./VERSION
RUN mkdir -p /app/data && chown -R clinic:clinic /app

USER clinic
ENV PYTHONUNBUFFERED=1
VOLUME ["/app/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8080"]
