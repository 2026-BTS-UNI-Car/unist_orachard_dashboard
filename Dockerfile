FROM python:3.11-slim AS raster-builder
RUN apt-get update && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
COPY dashboard_server/native_raster.cc /build/native_raster.cc
RUN g++ -O3 -std=c++17 -shared -fPIC /build/native_raster.cc -o /build/_native_raster.so

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
ENV DASHBOARD_PORT=8080
ENV TELEMETRY_FILE=/app/runtime/telemetry_snapshot.json

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY dashboard_server ./dashboard_server
COPY --from=raster-builder /build/_native_raster.so ./dashboard_server/_native_raster.so
COPY static ./static
COPY config ./config
COPY docs ./docs
COPY scripts/__init__.py ./scripts/__init__.py
COPY scripts/recover_recorded_lidar.py ./scripts/recover_recorded_lidar.py

RUN apt-get update \
  && apt-get install -y --no-install-recommends util-linux \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/runtime

EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json, urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3))"

CMD ["python", "-m", "dashboard_server"]
