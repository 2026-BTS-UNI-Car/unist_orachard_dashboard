"""실행 결과 JSON을 사람이 읽는 표로 바꾼다.

    python summarize.py <실행 결과 JSON>                     # 화면 크기별 합계
    python summarize.py <실행 결과 JSON> --pages --dataset stress --viewport 390x844 --viewport 1440x900
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ui_metrics  # noqa: E402


def load_run(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != ui_metrics.RUN_SCHEMA:
        raise ValueError(f"{path}: schema가 {ui_metrics.RUN_SCHEMA}가 아닙니다")
    if data.get("schema_version") != ui_metrics.RUN_SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version {data.get('schema_version')}은 이 도구(v{ui_metrics.RUN_SCHEMA_VERSION})가 읽지 못합니다")
    return data


def table(headers: list, rows: list) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def overview(run: dict) -> str:
    """자료 묶음 · 화면 크기별 합계 표."""
    headers = ["자료", "화면", "쪽", "가로 넘친 쪽", "최대 넘침px", "넘친 요소", "잘린 글", "겹침", "44px 미만", "캔버스 불일치", "돌린 뒤 안 따라온 캔버스", "STOP 첫 화면", "최대 화면 수", "콘솔·스크립트 오류", "요청 실패", "깨진 그림"]
    rows = []
    for group in run.get("summary", {}).get("by_dataset_viewport", []):
        rows.append(
            [
                group["dataset"],
                group["viewport"],
                group["pages"],
                group["pages_with_horizontal_overflow"],
                group["max_horizontal_overflow_px"],
                group["overflowing_elements"],
                group["clipped_texts"],
                group["overlaps"],
                group["small_targets"],
                group["canvas_mismatches"],
                group.get("canvas_stale_after_resize", 0),
                f"{group['stop_in_first_viewport_pages']}/{group['stop_visible_pages']}",
                group["max_screens"],
                group["console_errors"] + group["page_errors"],
                group["failed_requests"] + group["http_errors"],
                group["broken_images"],
            ]
        )
    return table(headers, rows)


def stop_cell(metrics: dict) -> str:
    if not metrics["stop_visible"]:
        return "없음"
    return f"{round(metrics['stop_top_px'])}px · {'첫 화면 안' if metrics['stop_in_first_viewport'] else '첫 화면 밖'}"


def pages_table(run: dict, datasets=None, viewports=None, routes=None) -> str:
    """쪽마다 한 줄."""
    headers = ["자료", "경로", "화면", "가로 넘침px", "넘친 요소", "잘린 글", "겹침", "44px 미만/전체", "캔버스 불일치/전체", "돌린 뒤 안 따라옴", "STOP 위쪽 위치", "높이px(화면 수)", "오류"]
    rows = []
    for page in run.get("pages", []):
        if datasets and page["dataset"] not in datasets:
            continue
        if viewports and page["viewport"]["name"] not in viewports:
            continue
        if routes and page["route"] not in routes:
            continue
        metrics = page.get("metrics")
        if not metrics:
            rows.append([page["dataset"], page["route"], page["viewport"]["name"], "측정 실패", "", "", "", "", "", "", "", "", str(page.get("error"))[:60]])
            continue
        stale = metrics.get("canvas_stale_after_resize")
        errors = metrics["console_errors"] + metrics["page_errors"] + metrics["failed_requests"] + metrics["http_errors"]
        rows.append(
            [
                page["dataset"],
                page["route"],
                page["viewport"]["name"],
                metrics["horizontal_overflow_px"],
                metrics["overflowing_elements"],
                metrics["clipped_texts"],
                metrics["overlaps"],
                f"{metrics['small_targets']}/{metrics['interactive_total']}",
                f"{metrics['canvas_mismatches']}/{metrics['canvases']}",
                "-" if stale is None else stale,
                stop_cell(metrics),
                f"{metrics['page_height_px']}({metrics['screens']})",
                errors,
            ]
        )
    return table(headers, rows)


def notable_pages(run: dict, limit: int = 12) -> list:
    """가로 넘침, 겹침, 오류가 있는 쪽을 한 줄씩."""
    lines = []
    for page in run.get("pages", []):
        metrics = page.get("metrics")
        where = f"{page['dataset']} {page['route']} {page['viewport']['name']}"
        if not metrics:
            lines.append(f"{where}: 측정 실패 · {str(page.get('error'))[:120]}")
            continue
        parts = []
        if metrics["horizontal_overflow_px"] > 0:
            parts.append(f"가로 넘침 {metrics['horizontal_overflow_px']}px")
        if metrics["overlaps"]:
            parts.append(f"조작 요소 겹침 {metrics['overlaps']}")
        if metrics["page_errors"] or metrics["console_errors"]:
            parts.append(f"콘솔·스크립트 오류 {metrics['page_errors'] + metrics['console_errors']}")
        if metrics["failed_requests"] or metrics["http_errors"]:
            parts.append(f"요청 실패 {metrics['failed_requests'] + metrics['http_errors']}")
        if parts:
            lines.append(f"{where}: " + ", ".join(parts))
    if len(lines) > limit:
        lines = lines[:limit] + [f"… 외 {len(lines) - limit}쪽"]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="화면 점검 실행 결과를 표로 보여 준다")
    parser.add_argument("run", help="run_checks.py가 만든 JSON")
    parser.add_argument("--pages", action="store_true", help="쪽마다 한 줄씩 보여 준다")
    parser.add_argument("--dataset", action="append", help="보여 줄 자료 묶음(여러 번 가능)")
    parser.add_argument("--viewport", action="append", help="보여 줄 화면 크기(여러 번 가능)")
    parser.add_argument("--route", action="append", help="보여 줄 경로(여러 번 가능)")
    parser.add_argument("--out", type=Path, help="표를 이 파일에도 쓴다(UTF-8, LF)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        run = load_run(args.run)
    except (OSError, ValueError) as error:
        print(f"읽지 못했습니다: {error}", file=sys.stderr)
        return 2
    text = pages_table(run, args.dataset, args.viewport, args.route) if args.pages else overview(run)
    print(text)
    if args.out:
        args.out.write_bytes((text + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
