"""화면 자동 점검: 모의 서버와 화면 없는 Chrome으로 모든 경로를 여러 화면 크기에서 재고 캡처한다.

    python run_checks.py --static <static 폴더> --out <결과 폴더>

- static 폴더는 읽기만 한다. 결과는 --out 폴더에만 쓴다.
- 모의 서버는 127.0.0.1의 빈 포트에 붙고, 실행이 끝나면 서버와 브라우저를 모두 끝낸다.
- 브라우저가 127.0.0.1 밖으로 보내는 요청은 막고 그 수를 적는다.
- 필요한 것: Python 3.10 이상, playwright 파이썬 패키지, 이 PC에 깔린 Chrome.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

import mock_data  # noqa: E402
import mock_server  # noqa: E402
import summarize  # noqa: E402
import ui_metrics  # noqa: E402

TOOL_VERSION = "1.0.0"
DEFAULT_ROUTES = ["/", "/connection", "/system", "/network", "/docker", "/fan", "/research", "/docs", "/rover-control"]
DEFAULT_VIEWPORTS = ["390x844", "844x390", "820x1180", "1180x820", "1440x900"]
DEFAULT_STOP_SELECTORS = ["#roverStopButton", "[data-ui-check~='stop']", "[data-rover-drive='stop']"]
DEFAULT_STOP_TEXTS = ["STOP", "정지", "비상 정지"]
LOCAL_HOSTS = ("127.0.0.1", "localhost")
SETTLE_SAMPLE_MS = 250
SETTLE_STABLE_SAMPLES = 3

SETTLE_JS = "() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight, document.body ? document.body.getElementsByTagName('*').length : 0].join('x')"
FONTS_READY_JS = "() => (document.fonts && document.fonts.ready ? document.fonts.ready.then(() => true) : true)"
STOP_SCROLL_JS = """(config) => {
  const el = window.__uiCheckStopElement;
  if (!el) return [];
  const root = document.documentElement;
  const width = root.clientWidth || config.viewportWidth;
  const maxY = Math.max(0, root.scrollHeight - window.innerHeight);
  const samples = [];
  for (const fraction of [0, 0.25, 0.5, 0.75, 1]) {
    window.scrollTo({top: Math.round(maxY * fraction), left: 0, behavior: 'instant'});
    const r = el.getBoundingClientRect();
    samples.push({scroll_y: Math.round(window.scrollY), top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10,
      in_viewport: r.width > 0 && r.top >= -0.5 && r.bottom <= config.viewportHeight + 0.5 && r.left >= -0.5 && r.right <= width + 0.5});
  }
  window.scrollTo({top: 0, left: 0, behavior: 'instant'});
  return samples;
}"""


CANVAS_JS = """() => [...document.querySelectorAll('canvas')].map((canvas, index) => {
  const r = canvas.getBoundingClientRect();
  const shown = r.width > 0 && r.height > 0
    && (!canvas.checkVisibility || canvas.checkVisibility({checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true}));
  return {index, id: canvas.id || '', cls: canvas.getAttribute('class') || '', visible: shown,
    css_w: Math.round(r.width * 10) / 10, css_h: Math.round(r.height * 10) / 10, buffer_w: canvas.width, buffer_h: canvas.height, dpr: window.devicePixelRatio};
})"""


class ApiHold:
    """API 요청을 응답 없이 붙잡아 두었다가, 끝낼 때 한꺼번에 놓아준다."""

    def __init__(self):
        self._release = asyncio.Event()
        self._tasks = set()

    async def handler(self, route) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            await self._release.wait()
            await route.abort("aborted")
        except Exception:  # 쪽이 이미 닫혔으면 놓아줄 것이 없다.
            pass
        finally:
            self._tasks.discard(task)

    async def finish(self) -> None:
        self._release.set()
        pending = list(self._tasks)
        if pending:
            await asyncio.wait(pending, timeout=3)


async def canvas_resize_probe(page, viewport: dict, options) -> dict:
    """화면을 돌려(너비와 높이를 바꿔) 캔버스 버퍼가 새 크기를 따라오는지 본다.

    자료가 올 때마다 다시 그리는 캔버스는 언젠가는 따라오므로, 먼저 API 응답을 붙잡아 두고
    그리던 움직임이 끝나기를 기다린 다음 크기를 바꾼다. 측정이 끝나면 쪽을 닫으므로 되돌리지 않는다.
    """
    hold = ApiHold()
    await page.route("**/api/**", hold.handler)
    try:
        await page.wait_for_timeout(options.resize_freeze_ms)
        before = await page.evaluate(CANVAS_JS)
        if viewport["width"] != viewport["height"]:
            target = {"width": viewport["height"], "height": viewport["width"]}
        else:
            target = {"width": max(240, viewport["width"] - 120), "height": viewport["height"]}
        await page.set_viewport_size(target)
        await page.wait_for_timeout(options.resize_wait_ms)
        after = await page.evaluate(CANVAS_JS)
    finally:
        await hold.finish()
    return {"target": [target["width"], target["height"]], "rows": ui_metrics.canvas_resize_rows(before, after)}


def load_probe_source() -> str:
    """page_probe.js에서 머리 주석을 뗀 함수 식."""
    text = (TOOL_DIR / "page_probe.js").read_text(encoding="utf-8")
    start = text.index("(config) =>")
    return text[start:].strip()


def parse_viewport(spec: str) -> dict:
    """'390x844', '1440x900:desktop', '820x1180:mobile' 꼴. 표시가 없으면 짧은 변이 820 이하일 때 터치 기기로 본다."""
    size, _, kind = spec.partition(":")
    try:
        width_text, height_text = size.lower().split("x")
        width, height = int(width_text), int(height_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"화면 크기는 '너비x높이' 꼴이어야 합니다: {spec}") from error
    if width < 200 or height < 200 or width > 8000 or height > 8000:
        raise argparse.ArgumentTypeError(f"화면 크기가 범위를 벗어났습니다: {spec}")
    if kind not in ("", "mobile", "desktop"):
        raise argparse.ArgumentTypeError(f"화면 종류는 mobile 또는 desktop입니다: {spec}")
    mobile = kind == "mobile" or (kind == "" and min(width, height) <= 820)
    return {"name": f"{width}x{height}", "width": width, "height": height, "mobile": mobile}


def normalize_route(value: str) -> str:
    """'/rover-control', 'rover-control', 'overview'(= '/') 꼴을 받는다.

    Git Bash는 '/'로 시작하는 인자를 윈도 경로로 바꿔 버리므로, 앞의 '/' 없이 적어도 되게 했다.
    """
    text = value.strip()
    if text in ("overview", "root", "/"):
        return "/"
    if ":" in text or "\\" in text:
        raise ValueError(f"경로가 파일 경로처럼 보입니다: {value} (Git Bash에서는 '--route rover-control'처럼 앞의 '/'를 빼고 적으세요)")
    return text if text.startswith("/") else f"/{text}"


def static_fingerprint(static_dir: Path) -> dict:
    """점검한 화면 코드가 무엇이었는지 남기는 지문(파일 경로와 내용의 SHA-256)."""
    digest = hashlib.sha256()
    count = 0
    size = 0
    for path in sorted(p for p in static_dir.rglob("*") if p.is_file()):
        data = path.read_bytes()
        digest.update(path.relative_to(static_dir).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
        count += 1
        size += len(data)
    return {"files": count, "bytes": size, "sha256": digest.hexdigest()}


def git_head(static_dir: Path):
    """static 폴더가 들어 있는 저장소의 커밋. 읽기만 하며, 알 수 없으면 None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(static_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) >= 7 else None


