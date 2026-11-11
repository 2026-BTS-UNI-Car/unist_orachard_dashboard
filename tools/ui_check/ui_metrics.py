"""화면 측정값을 수치로 줄이는 순수 함수 모음.

브라우저도 파일도 쓰지 않는다. page_probe.js가 돌려준 원본(raw)과 브라우저 사건 기록을 받아
쪽마다 한 묶음의 수치(metrics)와 근거 목록(details)을 만든다.
"""
from __future__ import annotations

RUN_SCHEMA = "ubts.ui_check.run"
RUN_SCHEMA_VERSION = 1

MIN_TOUCH_PX = 44.0  # 누름 영역 최소 크기
TINY_TOUCH_PX = 24.0  # 이보다 작으면 아주 작은 누름 영역으로 따로 센다
OVERLAP_MIN_PX = 2.0  # 가로와 세로 모두 이만큼 넘게 겹쳐야 겹침으로 본다
CANVAS_TOLERANCE_PX = 2.0  # 캔버스 표시 크기와 버퍼 크기의 허용 차이
PINNED_LAYERS = ("fixed", "sticky")
DETAIL_LIMIT = 40


def intersection(a: dict, b: dict) -> tuple[float, float]:
    """두 상자({x, y, w, h})가 겹친 너비와 높이. 떨어져 있으면 0 이하가 나온다."""
    width = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    height = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return width, height


def boxes_overlap(a: dict, b: dict, min_px: float = OVERLAP_MIN_PX) -> bool:
    width, height = intersection(a, b)
    return width > min_px and height > min_px


def layer_class(element: dict) -> str:
    """화면에 붙어 있는 층(pinned)인지 문서와 함께 흐르는 층(flow)인지."""
    return "pinned" if element.get("layer") in PINNED_LAYERS else "flow"


def related(index_a: int, index_b: int, elements: list) -> bool:
    """부모·자식 사이이거나 같은 label 안에 있으면 겹침 검사에서 뺀다."""
    a, b = elements[index_a], elements[index_b]
    if index_b in a.get("ancestors", ()) or index_a in b.get("ancestors", ()):
        return True
    label_a, label_b = a.get("label"), b.get("label")
    return label_a is not None and label_a == label_b


def find_overlaps(elements: list, min_px: float = OVERLAP_MIN_PX) -> dict:
    """조작 요소끼리의 겹침.

    same_layer : 같은 층에서 겹친 쌍(실제 겹침).
    cross_layer: 화면에 붙은 막대와 그 아래로 흘러가는 내용의 쌍(참고용).
    """
    same_layer, cross_layer = [], []
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            if related(i, j, elements):
                continue
            width, height = intersection(elements[i], elements[j])
            if width <= min_px or height <= min_px:
                continue
            row = {
                "a": elements[i].get("sel", ""),
                "a_text": elements[i].get("text", ""),
                "b": elements[j].get("sel", ""),
                "b_text": elements[j].get("text", ""),
                "w": round(width, 1),
                "h": round(height, 1),
            }
            if layer_class(elements[i]) == layer_class(elements[j]):
                same_layer.append(row)
            else:
                cross_layer.append(row)
    return {"same_layer": same_layer, "cross_layer": cross_layer}


def hit_box(element: dict) -> dict:
    return element.get("hit") or element


def small_targets(elements: list, min_px: float = MIN_TOUCH_PX) -> dict:
    """누름 영역이 min_px보다 작은 조작 요소. 문장 속 링크는 따로 센다."""
    small, inline_links = [], []
    for element in elements:
        box = hit_box(element)
        if box["w"] >= min_px and box["h"] >= min_px:
            continue
        row = {
            "sel": element.get("sel", ""),
            "text": element.get("text", ""),
            "w": box["w"],
            "h": box["h"],
            "hit_source": element.get("hit_source", "self"),
            "disabled": bool(element.get("disabled")),
        }
        (inline_links if element.get("inline_text_link") else small).append(row)
    return {"small": small, "inline_links": inline_links}


