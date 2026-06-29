FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_HOST=0.0.0.0
ENV DASHBOARD_PORT=8080
ENV TELEMETRY_FILE=/app/runtime/telemetry_snapshot.json

WORKDIR /app

COPY requirements.txt ./
COPY dashboard_server ./dashboard_server
COPY static ./static
COPY config ./config
COPY sample_data ./sample_data

RUN mkdir -p /app/runtime \
  && useradd --create-home --uid 10001 dashboard \
  && chown -R dashboard:dashboard /app

USER dashboard

EXPOSE 8080

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json, urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3))"

CMD ["python", "-m", "dashboard_server"]
