#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import termios
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "event": f"rover_bridge.{event}", **fields}, ensure_ascii=False), flush=True)


def write_status(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


class WaveRoverBridge(Node):
    def __init__(self, args: argparse.Namespace):
        super().__init__("ubts_wave_rover_bridge")
        self.args = args
        self.transport = args.transport.strip().lower()
        self.serial_fd: int | None = None
        self.last_cmd: Twist | None = None
        self.last_cmd_at = 0.0
        self.last_sent: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.stop_sent = False
        self.status_pub = self.create_publisher(String, args.status_topic, 10)
        self.create_subscription(Twist, args.cmd_topic, self.cmd_vel_callback, 10)
        self.create_timer(1.0 / max(1.0, args.command_hz), self.command_timer)
        self.create_timer(1.0 / max(0.2, args.status_hz), self.status_timer)
        log(
            "start",
            transport=self.transport,
            serial_port=args.serial_port,
            serial_baudrate=args.serial_baudrate,
            base_url=args.base_url,
            cmd_topic=args.cmd_topic,
            status_topic=args.status_topic,
        )

    def cmd_vel_callback(self, msg: Twist) -> None:
        self.last_cmd = msg
        self.last_cmd_at = time.time()
        self.stop_sent = False

    def command_timer(self) -> None:
        now = time.time()
        if self.last_cmd is None or now - self.last_cmd_at > self.args.cmd_timeout_s:
            if not self.stop_sent:
                self.send_speed(0.0, 0.0)
                self.stop_sent = True
            return
        left, right = self.twist_to_wheels(self.last_cmd)
        self.send_speed(left, right)

    def twist_to_wheels(self, msg: Twist) -> tuple[float, float]:
        linear = float(msg.linear.x)
        angular = float(msg.angular.z)
        left_mps = linear - angular * self.args.wheel_base_m / 2.0
        right_mps = linear + angular * self.args.wheel_base_m / 2.0
        left = clamp((left_mps / self.args.max_linear_mps) * 0.5, -self.args.speed_limit_fraction, self.args.speed_limit_fraction)
        right = clamp((right_mps / self.args.max_linear_mps) * 0.5, -self.args.speed_limit_fraction, self.args.speed_limit_fraction)
        return left, right

    def send_speed(self, left: float, right: float) -> None:
        command = {"T": 1, "L": round(left, 3), "R": round(right, 3)}
        if self.transport == "http":
            self.send_http_command(command)
        else:
            self.send_serial_command(command)

    def send_serial_command(self, command: dict[str, float | int]) -> None:
        payload = json.dumps(command, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            fd = self.ensure_serial()
            os.write(fd, payload)
            self.last_error = None
            self.last_sent = {
                "transport": "serial",
                "serial_port": self.args.serial_port,
                "left": float(command.get("L", 0.0)),
                "right": float(command.get("R", 0.0)),
                "at": time.time(),
            }
        except Exception as exc:
            self.last_error = str(exc)
            self.close_serial()
            self.get_logger().warn(f"WAVE ROVER serial command failed: {exc}", throttle_duration_sec=2.0)

    def ensure_serial(self) -> int:
        if self.serial_fd is not None:
            return self.serial_fd
        fd = os.open(self.args.serial_port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        speed = getattr(termios, f"B{self.args.serial_baudrate}", termios.B115200)
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        attrs[4] = speed
        attrs[5] = speed
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        self.serial_fd = fd
        log("serial_opened", serial_port=self.args.serial_port, baudrate=self.args.serial_baudrate)
        return fd

    def close_serial(self) -> None:
        if self.serial_fd is None:
            return
        try:
            os.close(self.serial_fd)
        except OSError:
            pass
        self.serial_fd = None

    def send_http_command(self, command: dict[str, float | int]) -> None:
        payload = json.dumps(command, separators=(",", ":"))
        url = f"{self.args.base_url.rstrip('/')}/js?json={urllib.parse.quote(payload)}"
        try:
            with urllib.request.urlopen(url, timeout=0.5) as res:
                body = res.read(160).decode("utf-8", errors="replace")
            self.last_error = None
            self.last_sent = {
                "transport": "http",
                "left": float(command.get("L", 0.0)),
                "right": float(command.get("R", 0.0)),
                "at": time.time(),
                "http_status": int(res.status),
            }
            if body:
                self.get_logger().debug(f"WAVE ROVER response: {body[:120]}")
        except Exception as exc:
            self.last_error = str(exc)
            self.get_logger().warn(f"WAVE ROVER HTTP command failed: {exc}", throttle_duration_sec=2.0)

    def status_timer(self) -> None:
        now = time.time()
        connected = self.last_sent is not None and now - float(self.last_sent.get("at", 0.0)) < 3.0 and not self.last_error
        payload = {
            "type": "rover_bridge_status",
            "updated_at": now,
            "alive": True,
            "connected": connected,
            "state": "connected" if connected else ("error" if self.last_error else "idle"),
            "transport": self.transport,
            "serial_port": self.args.serial_port,
            "serial_baudrate": self.args.serial_baudrate,
            "base_url": self.args.base_url,
            "cmd_topic": self.args.cmd_topic,
            "status_topic": self.args.status_topic,
            "last_command": self.last_sent,
            "last_error": self.last_error,
            "mode": "cmd_vel_to_wave_rover_json",
        }
        msg = String()
        msg.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.status_pub.publish(msg)
        write_status(Path(self.args.status_file), payload)

    def destroy_node(self):
        self.close_serial()
        super().destroy_node()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge ROS2 /cmd_vel to Waveshare WAVE ROVER JSON control.")
    parser.add_argument("--transport", default=os.environ.get("ROVER_TRANSPORT", "serial"), choices=["serial", "http"])
    parser.add_argument("--serial-port", default=os.environ.get("ROVER_SERIAL_PORT", "/dev/ttyTHS1"))
    parser.add_argument("--serial-baudrate", type=int, default=int(os.environ.get("ROVER_SERIAL_BAUDRATE", "115200")))
    parser.add_argument("--base-url", default=os.environ.get("ROVER_BASE_URL", "http://192.168.4.1"))
    parser.add_argument("--cmd-topic", default=os.environ.get("ROVER_CMD_TOPIC", "/cmd_vel"))
    parser.add_argument("--status-topic", default=os.environ.get("ROVER_STATUS_TOPIC", "/rover/status"))
    parser.add_argument("--status-file", default=os.environ.get("ROVER_BRIDGE_STATUS_FILE", "/app/runtime/rover_bridge_status.json"))
    parser.add_argument("--wheel-base-m", type=float, default=float(os.environ.get("ROVER_WHEEL_BASE_M", "0.19")))
    parser.add_argument("--max-linear-mps", type=float, default=max(0.1, float(os.environ.get("ROVER_MAX_LINEAR_MPS", "1.25"))))
    parser.add_argument("--speed-limit-fraction", type=float, default=max(0.05, min(0.5, float(os.environ.get("ROVER_SPEED_LIMIT_FRACTION", "0.5")))))
    parser.add_argument("--command-hz", type=float, default=float(os.environ.get("ROVER_COMMAND_HZ", "5.0")))
    parser.add_argument("--status-hz", type=float, default=float(os.environ.get("ROVER_STATUS_HZ", "1.0")))
    parser.add_argument("--cmd-timeout-s", type=float, default=float(os.environ.get("ROVER_CMD_TIMEOUT_S", "2.0")))
    args = parser.parse_args()
    rclpy.init()
    node = WaveRoverBridge(args)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
