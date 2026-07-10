#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Non-destructive browser validation for deployed Rover Control archives.")
    parser.add_argument("--base-url", default="https://bts.sarihub.com")
    parser.add_argument("--recording-id", default="20260708T053333Z_both")
    parser.add_argument("--output-dir", default="runtime/analysis/operational_readiness/dashboard_ui")
    return parser.parse_args()


def canvas_stats(page, selector: str) -> dict[str, int]:
    return page.eval_on_selector(
        selector,
        """canvas => {
          const ctx = canvas.getContext('2d');
          const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
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

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(f"{args.base_url.rstrip('/')}/rover-control", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.querySelectorAll('#roverArchiveList button').length > 0", timeout=30_000)
        archive = page.locator("#roverArchiveList button", has_text=args.recording_id).first
        archive.click()
        page.wait_for_function(
            "document.querySelector('#roverArchiveCameraImage')?.getAttribute('src')?.length > 10",
            timeout=30_000,
        )
        page.wait_for_function(
            "document.querySelector('#roverOrchardMapFusionStats')?.textContent?.trim() !== '--'",
            timeout=30_000,
        )
        page.wait_for_timeout(800)

        error_text = page.locator("body").inner_text()
        report["checks"]["camera_ai_available"] = "Camera AI unavailable" not in error_text and "AI Vision\nERROR" not in error_text
        report["checks"]["archive_camera_loaded"] = bool(page.locator("#roverArchiveCameraImage").get_attribute("src"))
        report["checks"]["map_layer_stats"] = {
            layer: page.locator(f"#roverOrchardMap{layer.title()}Stats").inner_text()
            for layer in ("lidar", "camera", "fusion")
        }
        start_map = page.locator("#roverOrchardMapFusionCanvas").evaluate("canvas => canvas.toDataURL()")
        page.locator(".orchard-map-panel").screenshot(path=str(output / "three_layer_map_start.png"))

        initial_time = float(page.locator("#roverArchiveScrubber").input_value())
        page.locator("#roverArchivePlayButton").click()
        page.wait_for_timeout(5200)
        played_time = float(page.locator("#roverArchiveScrubber").input_value())
        map_time = float(page.locator("#roverOrchardMapScrubber").input_value())
        page.locator("#roverArchivePlayButton").click()
        report["checks"]["playback_progress"] = round(played_time - initial_time, 3)
        report["checks"]["map_timeline_sync_error_s"] = round(abs(played_time - map_time), 4)

        page.locator("#roverOrchardMapScrubber").evaluate(
            """(slider) => { slider.value = Math.min(45, Number(slider.max)); slider.dispatchEvent(new Event('input', { bubbles: true })); }"""
        )
        page.wait_for_timeout(500)
        report["checks"]["map_seek_sync_error_s"] = round(
            abs(
                float(page.locator("#roverArchiveScrubber").input_value())
                - float(page.locator("#roverOrchardMapScrubber").input_value())
            ),
            4,
        )
        end_map = page.locator("#roverOrchardMapFusionCanvas").evaluate("canvas => canvas.toDataURL()")
        report["checks"]["map_pose_changes_with_timeline"] = start_map != end_map
        report["checks"]["canvas"] = {
            selector: canvas_stats(page, selector)
            for selector in (
                "#roverArchiveLidarCanvas",
                "#roverArchiveCameraOverlayCanvas",
                "#roverOrchardMapLidarCanvas",
                "#roverOrchardMapCameraCanvas",
                "#roverOrchardMapFusionCanvas",
            )
        }

        report["checks"]["desktop_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        page.locator(".archive-preview").screenshot(path=str(output / "archive_playback_desktop.png"))
        page.locator(".orchard-map-panel").screenshot(path=str(output / "three_layer_map_desktop.png"))

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        report["checks"]["mobile_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        page.screenshot(path=str(output / "rover_control_mobile.png"), full_page=False)
        report["console_errors"] = console_errors
        browser.close()

    canvas_ok = all(item["colors"] >= 3 and item["opaque"] > 0 for item in report["checks"]["canvas"].values())
    report["ok"] = bool(
        report["checks"]["camera_ai_available"]
        and report["checks"]["archive_camera_loaded"]
        and canvas_ok
        and report["checks"]["playback_progress"] >= 4.0
        and report["checks"]["map_timeline_sync_error_s"] <= 0.08
        and report["checks"]["map_seek_sync_error_s"] <= 0.08
        and report["checks"]["map_pose_changes_with_timeline"]
        and report["checks"]["desktop_overflow_px"] <= 2
        and report["checks"]["mobile_overflow_px"] <= 2
        and not console_errors
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