def local_url(url: str) -> str:
    """127.0.0.1 주소는 포트가 실행마다 달라지므로 경로만 남긴다."""
    parts = urlsplit(url)
    if parts.hostname in LOCAL_HOSTS:
        return parts.path + (f"?{parts.query}" if parts.query else "")
    return url[:200]


def strip_base(text: str, base_url: str) -> str:
    return text.replace(base_url, "").replace(base_url.replace("http://", "ws://"), "")[:300]


async def settle(page, min_ms: int, max_ms: int) -> dict:
    """문서 크기와 요소 수가 연달아 같아질 때까지 기다린다."""
    started = time.monotonic()
    try:
        await page.evaluate(FONTS_READY_JS)
    except Exception:  # 글꼴 준비를 못 기다려도 측정은 계속한다.
        pass
    last, stable = None, 0
    while True:
        elapsed_ms = (time.monotonic() - started) * 1000
        signature = await page.evaluate(SETTLE_JS)
        stable = stable + 1 if signature == last else 1
        last = signature
        if elapsed_ms >= min_ms and stable >= SETTLE_STABLE_SAMPLES:
            return {"settled": True, "settle_ms": round(elapsed_ms)}
        if elapsed_ms >= max_ms:
            return {"settled": False, "settle_ms": round(elapsed_ms)}
        await page.wait_for_timeout(SETTLE_SAMPLE_MS)


