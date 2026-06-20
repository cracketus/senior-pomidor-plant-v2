FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

ARG INSTALL_HARDWARE_DEPS=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc i2c-tools python3-dev \
    && if [ "$INSTALL_HARDWARE_DEPS" = "true" ]; then \
        apt-get install -y --no-install-recommends fswebcam v4l-utils libgpiod2 wireless-tools; \
    fi \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-hardware.txt ./
RUN if [ "$INSTALL_HARDWARE_DEPS" = "true" ]; then \
        pip install --no-cache-dir -r requirements-hardware.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY src ./src
COPY scripts ./scripts

CMD ["python", "-m", "src.main"]
