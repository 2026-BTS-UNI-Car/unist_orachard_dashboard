from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import secrets
import select
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dashboard_server.telemetry import ROOT, TelemetryProvider


STATIC_ROOT = ROOT / "static"
DOCS_ROOT = ROOT / "docs"
APP_ROUTES = {"/", "/connection", "/system", "/network", "/docker", "/fan", "/research", "/docs"}
SESSION_COOKIE_NAME = "ubts_session"
SESSION_TTL_S = int(os.environ.get("DASHBOARD_SESSION_TTL_S", "3600"))
_SESSION_LOCK = threading.Lock()
_SESSIONS: dict[str, dict[str, float]] = {}
_ACTIVE_LIDAR_WS: dict[str, dict[str, float | str]] = {}


def dashboard_log(event: str, **fields):
    payload = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def _prune_sessions(now: float | None = None):
    now = now or time.time()
    expired = [token for token, item in _SESSIONS.items() if item.get("expires_at", 0.0) <= now]
    for token in expired:
        _SESSIONS.pop(token, None)
    for connection_id, item in list(_ACTIVE_LIDAR_WS.items()):
        if item.get("session") in expired:
            _ACTIVE_LIDAR_WS.pop(connection_id, None)


def lidar_websocket_stats() -> dict[str, object]:
    now = time.time()
    with _SESSION_LOCK:
        _prune_sessions(now)
        sessions = [
            {
                "id": token[:8],
                "age_sec": round(now - float(item.get("connected_at", now)), 1),
                "remote": item.get("remote", ""),
                "session": str(item.get("session", ""))[:8],
            }
            for token, item in _ACTIVE_LIDAR_WS.items()
        ]
        return {
            "active": len(sessions),
            "sessions": sessions,
            "session_ttl_s": SESSION_TTL_S,
            "known_sessions": len(_SESSIONS),
        }


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
        "summary": "Jetson 온도 확인과 수동 PWM 유지",
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
    protocol_version = "HTTP/1.1"
    _last_telemetry_log_at = 0.0

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
        elif path.startswith("/api/docker/container/"):
            self._post_docker_container_action(path)
        else:
            self.send_error(404, "Not found")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/ws/lidar":
            self._proxy_lidar_websocket()
        elif path in APP_ROUTES or path.startswith("/docs/"):
            self._send_file(STATIC_ROOT / "index.html")
        elif path == "/api/health":
            self._send_json(self.provider.health())
        elif path == "/api/telemetry":
            payload = self.provider.telemetry()
            payload["connection"]["lidar_websocket"] = lidar_websocket_stats()
            self._send_json(payload)
        elif path == "/api/system":
            self._send_json(self.provider.system())
        elif path == "/api/network":
            self._send_json(self.provider.network())
        elif path == "/api/docker":
            self._send_json(self.provider.docker())
        elif path.startswith("/api/docker/container/"):
            self._send_docker_container_payload(path, parsed.query)
        elif path == "/api/connection":
            payload = self.provider.connection()
            payload["lidar_websocket"] = lidar_websocket_stats()
            self._send_json(payload)
        elif path == "/api/plan":
            self._send_json(self.provider.plan())
        elif path == "/api/site-map":
            self._send_json(self.provider.site_map())
        elif path == "/api/fan/status":
            self._send_json(self.provider.fan_status())
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
        session_token = self._ensure_session(create=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._send_session_cookie(session_token)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True
        self._log_served_json()

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
        session_token = self._ensure_session(create=True)
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
        self.send_header("Connection", "close")
        self._send_session_cookie(session_token)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _ensure_session(self, create: bool) -> str | None:
        now = time.time()
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            cookie = SimpleCookie()
        token = cookie.get(SESSION_COOKIE_NAME).value if SESSION_COOKIE_NAME in cookie else None
        with _SESSION_LOCK:
            _prune_sessions(now)
            if token and token in _SESSIONS:
                return token
            if not create:
                return None
            token = secrets.token_urlsafe(24)
            _SESSIONS[token] = {"created_at": now, "expires_at": now + SESSION_TTL_S}
        dashboard_log("session.created", session=token[:8], remote=self.client_address[0])
        return token

    def _send_session_cookie(self, token: str | None):
        if not token:
            return
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_TTL_S}; HttpOnly; SameSite=Lax",
        )

    def _log_served_json(self):
        path = urlparse(self.path).path
        now = time.time()
        if path == "/api/telemetry":
            if now - DashboardHandler._last_telemetry_log_at < 30:
                return
            DashboardHandler._last_telemetry_log_at = now
            dashboard_log("api.telemetry", active_lidar_ws=lidar_websocket_stats()["active"])
            return
        if path not in {"/api/health"}:
            dashboard_log("api.request", path=path, active_lidar_ws=lidar_websocket_stats()["active"])

    def _send_docker_container_payload(self, path: str, query: str):
        raw = path.removeprefix("/api/docker/container/").strip("/")
        if not raw:
            self.send_error(404, "Container id required")
            return
        if raw.endswith("/logs"):
            container_id = unquote(raw.removesuffix("/logs").strip("/"))
            params = parse_qs(query)
            tail = (params.get("tail") or ["160"])[0]
            self._send_json(self.provider.docker_container_logs(container_id, tail=tail))
            return
        self._send_json(self.provider.docker_container_detail(unquote(raw)))

    def _post_docker_container_action(self, path: str):
        raw = path.removeprefix("/api/docker/container/").strip("/")
        parts = [unquote(item) for item in raw.split("/") if item]
        if len(parts) != 2 or parts[1] not in {"start", "stop", "restart"}:
            self.send_error(404, "Unknown container action")
            return
        container_id, action = parts
        result = self.provider.docker_container_action(container_id, action)
        dashboard_log("docker.action", action=action, container=container_id[:12], ok=bool(result.get("ok")), state=result.get("state"))
        self._send_json(result)

    def _proxy_lidar_websocket(self):
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(426, "WebSocket upgrade required")
            return
        client_key = self.headers.get("Sec-WebSocket-Key", "")
        if not client_key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return
        session_token = self._ensure_session(create=False)
        if not session_token:
            dashboard_log("ws.lidar.rejected", reason="missing_session", remote=self.client_address[0])
            self.send_error(403, "LiDAR WebSocket session required")
            return
        now = time.time()
        with _SESSION_LOCK:
            session = _SESSIONS.get(session_token)
            session_deadline = float(session.get("expires_at", now + SESSION_TTL_S)) if session else now
            connection_id = secrets.token_urlsafe(8)
            _ACTIVE_LIDAR_WS[connection_id] = {"session": session_token, "connected_at": now, "remote": self.client_address[0]}

        accept = base64.b64encode(
            hashlib.sha1((client_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        dashboard_log("ws.lidar.open", session=session_token[:8], connection=connection_id[:8], active=lidar_websocket_stats()["active"])

        upstream_url = self._lidar_upstream_with_client_query(
            os.environ.get("LIDAR_WS_UPSTREAM", "ws://127.0.0.1:8765/ws/lidar")
        )
        try:
            unavailable_logged = False
            while time.time() < session_deadline:
                try:
                    upstream, initial_upstream = self._connect_lidar_upstream(upstream_url)
                except OSError as exc:
                    if not unavailable_logged:
                        dashboard_log("ws.lidar.upstream_unavailable", session=session_token[:8], error=str(exc))
                        unavailable_logged = True
                    ok = self._ws_send_json(
                        self.connection,
                        {
                            "type": "status",
                            "device_found": False,
                            "scan_topic_alive": False,
                            "last_scan_age_sec": None,
                            "point_count": 0,
                            "source": "bridge",
                            "error": f"LiDAR WebSocket bridge unavailable: {exc}",
                        },
                    )
                    if not ok:
                        return
                    time.sleep(min(2.0, max(0.1, session_deadline - time.time())))
                    continue
                try:
                    self._relay_websocket(upstream, initial_upstream, session_deadline)
                finally:
                    try:
                        upstream.close()
                    except OSError:
                        pass
                return
            self._ws_send_json(
                self.connection,
                {"type": "status", "scan_topic_alive": False, "error": "LiDAR WebSocket session expired."},
            )
            self._ws_send_close(self.connection)
        finally:
            with _SESSION_LOCK:
                _ACTIVE_LIDAR_WS.pop(connection_id, None)
            dashboard_log("ws.lidar.close", session=session_token[:8], connection=connection_id[:8], active=lidar_websocket_stats()["active"])

    def _connect_lidar_upstream(self, upstream_url: str) -> tuple[socket.socket, bytes]:
        parsed = urlparse(upstream_url)
        if parsed.scheme != "ws":
            raise OSError("LIDAR_WS_UPSTREAM must use ws:// for the local bridge")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/ws/lidar"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        sock = socket.create_connection((host, port), timeout=3)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        host_header = f"{host}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 8192:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk
        response_head, _, initial = response.partition(b"\r\n\r\n")
        if b" 101 " not in response_head.split(b"\r\n", 1)[0]:
            sock.close()
            raise OSError("local bridge did not accept WebSocket upgrade")
        sock.settimeout(None)
        return sock, initial

    def _lidar_upstream_with_client_query(self, upstream_url: str) -> str:
        client_query = urlparse(self.path).query
        if not client_query:
            return upstream_url
        separator = "&" if urlparse(upstream_url).query else "?"
        return f"{upstream_url}{separator}{client_query}"

    def _relay_websocket(self, upstream: socket.socket, initial_upstream: bytes = b"", deadline: float | None = None):
        if initial_upstream:
            try:
                self.connection.sendall(initial_upstream)
            except OSError:
                return
        sockets = [self.connection, upstream]
        while True:
            timeout = 30.0
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._ws_send_close(self.connection)
                    return
                timeout = min(timeout, max(0.1, remaining))
            readable, _, errored = select.select(sockets, [], sockets, timeout)
            if errored:
                break
            if not readable:
                if deadline is not None and time.time() >= deadline:
                    self._ws_send_close(self.connection)
                    return
                self._ws_send_json(
                    self.connection,
                    {
                        "type": "status",
                        "device_found": False,
                        "scan_topic_alive": False,
                        "last_scan_age_sec": None,
                        "point_count": 0,
                        "error": "LiDAR WebSocket bridge timeout",
                    },
                )
                continue
            for sock in readable:
                try:
                    data = sock.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                target = upstream if sock is self.connection else self.connection
                try:
                    target.sendall(data)
                except OSError:
                    return

    def _ws_send_json(self, sock: socket.socket, payload: dict) -> bool:
        return self._ws_send_frame(sock, 0x1, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _ws_send_close(self, sock: socket.socket):
        self._ws_send_frame(sock, 0x8, b"\x03\xe8")

    def _ws_send_frame(self, sock: socket.socket, opcode: int, payload: bytes) -> bool:
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.extend([126, (length >> 8) & 0xFF, length & 0xFF])
        else:
            header.append(127)
            header.extend(length.to_bytes(8, "big"))
        try:
            sock.sendall(bytes(header) + payload)
        except OSError:
            return False
        return True


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host: str, port: int, config: str | None = None) -> ThreadingHTTPServer:
    DashboardHandler.provider = TelemetryProvider(config)
    dashboard_log("server.create", host=host, port=port, session_ttl_s=SESSION_TTL_S)
    return DashboardServer((host, port), DashboardHandler)


def main():
    parser = argparse.ArgumentParser(description="UNIST orchard rover dashboard server")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "8080")))
    parser.add_argument("--config", default=os.environ.get("DASHBOARD_CONFIG"))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.config)
    dashboard_log("server.listen", url=f"http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
