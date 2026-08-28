FROM python:3.11-slim-bookworm AS mock-core

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /integration
COPY docker/integration/mock_core.py ./mock_core.py

CMD ["python", "mock_core.py"]


FROM python:3.11-slim-bookworm AS integration-test

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /integration
COPY docker/integration/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY docker/integration/integration_test.py ./integration_test.py
COPY schemas/edge-telemetry-v2.schema.json /schemas/edge-telemetry-v2.schema.json

CMD ["python", "integration_test.py"]
