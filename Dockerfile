FROM python:3.11-slim

LABEL org.opencontainers.image.version="84a-mini-20260813a"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates fontconfig fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY . ./
RUN mkdir -p /app/data && chmod +x /app/start_linux.sh

EXPOSE 3000

CMD ["sh", "-c", "python scripts/live_start_check.py && exec python -m app.runtime"]
