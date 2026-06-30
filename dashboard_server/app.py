from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from dashboard_server.telemetry import ROOT, TelemetryProvider


STATIC_ROOT = ROOT / "static"
DOCS_ROOT = ROOT / "docs"
APP_ROUTES = {"/", "/connection", "/system", "/network", "/docker", "/fan", "/research", "/docs"}
DOCS_CATALOG = [
    {
        "slug": "architecture",
        "title": "대시보드 아키텍처",
        "category": "System",
        "summary": "Cloudflare Tunnel, Python 서버, telemetry 파일, 프론트엔드 흐름",
        "file": "architecture.md",
    },
    {
        "slug": "jetson-deployment",
        "title": "Jetson 배포",
        "category": "Deployment",
        "summary": "Jetson Orin Nano에서 Docker Compose와 자동 재시작으로 운영하는 절차",
        "file": "jetson_deployment.md",
    },
    {
        "slug": "lidar-connection",
        "title": "LiDAR 연결과 테스트",
        "category": "Hardware",
        "summary": "RPLIDAR S2, ROS2 /scan owner 구조, WebSocket live map, serial 충돌 방지",
        "file": "lidar_connection.md",
    },
    {
        "slug": "jetson-runtime-architecture",
        "title": "Jetson 런타임 아키텍처",
        "category": "Deployment",
        "summary": "ROS2 real bringup, LiDAR bridge, dashboard, fan control 서비스 구성",
        "file": "jetson_runtime_architecture.md",
    },
    {
        "slug": "fan-control",
        "title": "Fan Control",
        "category": "Hardware",
        "summary": "nvfancontrol 자동 제어, 수동 PWM, RPM 보정, 목표 RPM 모드",
        "file": "fan_control.md",
    },
    {
        "slug": "cloudflared",
        "title": "Cloudflare Tunnel",
        "category": "Network",
        "summary": "bts.sarihub.com 터널과 localhost:8080 연결 구성",
        "file": "cloudflared.md",
    },
    {
        "slug": "telemetry-schema",
        "title": "텔레메트리 스키마",
        "category": "Data",
        "summary": "ROS2 bridge가 dashboard server로 넘길 JSON 구조",
        "file": "telemetry_schema.md",
    },
    {
        "slug": "research-metrics",
        "title": "연구 지표",
        "category": "Research",
        "summary": "Vision-LiDAR 융합, 중심선 추정, 경로추종 성능 지표",
        "file": "research_metrics.md",
    },
]


class DashboardHandler(BaseHTTPRequestHandler):
    provider: TelemetryProvider

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/lidar/test":
            self._send_json(self.provider.lidar_test())
        elif path == "/api/lidar/scan":
            self._send_json(self.provider.lidar_scan())
        elif path == "/api/fan/mode":
            payload = self._read_json_body()
            self._send_json(self.provider.set_fan_mode(bool(payload.get("auto"))))
        elif path == "/api/fan/pwm":
            payload = self._read_json_body()
            self._send_json(self.provider.set_fan_pwm(payload.get("pwm")))
        elif path == "/api/fan/preset":
            payload = self._read_json_body()
            self._send_json(self.provider.set_fan_preset(str(payload.get("preset", ""))))
        elif path == "/api/fan/calibrate":
            self._send_json(self.provider.calibrate_fan())
        elif path == "/api/fan/rpm":
            payload = self._read_json_body()
            self._send_json(self.provider.set_fan_target_rpm(payload.get("target_rpm")))
        else:
            self.send_error(404, "Not found")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in APP_ROUTES or path.startswith("/docs/"):
            self._send_file(STATIC_ROOT / "index.html")
        elif path == "/api/health":
            self._send_json(self.provider.health())
        elif path == "/api/telemetry":
            self._send_json(self.provider.telemetry())
        elif path == "/api/system":
            self._send_json(self.provider.system())
        elif path == "/api/network":
            self._send_json(self.provider.network())
        elif path == "/api/docker":
            self._send_json(self.provider.docker())
        elif path == "/api/connection":
            self._send_json(self.provider.connection())
        elif path == "/api/plan":
            self._send_json(self.provider.plan())
        elif path == "/api/site-map":
            self._send_json(self.provider.site_map())
        elif path == "/api/fan/status":
            self._send_json(self.provider.fan_status())
        elif path == "/api/fan/calibration":
            self._send_json(self.provider.fan_calibration())
        elif path == "/api/fan/calibration/status":
            self._send_json(self.provider.fan_calibration_status())
        elif path == "/api/docs":
            self._send_json(self._docs_index())
        elif path.startswith("/api/docs/"):
            self._send_json(self._doc_payload(path.removeprefix("/api/docs/")))
        elif path.startswith("/static/"):
            self._send_file(STATIC_ROOT / path.removeprefix("/static/"))
        else:
            self.send_error(404, "Not found")

    def log_message(self, fmt, *args):
        if os.environ.get("DASHBOARD_ACCESS_LOG", "0") == "1":
            super().log_message(fmt, *args)

    def _send_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _docs_index(self):
        return {"documents": [self._doc_meta(item) for item in DOCS_CATALOG]}

    def _doc_payload(self, raw_slug: str):
        slug = unquote(raw_slug).strip("/")
        item = next((row for row in DOCS_CATALOG if row["slug"] == slug), None)
        if not item:
            return {"ok": False, "error": "document_not_found", "slug": slug}
        path = (DOCS_ROOT / item["file"]).resolve()
        try:
            if DOCS_ROOT.resolve() not in path.parents:
                return {"ok": False, "error": "document_outside_root", "slug": slug}
            markdown = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc), "slug": slug}
        return {"ok": True, **self._doc_meta(item), "markdown": markdown}

    def _doc_meta(self, item):
        path = DOCS_ROOT / item["file"]
        mtime = int(path.stat().st_mtime) if path.exists() else None
        return {
            "slug": item["slug"],
            "title": item["title"],
            "category": item["category"],
            "summary": item["summary"],
            "source": f"docs/{item['file']}",
            "updated_at": mtime,
            "url": f"/docs/{item['slug']}",
        }

    def _send_file(self, path: Path):
        try:
            resolved = path.resolve()
            if STATIC_ROOT.resolve() not in resolved.parents and resolved != (STATIC_ROOT / "index.html").resolve():
                self.send_error(403, "Forbidden")
                return
            body = resolved.read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        if resolved.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif resolved.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif resolved.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def create_server(host: str, port: int, config: str | None = None) -> ThreadingHTTPServer:
    DashboardHandler.provider = TelemetryProvider(config)
    return ThreadingHTTPServer((host, port), DashboardHandler)


def main():
    parser = argparse.ArgumentParser(description="UNIST orchard rover dashboard server")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8080")))
    parser.add_argument("--config", default=os.environ.get("DASHBOARD_CONFIG"))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.config)
    print(f"dashboard listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
