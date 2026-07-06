FROM python:3.12-slim AS native-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY native ./native
RUN g++ -std=c++17 -O2 -Wall -Wextra -pedantic \
    native/epsonscan2_push_ready.cpp \
    -ldl \
    -o /epsonscan2-push-ready

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/consume \
    RAW_DUMP_DIR=/debug-dumps \
    WSD_HTTP_PORT=5357

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libusb-1.0-0 libudev1 libcap2 && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /usr/lib/x86_64-linux-gnu && \
    ln -s /epsonscan2-lib /usr/lib/x86_64-linux-gnu/epsonscan2 && \
    addgroup --system wsd && \
    adduser --system --ingroup wsd --home /app wsd && \
    mkdir -p /consume /debug-dumps /data && \
    chown -R wsd:wsd /app /consume /debug-dumps /data

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=native-builder /epsonscan2-push-ready /usr/local/bin/epsonscan2-push-ready

RUN pip install --no-cache-dir .

USER wsd

EXPOSE 5357/tcp
EXPOSE 3702/udp
EXPOSE 2968/udp
EXPOSE 2968/tcp
EXPOSE 3289/udp
EXPOSE 1865/tcp

ENTRYPOINT ["wsd-scan-receiver"]