async def check_page(browser, base_url: str, dataset: str, route: str, viewport: dict, options) -> dict:
    slug = ui_metrics.route_slug(route)
    record = {
        "key": ui_metrics.page_key(dataset, route, viewport["name"]),
        "dataset": dataset,
        "route": route,
        "route_slug": slug,
        "viewport": viewport,
    }
    events = {"console_errors": [], "console_warnings": 0, "page_errors": [], "failed_requests": [], "http_errors": [], "blocked_external": []}
    collecting = {"on": True}
    context = await browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        device_scale_factor=1,
        is_mobile=viewport["mobile"],
        has_touch=viewport["mobile"],
        locale="ko-KR",
        timezone_id="Asia/Seoul",
    )
    try:
        async def guard(route_handle):
            parts = urlsplit(route_handle.request.url)
            if parts.scheme in ("http", "https") and parts.hostname not in LOCAL_HOSTS:
                events["blocked_external"].append(route_handle.request.url[:200])
                await route_handle.abort("blockedbyclient")
            else:
                await route_handle.continue_()

        await context.route("**/*", guard)
        page = await context.new_page()

        def on_console(message):
            if not collecting["on"]:
                return
            if message.type == "error":
                events["console_errors"].append(strip_base(message.text, base_url))
            elif message.type == "warning":
                events["console_warnings"] += 1

        def on_request_failed(request):
            if collecting["on"]:
                events["failed_requests"].append({"method": request.method, "url": local_url(request.url), "failure": (request.failure or "")[:80]})

        def on_response(response):
            if collecting["on"] and response.status >= 400:
                events["http_errors"].append({"status": response.status, "url": local_url(response.url)})

        page.on("console", on_console)
        page.on("pageerror", lambda error: events["page_errors"].append(strip_base(str(error), base_url)) if collecting["on"] else None)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)

        started = time.monotonic()
        await page.goto(base_url + route, wait_until="load", timeout=options.page_timeout_ms)
        record["load_ms"] = round((time.monotonic() - started) * 1000)
        record.update(await settle(page, options.settle_ms, options.max_settle_ms))
        config = {
            "viewportWidth": viewport["width"],
            "viewportHeight": viewport["height"],
            "stopSelectors": options.stop_selector,
            "stopTexts": options.stop_text,
            "maxList": 40,
        }
        raw = await page.evaluate(options.probe_source, config)
        record["final_path"] = raw["document"]["path"]
        collecting["on"] = False  # 캡처와 스크롤 중에 생기는 사건은 세지 않는다.
        shots = {}
        if not options.no_screenshots:
            first = f"shots/{dataset}_{slug}_{viewport['name']}_first.png"
            await page.screenshot(path=str(options.out / first), full_page=False, type="png")
            shots["first"] = first
            if options.full_format == "png":
                full = f"shots/{dataset}_{slug}_{viewport['name']}_full.png"
                await page.screenshot(path=str(options.out / full), full_page=True, type="png")
            else:
                full = f"shots/{dataset}_{slug}_{viewport['name']}_full.jpg"
                await page.screenshot(path=str(options.out / full), full_page=True, type="jpeg", quality=options.jpeg_quality)
            shots["full"] = full
        record["screenshots"] = shots
        scroll_samples = await page.evaluate(STOP_SCROLL_JS, config)
        resize_probe = None
        if not options.no_resize_probe and raw.get("canvases"):
            resize_probe = await canvas_resize_probe(page, viewport, options)
        metrics, details = ui_metrics.page_metrics(raw, events, viewport, scroll_samples, resize_probe)
        record["metrics"] = metrics
        record["details"] = details
    finally:
        collecting["on"] = False
        await context.close()
    return record