def canvas_mismatch(canvas: dict, tolerance: float = CANVAS_TOLERANCE_PX):
    """표시 크기와 (버퍼 크기 / 화소 배율)의 차이가 허용값을 넘으면 그 차이를 돌려준다."""
    dpr = float(canvas.get("dpr") or 1.0) or 1.0
    delta_w = canvas["buffer_w"] / dpr - canvas["css_w"]
    delta_h = canvas["buffer_h"] / dpr - canvas["css_h"]
    if abs(delta_w) <= tolerance and abs(delta_h) <= tolerance:
        return None
    return {
        "sel": canvas.get("sel", ""),
        "css": [canvas["css_w"], canvas["css_h"]],
        "buffer": [canvas["buffer_w"], canvas["buffer_h"]],
        "dpr": dpr,
        "delta_w": round(delta_w, 1),
        "delta_h": round(delta_h, 1),
    }


def canvas_resize_rows(before: list, after: list, tolerance: float = CANVAS_TOLERANCE_PX) -> list:
    """화면 크기를 바꾼 뒤(자료 응답은 멈춘 상태) 캔버스 버퍼가 새 표시 크기를 따라왔는지.

    before/after는 문서 순서 index로 짝짓는다. 표시 크기가 바뀌었는데 버퍼가 그대로면 stale이다.
    """
    previous = {row["index"]: row for row in before}
    rows = []
    for row in after:
        if not row.get("visible"):
            continue
        old = previous.get(row["index"])
        was_visible = bool(old and old.get("visible"))
        changed = (not was_visible) or abs(old["css_w"] - row["css_w"]) > tolerance or abs(old["css_h"] - row["css_h"]) > tolerance
        mismatch = canvas_mismatch(row, tolerance)
        name = f"canvas#{row['id']}" if row.get("id") else f"canvas[{row['index']}]" + (f".{row['cls'].split()[0]}" if row.get("cls") else "")
        rows.append(
            {
                "sel": name,
                "before_css": [old["css_w"], old["css_h"]] if was_visible else None,
                "after_css": [row["css_w"], row["css_h"]],
                "buffer": [row["buffer_w"], row["buffer_h"]],
                "css_changed": changed,
                "stale": bool(changed and mismatch),
            }
        )
    return rows


def inside_viewport(box: dict, viewport_w: float, viewport_h: float, slack: float = 0.5) -> bool:
    """문서 기준 상자가 스크롤 0일 때 첫 화면 안에 온전히 들어오는지."""
    return (
        box["x"] >= -slack
        and box["y"] >= -slack
        and box["x"] + box["w"] <= viewport_w + slack
        and box["y"] + box["h"] <= viewport_h + slack
    )


def stop_summary(stop_raw: dict, scroll_samples: list, viewport_w: float, viewport_h: float) -> dict:
    """STOP 조작 요소의 위치 요약.

    in_first_viewport : 페이지를 열자마자(스크롤 없이) 온전히 보이는가.
    always_in_viewport: 여러 스크롤 위치에서 계속 화면 안에 있는가.
    """
    candidates = (stop_raw or {}).get("candidates") or []
    primary_index = (stop_raw or {}).get("primary_index", -1)
    summary = {
        "found": bool(candidates),
        "visible": False,
        "selector": None,
        "top_px": None,
        "width_px": None,
        "height_px": None,
        "layer": None,
        "in_first_viewport": False,
        "always_in_viewport": False,
        "hit_test": None,
        "candidates": len(candidates),
        "visible_candidates": sum(1 for item in candidates if item.get("visible")),
    }
    if primary_index is None or primary_index < 0 or primary_index >= len(candidates):
        return summary
    primary = candidates[primary_index]
    summary.update(
        visible=True,
        selector=primary.get("selector") or primary.get("sel"),
        top_px=primary["y"],
        width_px=primary["w"],
        height_px=primary["h"],
        layer=primary.get("layer"),
        hit_test=primary.get("hit_test"),
        in_first_viewport=inside_viewport(primary, viewport_w, viewport_h),
    )
    if scroll_samples:
        summary["always_in_viewport"] = summary["in_first_viewport"] and all(sample.get("in_viewport") for sample in scroll_samples)
    return summary


def screens(page_height: float, viewport_h: float) -> float:
    return round(page_height / viewport_h, 2) if viewport_h else 0.0


