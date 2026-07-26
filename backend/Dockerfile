# cryptg publishes no musllinux wheel: on Alpine it has to be compiled with Rust.
# Without cryptg, Telethon's AES-IGE runs in pure Python and the upload becomes CPU-bound.
FROM python:3.14.6-alpine AS builder

RUN apk add --no-cache build-base musl-dev libffi-dev openssl-dev rust cargo

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# rclone reads remotes directly, with no mount: lsjson to list and cat --offset --count
# for byte ranges. The official static binary is used instead of the Alpine package,
# which lags a few versions behind.
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
# The migrations travel with the code: the container upgrades its own database at start.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

EXPOSE 8000

# A single worker: the scheduler, the Telegram client registry and the progress registry
# live in process memory and must not be replicated.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