async def check_page_with_retry(semaphore, browser, base_url, dataset, route, viewport, options) -> dict:
    async with semaphore:
        last_error = None
        for attempt in range(1, options.retries + 2):
            try:
                record = await check_page(browser, base_url, dataset, route, viewport, options)
                record["attempts"] = attempt
                return record
            except Exception as error:  # 쪽 하나의 실패가 전체 실행을 멈추지 않게 한다.
                last_error = f"{type(error).__name__}: {str(error)[:300]}"
        return {
            "key": ui_metrics.page_key(dataset, route, viewport["name"]),
            "dataset": dataset,
            "route": route,
            "route_slug": ui_metrics.route_slug(route),
            "viewport": viewport,
            "attempts": options.retries + 1,
            "error": last_error,
            "metrics": None,
        }


async def warm_up(browser, url: str, options) -> None:
    """갓 띄운 브라우저의 첫 쪽은 느려서 사건 순서가 달라질 수 있다. 재지 않는 쪽을 한 번 열어 데운다."""
    context = await browser.new_context(viewport={"width": 800, "height": 600}, device_scale_factor=1)
    try:
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=options.page_timeout_ms)
        await page.wait_for_timeout(800)
    except Exception:  # 데우기 실패는 무시한다. 실제 측정에서 다시 드러난다.
        pass
    finally:
        await context.close()