def page_metrics(raw: dict, events: dict, viewport: dict, scroll_samples: list | None = None, resize_probe: dict | None = None) -> tuple[dict, dict]:
    """쪽 하나의 원본 측정값 -> (수치, 근거 목록).

    resize_probe는 {"target": [너비, 높이], "rows": canvas_resize_rows(...)} 꼴이며, 재지 않았으면 None이다.
    """
    resize_rows = (resize_probe or {}).get("rows") or []
    document = raw.get("document", {})
    effective = raw.get("effective_viewport") or {"width": viewport["width"], "height": viewport["height"]}
    viewport_w = effective.get("width") or viewport["width"]
    viewport_h = viewport["height"]
    elements = (raw.get("interactive") or {}).get("items") or []
    overlaps = find_overlaps(elements)
    targets = small_targets(elements)
    tiny = [row for row in targets["small"] if row["w"] < TINY_TOUCH_PX or row["h"] < TINY_TOUCH_PX]
    canvases = raw.get("canvases") or []
    mismatches = [row for row in (canvas_mismatch(canvas) for canvas in canvases) if row]
    stop = stop_summary(raw.get("stop") or {}, scroll_samples or [], viewport_w, viewport_h)
    style = raw.get("style") or {}
    page_height = document.get("scroll_height") or 0
    scroll_width = max(document.get("scroll_width") or 0, document.get("body_scroll_width") or 0)
    text_elements = style.get("text_elements") or 0
    metrics = {
        "horizontal_overflow_px": max(0, scroll_width - (document.get("client_width") or viewport_w)),
        "outside_viewport_elements": (raw.get("outside_viewport") or {}).get("count", 0),
        "overflowing_elements": (raw.get("content_overflow_x") or {}).get("count", 0),
        "overflowing_elements_vertical": (raw.get("content_overflow_y") or {}).get("count", 0),
        "clipped_texts": (raw.get("clipped") or {}).get("count", 0),
        "ellipsis_truncations": (raw.get("ellipsis") or {}).get("count", 0),
        "overlaps": len(overlaps["same_layer"]),
        "overlaps_pinned_vs_flow": len(overlaps["cross_layer"]),
        "interactive_total": len(elements),
        "interactive_in_first_viewport": sum(1 for element in elements if element.get("in_first_viewport")),
        "small_targets": len(targets["small"]),
        "tiny_targets": len(tiny),
        "small_inline_links": len(targets["inline_links"]),
        "canvases": len(canvases),
        "canvas_mismatches": len(mismatches),
        "canvas_resize_checked": sum(1 for row in resize_rows if row["css_changed"]) if resize_probe else None,
        "canvas_stale_after_resize": sum(1 for row in resize_rows if row["stale"]) if resize_probe else None,
        "stop_found": stop["found"],
        "stop_visible": stop["visible"],
        "stop_top_px": stop["top_px"],
        "stop_width_px": stop["width_px"],
        "stop_height_px": stop["height_px"],
        "stop_in_first_viewport": stop["in_first_viewport"],
        "stop_always_in_viewport": stop["always_in_viewport"],
        "page_height_px": page_height,
        "screens": screens(page_height, viewport_h),
        "console_errors": len(events.get("console_errors") or []),
        "page_errors": len(events.get("page_errors") or []),
        "failed_requests": len(events.get("failed_requests") or []),
        "http_errors": len(events.get("http_errors") or []),
        "blocked_external_requests": len(events.get("blocked_external") or []),
        "broken_images": len((raw.get("images") or {}).get("broken") or []),
        "probe_problems": len(raw.get("probe_problems") or []),
        "style_gradient_elements": style.get("gradient_elements", 0),
        "style_blur_elements": style.get("blur_elements", 0),
        "style_shadow_kinds": style.get("shadow_kinds", 0),
        "style_font_size_kinds": len(style.get("font_sizes") or {}),
        "style_font_weight_kinds": len(style.get("font_weights") or {}),
        "style_text_colors": style.get("text_colors", 0),
        "style_heavy_text_percent": round(100.0 * (style.get("heavy_text_elements") or 0) / text_elements, 1) if text_elements else 0.0,
    }
    details = {
        "document": document,
        "outside_viewport": (raw.get("outside_viewport") or {}).get("items", [])[:DETAIL_LIMIT],
        "outside_viewport_count_with_ancestors": (raw.get("outside_viewport") or {}).get("count_all", 0),
        "overflowing_elements": (raw.get("content_overflow_x") or {}).get("items", [])[:DETAIL_LIMIT],
        "overflowing_elements_vertical": (raw.get("content_overflow_y") or {}).get("items", [])[:DETAIL_LIMIT],
        "clipped_texts": (raw.get("clipped") or {}).get("items", [])[:DETAIL_LIMIT],
        "ellipsis_truncations": (raw.get("ellipsis") or {}).get("items", [])[:DETAIL_LIMIT],
        "scrollable_x": raw.get("scrollable_x") or {},
        "overlaps": overlaps["same_layer"][:DETAIL_LIMIT],
        "overlaps_pinned_vs_flow": overlaps["cross_layer"][:DETAIL_LIMIT],
        "small_targets": targets["small"][:80],
        "small_inline_links": targets["inline_links"][:DETAIL_LIMIT],
        "interactive_skipped": (raw.get("interactive") or {}).get("skipped", {}),
        "canvases": canvases,
        "canvas_mismatches": mismatches,
        "canvas_resize_probe": resize_probe,
        "stop": stop,
        "stop_candidates": (raw.get("stop") or {}).get("candidates", []),
        "stop_scroll_samples": scroll_samples or [],
        "broken_images": (raw.get("images") or {}).get("broken", []),
        "images_total": (raw.get("images") or {}).get("total", 0),
        "console_errors": (events.get("console_errors") or [])[:DETAIL_LIMIT],
        "console_warnings": events.get("console_warnings", 0),
        "page_errors": (events.get("page_errors") or [])[:DETAIL_LIMIT],
        "failed_requests": (events.get("failed_requests") or [])[:DETAIL_LIMIT],
        "http_errors": (events.get("http_errors") or [])[:DETAIL_LIMIT],
        "blocked_external": (events.get("blocked_external") or [])[:DETAIL_LIMIT],
        "style": style,
        "probe_problems": raw.get("probe_problems") or [],
    }
    return metrics, details


