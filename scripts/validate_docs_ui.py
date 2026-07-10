#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate deployed docs scrolling, formulas and report assets.")
    parser.add_argument("--base-url", default="https://bts.sarihub.com")
    parser.add_argument("--output-dir", default="runtime/analysis/operational_readiness/docs_ui")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"ok": False, "checks": {}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        console_errors: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.goto(
            f"{args.base_url.rstrip('/')}/docs/orchard-perception-final-report",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        page.wait_for_function("document.querySelectorAll('#docMarkdown img').length >= 5", timeout=30_000)
        scroll_height = page.locator("#docMarkdown").evaluate("element => element.scrollHeight")
        for offset in range(0, int(scroll_height) + 800, 700):
            page.locator("#docMarkdown").evaluate("(element, value) => { element.scrollTop = value; }", offset)
            page.wait_for_timeout(120)
        page.locator("#docMarkdown").evaluate("element => { element.scrollTop = 0; }")
        page.wait_for_function(
            "[...document.querySelectorAll('#docMarkdown img')].every(img => img.complete && img.naturalWidth > 0)",
            timeout=30_000,
        )
        page.wait_for_timeout(500)

        checks = report["checks"]
        checks["title"] = page.locator("#docTitle").inner_text()
        checks["image_count"] = page.locator("#docMarkdown img").count()
        checks["math_block_count"] = page.locator("#docMarkdown .math-block").count()
        checks["broken_images"] = page.locator("#docMarkdown img").evaluate_all(
            "imgs => imgs.filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src)"
        )
        checks["asset_routes"] = page.locator("#docMarkdown img").evaluate_all(
            "imgs => imgs.map(img => new URL(img.src).pathname)"
        )
        checks["desktop_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        scroll = page.evaluate(
            """() => {
              const index = document.querySelector('.page[data-page="/docs"] .docs-index');
              const body = document.querySelector('.page[data-page="/docs"] .markdown-body');
              const indexStyle = getComputedStyle(index);
              const bodyStyle = getComputedStyle(body);
              index.scrollTop = 160;
              const bodyBefore = body.scrollTop;
              body.scrollTop = 720;
              return {
                indexOverflow: indexStyle.overflowY,
                bodyOverflow: bodyStyle.overflowY,
                indexScrollable: index.scrollHeight > index.clientHeight,
                bodyScrollable: body.scrollHeight > body.clientHeight,
                indexTop: index.scrollTop,
                bodyTop: body.scrollTop,
                independent: bodyBefore === 0 && index.scrollTop > 0 && body.scrollTop > 0
              };
            }"""
        )
        checks["desktop_scroll"] = scroll
        page.screenshot(path=str(output / "final_report_desktop.png"), full_page=False)

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        checks["mobile_overflow_px"] = page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth)"
        )
        checks["mobile_docs_overflow"] = page.evaluate(
            "getComputedStyle(document.querySelector('.page[data-page=\"/docs\"] .markdown-body')).overflowY"
        )
        page.screenshot(path=str(output / "final_report_mobile.png"), full_page=False)
        report["console_errors"] = console_errors
        browser.close()

    routes_ok = all(path.startswith("/docs-assets/") for path in report["checks"]["asset_routes"])
    scroll = report["checks"]["desktop_scroll"]
    report["ok"] = bool(
        "최종 보고서" in report["checks"]["title"]
        and report["checks"]["image_count"] >= 5
        and report["checks"]["math_block_count"] >= 5
        and not report["checks"]["broken_images"]
        and routes_ok
        and report["checks"]["desktop_overflow_px"] <= 2
        and report["checks"]["mobile_overflow_px"] <= 2
        and scroll["indexOverflow"] == "auto"
        and scroll["bodyOverflow"] == "auto"
        and scroll["indexScrollable"]
        and scroll["bodyScrollable"]
        and scroll["independent"]
        and report["checks"]["mobile_docs_overflow"] == "visible"
        and not console_errors
    )
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
