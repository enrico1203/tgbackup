# cryptg non pubblica wheel musllinux: su Alpine va compilato con Rust.
# Senza cryptg l'AES-IGE di Telethon gira in Python puro e l'upload diventa CPU-bound.
FROM python:3.14.6-alpine AS builder

RUN apk add --no-cache build-base musl-dev libffi-dev openssl-dev rust cargo

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# rclone serve a leggere direttamente dai remote, senza mount: lsjson per elencare
# e cat --offset --count per le fette di byte. Si prende il binario statico ufficiale
# invece del pacchetto Alpine, che resta indietro di qualche versione.
FROM alpine:3.24 AS rclone

ARG RCLONE_VERSION=1.74.4
RUN apk add --no-cache curl unzip \
    && curl -fsSL -o /tmp/rclone.zip \
        "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-amd64.zip" \
    && unzip -q /tmp/rclone.zip -d /tmp \
    && mv "/tmp/rclone-v${RCLONE_VERSION}-linux-amd64/rclone" /usr/local/bin/rclone \
    && chmod 755 /usr/local/bin/rclone


FROM python:3.14.6-alpine

RUN apk add --no-cache libgcc libstdc++ tzdata fuse3
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

WORKDIR /app
COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

EXPOSE 8000

# Un solo worker: lo scheduler, il registry dei client Telegram e il registry di
# progresso vivono in memoria di processo e non vanno replicati.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