def page_key(dataset: str, route: str, viewport_name: str) -> str:
    return f"{dataset}|{route}|{viewport_name}"


def route_slug(route: str) -> str:
    return "overview" if route == "/" else route.strip("/").replace("/", "_")


SUM_METRICS = (
    "outside_viewport_elements",
    "overflowing_elements",
    "overflowing_elements_vertical",
    "clipped_texts",
    "overlaps",
    "small_targets",
    "canvas_mismatches",
    "canvas_stale_after_resize",
    "console_errors",
    "page_errors",
    "failed_requests",
    "http_errors",
    "broken_images",
)


def summarize(pages: list) -> dict:
    """자료 묶음과 화면 크기별 합계. 실행 결과 JSON의 summary에 넣는다."""
    groups: dict = {}
    for page in pages:
        metrics = page.get("metrics")
        if not metrics:
            continue
        key = f"{page['dataset']}|{page['viewport']['name']}"
        group = groups.setdefault(
            key,
            {
                "dataset": page["dataset"],
                "viewport": page["viewport"]["name"],
                "pages": 0,
                "pages_with_horizontal_overflow": 0,
                "max_horizontal_overflow_px": 0,
                "max_screens": 0.0,
                "stop_visible_pages": 0,
                "stop_in_first_viewport_pages": 0,
                **{name: 0 for name in SUM_METRICS},
            },
        )
        group["pages"] += 1
        if metrics["horizontal_overflow_px"] > 0:
            group["pages_with_horizontal_overflow"] += 1
        group["max_horizontal_overflow_px"] = max(group["max_horizontal_overflow_px"], metrics["horizontal_overflow_px"])
        group["max_screens"] = max(group["max_screens"], metrics["screens"])
        group["stop_visible_pages"] += 1 if metrics["stop_visible"] else 0
        group["stop_in_first_viewport_pages"] += 1 if metrics["stop_in_first_viewport"] else 0
        for name in SUM_METRICS:
            group[name] += metrics.get(name) or 0
    return {"by_dataset_viewport": list(groups.values()), "pages_total": len(pages), "pages_failed_to_load": sum(1 for page in pages if page.get("error"))}