async def run_browser(options, servers: dict) -> tuple[list, dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        launch = {"headless": True}
        if options.browser_path:
            launch["executable_path"] = options.browser_path
        else:
            launch["channel"] = options.browser_channel
        browser = await playwright.chromium.launch(**launch)
        try:
            browser_info = {"name": "chromium", "channel": None if options.browser_path else options.browser_channel, "version": browser.version}
            await warm_up(browser, servers[options.datasets[0]].base_url + options.route[0], options)
            semaphore = asyncio.Semaphore(options.concurrency)
            tasks = [
                check_page_with_retry(semaphore, browser, servers[dataset].base_url, dataset, route, viewport, options)
                for dataset in options.datasets
                for route in options.route
                for viewport in options.viewports
            ]
            pages = await asyncio.gather(*tasks)
        finally:
            await browser.close()
    return list(pages), browser_info


def build_run(options, pages: list, browser_info: dict, server_stats: dict, started_at: datetime.datetime, duration_s: float) -> dict:
    return {
        "schema": ui_metrics.RUN_SCHEMA,
        "schema_version": ui_metrics.RUN_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "created_at": started_at.isoformat(timespec="seconds"),
        "duration_s": round(duration_s, 1),
        "label": options.label,
        "static_dir": str(options.static),
        "static_fingerprint": options.fingerprint,
        "static_git_head": options.git_head,
        "browser": browser_info,
        "device_scale_factor": 1,
        "concurrency": options.concurrency,
        "datasets": options.datasets,
        "routes": options.route,
        "viewports": options.viewports,
        "stop_selectors": options.stop_selector,
        "stop_texts": options.stop_text,
        "settle": {"min_ms": options.settle_ms, "max_ms": options.max_settle_ms, "sample_ms": SETTLE_SAMPLE_MS, "stable_samples": SETTLE_STABLE_SAMPLES},
        "thresholds": {
            "min_touch_px": ui_metrics.MIN_TOUCH_PX,
            "tiny_touch_px": ui_metrics.TINY_TOUCH_PX,
            "overlap_min_px": ui_metrics.OVERLAP_MIN_PX,
            "canvas_tolerance_px": ui_metrics.CANVAS_TOLERANCE_PX,
        },
        "screenshots": {"enabled": not options.no_screenshots, "first": "png", "full": options.full_format, "jpeg_quality": options.jpeg_quality},
        "resize_probe": {
            "enabled": not options.no_resize_probe,
            "method": "API 응답을 붙잡아 둔 뒤 화면의 너비와 높이를 맞바꿈",
            "freeze_ms": options.resize_freeze_ms,
            "wait_ms": options.resize_wait_ms,
        },
        "mock": {
            "data_schema": mock_data.SCHEMA,
            "data_schema_version": mock_data.SCHEMA_VERSION,
            "websocket_mode": options.websocket,
            "websocket_feeds": {name: list(mock_data.WEBSOCKET_FEEDS[name]) for name in options.datasets},
            "websocket_behavior": {name: mock_data.WEBSOCKET_BEHAVIOR[name] for name in options.datasets},
            "panels_without_data": {name: mock_data.PANELS_WITHOUT_DATA[name] for name in options.datasets},
            "requests": server_stats,
        },
        "summary": ui_metrics.summarize(pages),
        "pages": pages,
    }


def write_json(path: Path, value) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="대시보드 화면 자동 점검(모의 서버 + 화면 없는 Chrome)")
    parser.add_argument("--static", required=True, type=Path, help="점검할 static 폴더(읽기만 함)")
    parser.add_argument("--out", required=True, type=Path, help="결과 JSON과 캡처를 쓸 폴더")
    parser.add_argument("--dataset", choices=("empty", "stress", "both"), default="both", help="모의 자료 묶음")
    parser.add_argument("--route", action="append", help=f"점검할 경로(여러 번 가능). 기본: {' '.join(DEFAULT_ROUTES)}")
    parser.add_argument("--viewport", action="append", type=parse_viewport, dest="viewports", help=f"화면 크기 '너비x높이[:mobile|:desktop]'(여러 번 가능). 기본: {' '.join(DEFAULT_VIEWPORTS)}")
    parser.add_argument("--stop-selector", action="append", help="STOP 조작 요소를 찾는 CSS 선택자(여러 번 가능, 앞의 것이 우선)")
    parser.add_argument("--stop-text", action="append", help="선택자로 못 찾을 때 버튼 글자로 찾는 값(여러 번 가능)")
    parser.add_argument("--concurrency", type=int, default=3, help="동시에 여는 쪽 수")
    parser.add_argument("--retries", type=int, default=1, help="쪽을 열지 못했을 때 다시 해 보는 횟수")
    parser.add_argument("--settle-ms", type=int, default=1500, help="측정 전에 기다리는 최소 시간")
    parser.add_argument("--max-settle-ms", type=int, default=6000, help="화면이 가라앉기를 기다리는 최대 시간")
    parser.add_argument("--page-timeout-ms", type=int, default=30000, help="쪽 하나를 여는 제한 시간")
    parser.add_argument("--full-format", choices=("jpeg", "png"), default="jpeg", help="전체 쪽 캡처 형식")
    parser.add_argument("--jpeg-quality", type=int, default=70, help="전체 쪽 JPEG 품질")
    parser.add_argument("--no-screenshots", action="store_true", help="캡처 없이 수치만 잰다")
    parser.add_argument("--no-resize-probe", action="store_true", help="화면을 돌린 뒤 캔버스가 따라오는지 보는 검사를 건너뛴다")
    parser.add_argument("--resize-freeze-ms", type=int, default=1300, help="화면을 돌리기 전에 API 응답을 붙잡아 두고 기다리는 시간")
    parser.add_argument("--resize-wait-ms", type=int, default=600, help="화면을 돌린 뒤 캔버스를 다시 재기까지 기다리는 시간")
    parser.add_argument("--websocket", choices=("accept", "refuse"), default="accept", help="모의 서버가 웹소켓을 받을지 거절할지")
    parser.add_argument("--browser-channel", default="chrome", help="Playwright 브라우저 채널(기본: 깔려 있는 Chrome)")
    parser.add_argument("--browser-path", help="브라우저 실행 파일 경로(주면 채널 대신 씀)")
    parser.add_argument("--json-name", default="ui_check.json", help="결과 JSON 파일 이름")
    parser.add_argument("--label", default="", help="결과에 적어 둘 설명(예: 'main 3372139')")
    options = parser.parse_args(argv)
    options.static = options.static.resolve()
    options.out = options.out.resolve()
    options.datasets = ["empty", "stress"] if options.dataset == "both" else [options.dataset]
    options.route = options.route or list(DEFAULT_ROUTES)
    options.viewports = options.viewports or [parse_viewport(spec) for spec in DEFAULT_VIEWPORTS]
    options.stop_selector = options.stop_selector or list(DEFAULT_STOP_SELECTORS)
    options.stop_text = options.stop_text or list(DEFAULT_STOP_TEXTS)
    if not (options.static / "index.html").is_file():
        parser.error(f"index.html이 없습니다: {options.static}")
    if options.out == options.static or options.static in options.out.parents:
        parser.error("결과 폴더를 static 폴더 안에 둘 수 없습니다")
    try:
        options.route = list(dict.fromkeys(normalize_route(route) for route in options.route))
    except ValueError as error:
        parser.error(str(error))
    if options.concurrency < 1 or options.concurrency > 8:
        parser.error("--concurrency는 1에서 8 사이여야 합니다")
    if len({viewport["name"] for viewport in options.viewports}) != len(options.viewports):
        parser.error("같은 화면 크기를 두 번 줄 수 없습니다")
    return options


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    options = parse_args(argv)
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("playwright 파이썬 패키지가 없습니다. README_KO.md의 '필요한 것'을 보세요.", file=sys.stderr)
        return 2
    options.probe_source = load_probe_source()
    options.fingerprint = static_fingerprint(options.static)
    options.git_head = git_head(options.static)
    (options.out / "shots").mkdir(parents=True, exist_ok=True)

    started_at = datetime.datetime.now().astimezone()
    started = time.monotonic()
    servers, threads = {}, {}
    pages, browser_info, server_stats = [], {}, {}
    try:
        for dataset in options.datasets:
            servers[dataset] = mock_server.MockServer(options.static, dataset, 0, options.websocket, tuple(options.route))
            threads[dataset] = servers[dataset].start_in_thread()
        pages, browser_info = asyncio.run(run_browser(options, servers))
    except Exception as error:
        print(f"점검을 끝내지 못했습니다: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    finally:
        for dataset, server in servers.items():
            server_stats[dataset] = server.stats()
            server.stop(threads.get(dataset))

    run = build_run(options, pages, browser_info, server_stats, started_at, time.monotonic() - started)
    target = options.out / options.json_name
    write_json(target, run)

    failed = [page for page in pages if page.get("error")]
    print(f"화면 점검 끝: {len(pages)}쪽 · {run['duration_s']}초 · Chrome {browser_info.get('version', '?')}")
    print(f"결과: {target}")
    print(summarize.overview(run))
    notable = summarize.notable_pages(run)
    if notable:
        print("눈여겨볼 쪽:")
        for line in notable:
            print(f"- {line}")
    unmocked = {name: stats.get("unmocked") for name, stats in server_stats.items() if stats.get("unmocked")}
    if unmocked:
        print(f"모의 응답이 없는 API 요청: {json.dumps(unmocked, ensure_ascii=False)}")
    if failed:
        print(f"열지 못한 쪽 {len(failed)}개. 결과 JSON의 error를 보세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
