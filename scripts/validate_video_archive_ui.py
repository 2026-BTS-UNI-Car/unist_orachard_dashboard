#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a native camera-only archive without touching rover controls.")
    parser.add_argument("--base-url", default="https://bts.sarihub.com")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def canvas_stats(page, selector: str) -> dict[str, int]:
    return page.eval_on_selector(
        selector,
        """canvas => {
          const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
          const colors = new Set();
          let opaque = 0;
          const stride = Math.max(4, Math.floor(data.length / 12000 / 4) * 4);
          for (let i = 0; i < data.length; i += stride) {
            if (data[i + 3] > 0) opaque += 1;
            colors.add(`${data[i] >> 4}:${data[i + 1] >> 4}:${data[i + 2] >> 4}:${data[i + 3] >> 4}`);
          }
          return { colors: colors.size, opaque };
        }""",
    )


def main() -> int:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"ok": False, "recording_id": args.recording_id, "checks": {}}
    base = args.base_url.rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(f"{base}/rover-control", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.querySelectorAll('#roverArchiveList button').length > 0", timeout=30_000)
        page.locator("#roverArchiveList button", has_text=args.recording_id).first.click()
        page.wait_for_function(
            """() => {
              const video = document.querySelector('#roverArchiveCameraVideo');
              return Boolean(video?.getAttribute('src')) && video.readyState >= 1 && video.videoWidth > 0;
            }""",
            timeout=60_000,
        )
        page.wait_for_timeout(800)

        report["checks"]["video"] = page.locator("#roverArchiveCameraVideo").evaluate(
            """video => ({
              src: video.getAttribute('src'),
              ready_state: video.readyState,
              duration_s: video.duration,
              width: video.videoWidth,
              height: video.videoHeight,
              paused: video.paused,
            })"""
        )
        report["checks"]["kind_label"] = page.locator("#roverArchiveList button.active span").inner_text()
        report["checks"]["camera_ai_available"] = "Camera AI unavailable" not in page.locator("body").inner_text()

        overlay_best = {"colors": 0, "opaque": 0, "time_s": 0.0}
        duration = float(report["checks"]["video"]["duration_s"])
        for time_s in [0.0, 3.0, 6.0, 9.0, 12.0, min(18.0, duration * 0.75)]:
            page.evaluate("value => setArchiveTime(value, { pause: true })", time_s)
            page.wait_for_timeout(500)
            stats = canvas_stats(page, "#roverArchiveCameraOverlayCanvas")
            if stats["colors"] > overlay_best["colors"]:
                overlay_best = {**stats, "time_s": time_s}
        report["checks"]["overlay_canvas"] = overlay_best

        page.evaluate("value => setArchiveTime(value, { pause: true })", 0.0)
        page.wait_for_function(
            """() => {
              const video = document.querySelector('#roverArchiveCameraVideo');
              return !video.seeking && Math.abs(video.currentTime) < 0.08;
            }""",
            timeout=10_000,
        )
        playback_started = time.perf_counter()
        page.locator("#roverArchivePlayButton").click()
        page.wait_for_function(
            """() => {
              const video = document.querySelector('#roverArchiveCameraVideo');
              return !video.paused && video.currentTime > 0.08;
            }""",
            timeout=10_000,
        )
        startup_ms = (time.perf_counter() - playback_started) * 1000
        playback_start_s = page.locator("#roverArchiveCameraVideo").evaluate("video => video.currentTime")
        page.wait_for_timeout(3200)
        playback = page.evaluate(
            """() => ({
              dashboard_s: state.archivePlayback.timeS,
              video_s: document.querySelector('#roverArchiveCameraVideo').currentTime,
              playing: state.archivePlayback.playing,
            })"""
        )
        report["checks"]["playback"] = {
            **playback,
            "startup_ms": round(startup_ms, 2),
            "progress_s": float(playback["video_s"]) - float(playback_start_s),
            "sync_error_s": abs(float(playback["dashboard_s"]) - float(playback["video_s"])),
        }
        page.locator("#roverArchivePlayButton").click()

        seek_target = duration * 0.62
        page.evaluate("value => setArchiveTime(value, { pause: true })", seek_target)
        page.wait_for_timeout(700)
        seek = page.evaluate(
            """() => ({
              dashboard_s: state.archivePlayback.timeS,
              video_s: document.querySelector('#roverArchiveCameraVideo').currentTime,
            })"""
        )
        report["checks"]["seek"] = {
            **seek,
            "target_s": seek_target,
            "sync_error_s": abs(float(seek["dashboard_s"]) - float(seek["video_s"])),
        }

        range_response = page.request.get(
            f"{base}/api/recordings/{args.recording_id}/video",
            headers={"Range": "bytes=0-1023"},
            timeout=30_000,
        )
        report["checks"]["range"] = {
            "status": range_response.status,
            "bytes": len(range_response.body()),
            "accept_ranges": range_response.headers.get("accept-ranges"),
            "content_range": range_response.headers.get("content-range"),
        }
        download_response = page.request.head(
            f"{base}/api/recordings/{args.recording_id}/download",
            timeout=30_000,
        )
        report["checks"]["download_head_status"] = download_response.status
        report["checks"]["desktop_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        page.locator(".archive-preview").screenshot(path=str(output / "video_archive_desktop.png"))

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        report["checks"]["mobile_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        page.screenshot(path=str(output / "video_archive_mobile.png"), full_page=False)
        report["console_errors"] = console_errors
        browser.close()

    video = report["checks"]["video"]
    playback = report["checks"]["playback"]
    seek = report["checks"]["seek"]
    range_check = report["checks"]["range"]
    report["ok"] = bool(
        str(report["checks"]["kind_label"]).lower() == "camera"
        and report["checks"]["camera_ai_available"]
        and video["ready_state"] >= 1
        and video["duration_s"] > 20
        and video["width"] > 0
        and video["height"] > 0
        and report["checks"]["overlay_canvas"]["colors"] >= 2
        and playback["startup_ms"] <= 3000
        and playback["progress_s"] >= 2.8
        and playback["sync_error_s"] <= 0.18
        and seek["sync_error_s"] <= 0.25
        and range_check["status"] == 206
        and range_check["bytes"] == 1024
        and range_check["accept_ranges"] == "bytes"
        and report["checks"]["download_head_status"] == 200
        and report["checks"]["desktop_overflow_px"] <= 2
        and report["checks"]["mobile_overflow_px"] <= 2
        and not console_errors
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
