"""화면 점검용 모의 서버.

주어진 static 폴더를 그대로 읽어 주고, 화면이 부르는 API 경로에는 지어낸 JSON으로 답한다.
- 127.0.0.1의 빈 포트에만 붙는다. 주소는 바꿀 수 없다.
- static 폴더는 읽기만 한다. 아무 파일도 쓰지 않는다.
- 쓰기 요청(POST)은 받기만 하고 처리하지 않는다.
- 웹소켓 네 경로는 연결을 받는다. stress 자료에서는 LiDAR·IMU·GPS 표본 하나를 1초마다 보낸다.

혼자 띄우기:
    python mock_server.py --static <static 폴더> --dataset stress
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_data  # noqa: E402

HOST = "127.0.0.1"
# The six screens, the system tabs, the docs and the old addresses (docs/SCREENS_KO.md); all answer with index.html.
APP_ROUTES = ("/rover-control", "/map", "/sensors", "/records", "/tuning", "/system", "/system/resources", "/system/network",
              "/system/docker", "/system/fan", "/docs", "/", "/connection", "/network", "/docker", "/fan", "/research")
VEHICLES = ("differential", "front_steer")
WS_CHANNELS = {"/ws/lidar": "lidar", "/ws/camera": "camera", "/ws/imu": "imu", "/ws/gps": "gps"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_POST_BYTES = 1 << 20
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".pbf": "application/x-protobuf",
    ".wasm": "application/wasm",
    ".map": "application/json",
}


def websocket_accept_key(client_key: str) -> str:
    """RFC 6455의 Sec-WebSocket-Accept 값."""
    digest = hashlib.sha1((client_key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """서버가 보내는 프레임(가림 없음, 조각 없음)."""
    head = bytes([0x80 | (opcode & 0x0F)])
    size = len(payload)
    if size < 126:
        head += bytes([size])
    elif size < 65536:
        head += bytes([126]) + struct.pack(">H", size)
    else:
        head += bytes([127]) + struct.pack(">Q", size)
    return head + payload


def decode_ws_frame(buffer: bytes):
    """받은 바이트 앞쪽에서 프레임 하나를 푼다. 모자라면 None, 아니면 (opcode, payload, 쓴 길이)."""
    if len(buffer) < 2:
        return None
    opcode = buffer[0] & 0x0F
    masked = bool(buffer[1] & 0x80)
    size = buffer[1] & 0x7F
    offset = 2
    if size == 126:
        if len(buffer) < offset + 2:
            return None
        size = struct.unpack(">H", buffer[offset:offset + 2])[0]
        offset += 2
    elif size == 127:
        if len(buffer) < offset + 8:
            return None
        size = struct.unpack(">Q", buffer[offset:offset + 8])[0]
        offset += 8
    mask = b""
    if masked:
        if len(buffer) < offset + 4:
            return None
        mask = buffer[offset:offset + 4]
        offset += 4
    if len(buffer) < offset + size:
        return None
    payload = buffer[offset:offset + size]
    if masked:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, payload, offset + size


def resolve_static(static_root: Path, relative: str):
    """static 폴더 밖을 가리키면 None. 폴더 안의 실제 파일이면 그 경로."""
    if not relative or "\x00" in relative:
        return None
    try:
        target = (static_root / relative).resolve()
    except (OSError, ValueError):
        return None
    if target != static_root and static_root not in target.parents:
        return None
    return target if target.is_file() else None


def route_kind(path: str, app_routes) -> str:
    """경로를 app(화면), static, api, ws, other로 나눈다."""
    if path in WS_CHANNELS:
        return "ws"
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/static/"):
        return "static"
    if path in app_routes or path.startswith("/docs/"):
        return "app"
    return "other"


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, static_dir, dataset: str = "stress", port: int = 0, websocket: str = "accept", app_routes=APP_ROUTES, ws_feeds=None,
                 vehicle: str = "differential"):
        if dataset not in mock_data.DATASETS:
            raise ValueError(f"unknown dataset: {dataset}")
        if websocket not in ("accept", "refuse"):
            raise ValueError(f"unknown websocket mode: {websocket}")
        feeds = mock_data.WEBSOCKET_FEEDS[dataset] if ws_feeds is None else tuple(ws_feeds)
        for channel in feeds:
            if channel not in mock_data.WEBSOCKET_CHANNELS:
                raise ValueError(f"unknown websocket feed: {channel}")
        self.ws_feeds = tuple(dict.fromkeys(feeds))
        self.static_root = Path(static_dir).resolve()
        if not (self.static_root / "index.html").is_file():
            raise FileNotFoundError(f"index.html not found in {self.static_root}")
        self.dataset = dataset
        self.websocket_mode = websocket
        if vehicle not in VEHICLES:
            raise ValueError(f"unknown vehicle kind: {vehicle}")
        # The dashboard server marks the page of a front-steer vehicle (DASHBOARD_VEHICLE, D-12); the mock does the same.
        self.vehicle = vehicle
        self.app_routes = tuple(dict.fromkeys(tuple(APP_ROUTES) + tuple(app_routes)))
        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self._counts = {"api": {}, "unmocked": {}, "writes": {}, "websocket": {}, "missing_static": {}}
        super().__init__((HOST, port), MockHandler)

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}"

    def count(self, group: str, key: str) -> None:
        with self._lock:
            bag = self._counts[group]
            bag[key] = bag.get(key, 0) + 1

    def stats(self) -> dict:
        with self._lock:
            return {group: dict(sorted(bag.items())) for group, bag in self._counts.items()}

    def start_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="ui-check-mock", daemon=True)
        thread.start()
        return thread

    def stop(self, thread: threading.Thread | None = None) -> None:
        self.stopping.set()
        self.shutdown()
        self.server_close()
        if thread is not None:
            thread.join(timeout=5)


class MockHandler(BaseHTTPRequestHandler):
    server_version = "UiCheckMock/1"
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, *args):  # 조용히 둔다.
        return

    # ---- 응답 보내기 -------------------------------------------------
    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

    def _send_json(self, status: int, value) -> None:
        self._send_bytes(status, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _send_plain(self, status: int, message: str) -> None:
        self._send_bytes(status, "text/plain; charset=utf-8", message.encode("utf-8"))

    def _send_file(self, target: Path) -> None:
        content_type = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            self._send_plain(404, "not found")
            return
        self._send_bytes(200, content_type, body)

    def _send_page(self, server) -> None:
        try:
            body = (server.static_root / "index.html").read_bytes()
        except OSError:
            self._send_plain(404, "not found")
            return
        if server.vehicle != "differential":
            body = body.replace(b"<html ", f'<html data-vehicle-kind="{server.vehicle}" '.encode("ascii"), 1)
        self._send_bytes(200, "text/html; charset=utf-8", body)

    # ---- GET ---------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        server: MockServer = self.server  # type: ignore[assignment]
        path = unquote(urlsplit(self.path).path)
        kind = route_kind(path, server.app_routes)
        if kind == "ws":
            self._websocket(WS_CHANNELS[path])
        elif kind == "app":
            self._send_page(server)
        elif kind == "static":
            target = resolve_static(server.static_root, path.removeprefix("/static/"))
            if target is None:
                server.count("missing_static", path)
                self._send_plain(404, "not found")
            else:
                self._send_file(target)
        elif kind == "api":
            self._api_get(path)
        elif path == "/favicon.ico":
            target = resolve_static(server.static_root, "favicon.ico")
            if target is None:
                self._send_plain(404, "not found")
            else:
                self._send_file(target)
        else:
            server.count("missing_static", path)
            self._send_plain(404, "not found")

    def _api_get(self, path: str) -> None:
        server: MockServer = self.server  # type: ignore[assignment]
        dataset = server.dataset
        if path == "/api/ping":
            # The page's link probe (NET-10): 204 without a body, as the dashboard answers.
            server.count("api", path)
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        payload = mock_data.http_payloads(dataset).get(path)
        if payload is None and path.startswith("/api/docs/"):
            payload = mock_data.doc_payload(dataset, path.removeprefix("/api/docs/"))
        if payload is None and path.startswith("/api/docker/container/"):
            rest = path.removeprefix("/api/docker/container/")
            if rest.endswith("/logs"):
                payload = mock_data.container_logs(dataset, rest.removesuffix("/logs"))
            elif "/" not in rest:
                payload = mock_data.container_detail(dataset, rest)
        if payload is None:
            server.count("unmocked", f"GET {path}")
            self._send_json(404, {"ok": False, "error": "mock_no_such_endpoint", "path": path})
            return
        server.count("api", path)
        self._send_json(200, payload)

    # ---- POST 등 쓰기 요청 -------------------------------------------
    def _refuse_write(self):
        server: MockServer = self.server  # type: ignore[assignment]
        path = unquote(urlsplit(self.path).path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0 or length > MAX_POST_BYTES:
            self.close_connection = True
            self._send_json(413, {"ok": False, "error": "mock_body_too_large"})
            return
        if length:
            self.rfile.read(length)
        server.count("writes", f"{self.command} {path}")
        self._send_json(200, {"ok": False, "error": "mock_read_only", "message": "점검용 모의 서버는 쓰기 요청을 처리하지 않습니다."})

    do_POST = _refuse_write
    do_PUT = _refuse_write
    do_DELETE = _refuse_write
    do_PATCH = _refuse_write

    # ---- 웹소켓 ------------------------------------------------------
    def _websocket(self, channel: str) -> None:
        server: MockServer = self.server  # type: ignore[assignment]
        key = self.headers.get("Sec-WebSocket-Key")
        upgrade = (self.headers.get("Upgrade") or "").lower()
        if server.websocket_mode == "refuse" or upgrade != "websocket" or not key:
            self.close_connection = True
            self._send_plain(404, "websocket not available")
            return
        server.count("websocket", channel)
        self.close_connection = True
        sock = self.connection
        try:
            sock.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                + b"Sec-WebSocket-Accept: " + websocket_accept_key(key).encode("ascii") + b"\r\n\r\n"
            )
        except OSError:
            return
        buffer = b""
        sequence = 0
        next_send = 0.0
        feeding = channel in server.ws_feeds
        period = mock_data.WEBSOCKET_PERIOD_S.get(channel, 1.0)
        poll = min(0.25, period / 2)
        sock.settimeout(poll)
        while not server.stopping.is_set():
            now = time.monotonic()
            if feeding and now >= next_send:
                next_send = now + period
                message = mock_data.websocket_message(channel, sequence + 1)
                if message is not None:
                    sequence += 1
                    try:
                        sock.settimeout(2.0)
                        sock.sendall(encode_ws_frame(json.dumps(message, separators=(",", ":")).encode("utf-8")))
                        sock.settimeout(poll)
                    except OSError:
                        return
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while True:
                frame = decode_ws_frame(buffer)
                if frame is None:
                    break
                opcode, payload, used = frame
                buffer = buffer[used:]
                try:
                    if opcode == 0x8:
                        sock.sendall(encode_ws_frame(payload[:2], 0x8))
                        return
                    if opcode == 0x9:
                        sock.sendall(encode_ws_frame(payload, 0xA))
                except OSError:
                    return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="화면 점검용 모의 서버 (127.0.0.1 전용, 읽기 전용)")
    parser.add_argument("--static", required=True, help="index.html이 들어 있는 static 폴더")
    parser.add_argument("--dataset", choices=mock_data.DATASETS, default="stress", help="모의 자료 묶음")
    parser.add_argument("--port", type=int, default=0, help="포트. 0이면 빈 포트를 고른다")
    parser.add_argument("--websocket", choices=("accept", "refuse"), default="accept", help="웹소켓 연결을 받을지 거절할지")
    parser.add_argument("--route", action="append", default=[], help="index.html을 돌려줄 화면 경로 추가(여러 번 가능)")
    parser.add_argument("--vehicle", choices=VEHICLES, default="differential", help="차량 종류(front_steer면 화면에 조향 표시를 붙인다)")
    parser.add_argument(
        "--ws-feed",
        action="append",
        choices=("lidar", "imu", "gps"),
        help="표본을 보낼 웹소켓 경로(여러 번 가능). 주지 않으면 자료 묶음의 기본값(stress: imu, gps)",
    )
    args = parser.parse_args(argv)
    try:
        server = MockServer(args.static, args.dataset, args.port, args.websocket, tuple(args.route), args.ws_feed, args.vehicle)
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"모의 서버를 띄우지 못했습니다: {error}", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"모의 서버: {server.base_url}/  자료 묶음: {args.dataset}  (Ctrl+C로 끝냄)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
    print("모의 서버를 끝냈습니다.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
