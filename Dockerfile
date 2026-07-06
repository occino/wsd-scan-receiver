FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/scans \
    RAW_DUMP_DIR=/debug-dumps \
    WSD_HTTP_PORT=5357

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libcap2 && \
    rm -rf /var/lib/apt/lists/* && \
    addgroup --system wsd && \
    adduser --system --ingroup wsd --home /app wsd && \
    mkdir -p /scans /debug-dumps /data && \
    chown -R wsd:wsd /app /scans /debug-dumps /data

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

USER wsd

EXPOSE 5357/tcp
EXPOSE 3702/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os; from urllib.request import urlopen; urlopen(f'http://127.0.0.1:{os.getenv(\"WSD_HTTP_PORT\", \"5357\")}/healthz', timeout=3).read()"

ENTRYPOINT ["wsd-scan-receiver"]
