#!/usr/bin/env python3
"""Compare bench runs with the accepted values, check the targets, refresh the accepted values.

  python -B compare.py --run <result folder | SUMMARY.json | folder of result folders> [...]
        one line per metric: accepted value, measured value, change, verdict (better / same / worse)
  python -B compare.py --targets [--run <result folder> ...]
        which targets of targets.json are met (without --run: by the accepted values themselves)
  python -B compare.py --targets-table
        the target table of docs/slow_link.md as Markdown (made from targets.json)
  python -B compare.py --accept <result folder> [...] [--replace-all]
        write the key numbers of these runs into the accepted-values file (after an intended change)

Exit value: 0 = nothing worse than the accepted values beyond the tolerance (with --targets: no kept target broken),
1 = a metric got worse or is missing, 2 = a file, argument or run that cannot be used.

A run is matched to its accepted values by "<scenario>/<profile>" (for example drive/normal). See README_KO.md.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

import analyze  # noqa: E402
import summarize  # noqa: E402

ACCEPTED_FILE = TOOL_DIR / "accepted" / "baseline.json"
TARGETS_FILE = TOOL_DIR / "targets.json"
ACCEPTED_SCHEMA = "ubts.slow_link.accepted"
TARGETS_SCHEMA = "ubts.slow_link.targets"
SENSOR_STREAMS = ("/ws/lidar", "/ws/imu", "/ws/gps")
ALL_STREAMS = SENSOR_STREAMS + ("/ws/camera",)

LIVE = ("drive", "quick")
LONG = ("drive", "quick", "drive_map")
# key, Korean label, unit, which direction is better, tolerance (rel, abs), tolerance on a profile with stalls (rel, abs), scenarios.
# The allowed change of a metric is max(abs, rel x accepted value). All tolerances are provisional: one run per cell so far.
# Counts of rare events (input expiry, lost control) allow 2: one of three runs of the same code on "normal" had two.
# The backlog count allows 150 %: two runs of the same data path on "very_bad" gave 29 and 59.
METRIC_ROWS = [
    ("camera_fps", "카메라 fps", "fps", "higher", (0.10, 0.05), (0.25, 0.05), LIVE),
    ("camera_delay_p50", "카메라 지연 p50", "ms", "lower", (0.10, 20), (0.20, 100), LIVE),
    ("camera_delay_p95", "카메라 지연 p95", "ms", "lower", (0.15, 30), (0.30, 200), LIVE),
    ("lidar_hz", "LiDAR 다시 그리는 수", "Hz", "higher", (0.10, 0.1), (0.15, 0.1), LIVE),
    ("lidar_delay_p50", "LiDAR 지연 p50", "ms", "lower", (0.10, 20), (0.20, 50), LIVE),
    ("lidar_delay_p95", "LiDAR 지연 p95", "ms", "lower", (0.15, 30), (0.50, 150), LIVE),
    ("imu_delay_p50", "IMU 지연 p50", "ms", "lower", (0.10, 20), (0.25, 50), LIVE),
    ("imu_delay_p95", "IMU 지연 p95", "ms", "lower", (0.15, 30), (0.50, 150), LIVE),
    ("gps_delay_p50", "GPS 표시 지연 p50", "ms", "lower", (0.20, 50), (0.30, 100), LIVE),
    ("gps_delay_p95", "GPS 표시 지연 p95", "ms", "lower", (0.60, 150), (0.80, 300), LIVE),
    ("status_kB_s", "상태 요청이 쓰는 양", "kB/s", "lower", (0.10, 0.15), (0.15, 0.2), LONG),
    ("map_kB_s", "지도 요청이 쓰는 양", "kB/s", "lower", (0.10, 0.15), (0.15, 0.2), LONG),
    ("link_share_pct", "내려받는 양 ÷ 연결 속도", "%", "lower", (0.0, 3.0), (0.0, 5.0), LONG),
    ("stream_closes", "닫힌 스트림 연결 수", "개", "lower", (0.0, 0), (0.25, 2), LONG + ("hidden", "session_expiry")),
    ("command_rt_p50", "조종 명령 왕복 p50", "ms", "lower", (0.10, 10), (0.15, 30), LONG),
    ("command_rt_p95", "조종 명령 왕복 p95", "ms", "lower", (0.10, 15), (0.25, 50), LIVE),
    ("command_presend_p95", "명령이 브라우저를 떠나기 전 기다림 p95", "ms", "lower", (0.0, 10), (0.0, 10), LIVE),
    ("control_presend_max", "조종 요청이 브라우저를 떠나기 전 기다림 최대 (명령·STOP·활성화 모두)", "ms", "lower", (0.0, 20), (0.0, 20), LONG),
    ("command_period_p50", "명령이 끝점에 닿는 간격 p50", "ms", "lower", (0.10, 15), (0.15, 30), LIVE),
    ("command_period_p95", "명령이 끝점에 닿는 간격 p95", "ms", "lower", (0.10, 15), (0.30, 60), LIVE),
    ("input_expired", "입력 만료 횟수", "번", "lower", (0.0, 2), (0.25, 3), LONG),
    ("control_lost_cycles", "누른 채 조종권을 잃은 조종 연습 수", "번", "lower", (0.0, 2), (0.0, 4), LIVE),
    ("stop_endpoint_p50", "STOP 버튼→제어 끝점 p50", "ms", "lower", (0.10, 8), (0.10, 15), LONG),
    ("stop_answer_p50", "STOP 답 p50", "ms", "lower", (0.10, 15), (0.25, 50), LIVE),
    ("stop_answer_p95", "STOP 답 p95", "ms", "lower", (0.15, 20), (0.50, 100), LIVE),
    ("cold_kB", "처음 열기 받는 양", "kB", "lower", (0.02, 5), (0.03, 5), ("drive", "load")),
    ("cold_page_files", "처음 열기 페이지 파일 요청 수", "개", "lower", (0.0, 0), (0.0, 0), ("drive", "load")),
    ("cold_over_100kB", "처음 열기 100 kB 넘는 파일 수", "개", "lower", (0.0, 0), (0.0, 0), ("drive", "load")),
    ("cold_usable_s", "처음 열기 쓸 수 있기까지", "s", "lower", (0.20, 0.3), (0.25, 0.5), ("drive", "load")),
    ("cold_load_event_s", "처음 열기 load 사건", "s", "lower", (0.20, 0.5), (0.25, 1.0), ("drive", "load")),
    ("warm_page_files_on_network", "다시 열기 연결에 나간 페이지 파일 요청 수", "개", "lower", (0.0, 0), (0.0, 2), ("drive", "load")),
    ("warm_page_file_body_B", "다시 열기 다시 받은 페이지 파일 본문", "B", "lower", (0.10, 500), (0.25, 500), ("drive", "load")),
    ("warm_usable_s", "다시 열기 쓸 수 있기까지", "s", "lower", (0.30, 0.2), (0.40, 0.5), ("drive", "load")),
    ("stall_sensor_fresh_max_ms", "멈춤 뒤 새 자료가 닿기까지 (LiDAR·IMU·GPS 중 가장 늦은 것)", "ms", "lower", (0.50, 500), (0.50, 500), ("drive",)),
    ("stall_camera_fresh_max_ms", "멈춤 뒤 새 카메라 그림이 닿기까지 (가장 늦은 것)", "ms", "lower", (0.30, 1000), (0.30, 1000), ("drive",)),
    ("stall_backlog_max", "멈춤 뒤에 닿은 밀린 메시지 수 (통로 중 최대)", "개", "lower", (1.5, 3), (1.5, 3), ("drive",)),
    ("map_growth_ratio", "지도 응답 크기: 마지막 1분 ÷ 첫 1분", "배", "lower", (0.15, 0.1), (0.15, 0.1), ("drive_map",)),
    ("map_last_minute_kB", "지도 응답 하나의 크기 (마지막 1분)", "kB", "lower", (0.15, 2), (0.15, 2), ("drive_map",)),
    ("map_versions_per_s", "지도 새 판이 그려진 수", "번/s", "higher", (0.15, 0.03), (0.20, 0.03), ("drive_map",)),
    ("map_drawn_delay_p50", "지도 발행→그림 p50", "ms", "lower", (0.20, 200), (0.25, 300), ("drive_map",)),
    ("hidden_stream_kB_s", "숨긴 탭이 받는 스트림 양", "kB/s", "lower", (0.10, 1), (0.10, 1), ("hidden",)),
    ("hidden_status_kB_s", "숨긴 탭의 상태 요청 양", "kB/s", "lower", (0.20, 0.1), (0.20, 0.1), ("hidden",)),
    ("session_longest_gap_ms", "세션 만료 때 화면이 다시 그려지기까지 가장 긴 간격", "ms", "lower", (0.30, 300), (0.30, 300), ("session_expiry",)),
]
METRICS = {row[0]: dict(key=row[0], label_ko=row[1], unit=row[2], better=row[3], rel=row[4][0], abs=row[4][1],
                        stall_rel=row[5][0], stall_abs=row[5][1], scenarios=row[6]) for row in METRIC_ROWS}
# Reported but not counted as a failure (unless --strict): a rare long wait that one run shows and the next does not.
REPORT_ONLY = ("control_presend_max",)
SCENARIO_ORDER = ["drive", "drive_map", "hidden", "session_expiry", "load", "quick"]
PROFILE_ORDER = ["good", "normal", "bad", "very_bad", "none"]
VERDICT_KO = dict(better="좋아짐", same="같음", worse="나빠짐", missing="없음(이번 실행에 수치가 없다)", new="새 수치(받아들인 값 없음)")


class InputError(Exception):
    """A file, argument or run that cannot be used (exit value 2)."""


def g(data, *keys):
    return summarize.g(data, *keys)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


# ---------------------------------------------------------------- one run -> key numbers
def load_run(path):
    """Summary of one run from a result folder or a summary file. Returns (name, summary).

    A result folder also gives what an older summary lacks: the pauses of the PC (cpu.jsonl) and the
    backlog numbers after a stall (RAW.json). Nothing in the folder is written.
    """
    path = Path(path)
    folder = path if path.is_dir() else (path.parent if path.name == "SUMMARY.json" else None)
    file = path / "SUMMARY.json" if path.is_dir() else path
    if not file.is_file():
        raise InputError(f"요약 파일이 없습니다: {file}")
    try:
        summary = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as error:
        raise InputError(f"요약 파일을 읽지 못했습니다: {file} ({error})") from None
    if not isinstance(summary, dict) or summary.get("schema") != "ubts.slow_link.summary":
        raise InputError(f"느린 통신 시험대의 요약 파일이 아닙니다: {file}")
    name = folder.name if folder is not None else path.stem
    if folder is not None:
        if summary.get("pause_rule") != analyze.PAUSE_RULE and (folder / "cpu.jsonl").is_file():
            summary.update(analyze.pause_fields(folder))   # judge older summaries with the same pause rule (nothing is written)
        after = g(summary, "live", "after_stall")
        if after and any("fresh_after_ms" not in item for item in after.values()) and (folder / "RAW.json").is_file():
            raw = analyze.read_json(folder / "RAW.json")
            marks = {m["mark"].split(":")[0]: m["t"] for m in raw.get("phase_marks", [])}
            stalls = g(summary, "live", "link", "stalls_in_window") or []
            if raw.get("clients") and stalls:
                summary["live"]["after_stall"] = analyze.stall_recovery(raw["clients"][0], stalls, marks.get("measure_start"), marks.get("measure_end"))
    return name, summary


def find_runs(paths):
    """Result folders and summary files named by --run (a folder of result folders gives all of them)."""
    found = []
    for path in paths:
        path = Path(path)
        if path.is_file() or (path / "SUMMARY.json").is_file():
            found.append(path)
        elif path.is_dir():
            inner = summarize.find_results([path])
            if not inner:
                raise InputError(f"결과 폴더가 아닙니다(SUMMARY.json 없음): {path}")
            found += inner
        else:
            raise InputError(f"찾을 수 없습니다: {path}")
    return found


def cell_of(summary):
    run = summary.get("run") or {}
    return f"{run.get('scenario')}/{run.get('profile')}"


def conditions_of(summary):
    """What must be equal for two runs to be comparable (no paths, no ports)."""
    run = summary.get("run") or {}
    profile = run.get("profile_settings")
    if isinstance(profile, dict):
        profile = {key: value for key, value in profile.items() if key != "label"}
    return dict(profile_settings=profile, scenario_settings=run.get("scenario_settings"), clients=run.get("clients"))


def has_stalls(conditions):
    return bool(g(conditions, "profile_settings", "stall_ms"))


def unusable(summary):
    """Reasons why the numbers of this run must not be used."""
    run = summary.get("run") or {}
    reasons = []
    if run.get("failed"):
        reasons.append("실행이 실패했다(" + str(run["failed"]).split(":")[0][:40] + ")")
    if summary.get("error"):
        reasons.append("브라우저 쪽이 끝까지 돌지 못했다")
    if summary.get("host_pauses"):
        reasons.append("실행 중에 PC가 멈췄다(host_pauses: 스택 프로세스의 깨어남 탐침 절반 이상이 3초 넘게 멈춤, 탐침이 없는 옛 실행은 CPU 표본의 공백)")
    if summary.get("lost_tabs"):
        reasons.append("시험 탭을 잃었다")
    if not summary.get("live") and not summary.get("load"):
        reasons.append("잰 것이 없다")
    return reasons


def page_file_count(load, field):
    kinds = load.get("by_kind") or {}
    return sum(item.get(field) or 0 for kind, item in kinds.items() if kind != "api")


def stall_numbers(after):
    def fresh_max(item):
        if item.get("not_fresh_within_horizon"):
            return item.get("fresh_horizon_ms")  # no fresh data in the watched time: written as the watched time
        return g(item, "fresh_after_ms", "max")
    if not after or any("fresh_after_ms" not in item for item in after.values()):
        return {}
    sensors = [fresh_max(after[s]) for s in SENSOR_STREAMS if s in after]
    backlog = [g(after[s], "backlog_messages", "max") for s in ALL_STREAMS if s in after]
    out = {}
    if sensors and all(v is not None for v in sensors):
        out["stall_sensor_fresh_max_ms"] = max(sensors)
    if "/ws/camera" in after:
        out["stall_camera_fresh_max_ms"] = fresh_max(after["/ws/camera"])
    if backlog and all(v is not None for v in backlog):
        out["stall_backlog_max"] = max(backlog)
    return out


def map_growth(live):
    """Mean size of one live-map answer in the first and in the last full minute (only when the map was drawing)."""
    series = live.get("series") or []
    minutes = int((live.get("duration_s") or 0) // 60)
    if not live.get("live_map_start") or minutes < 3:
        return {}

    def mean(minute):
        values = [row["map_response_wire_B"] for row in series
                  if minute * 60 <= (row.get("t_s") or 0) < (minute + 1) * 60 and row.get("map_response_wire_B") is not None]
        return statistics.mean(values) if values else None
    first, last = mean(0), mean(minutes - 1)
    if not first or last is None:
        return {}
    return dict(map_growth_ratio=round(last / first, 2), map_last_minute_kB=round(last / 1000, 1))


def metrics_of(summary):
    """Every key number this run has (None when it was not measured)."""
    row = summarize.row_from_summary(summary)
    live = summary.get("live") or {}
    values = {key: row.get(key) for key in (
        "camera_fps", "camera_delay_p50", "camera_delay_p95", "lidar_hz", "lidar_delay_p50", "lidar_delay_p95", "imu_delay_p50", "imu_delay_p95",
        "gps_delay_p50", "gps_delay_p95", "status_kB_s", "map_kB_s", "link_share_pct", "command_rt_p50", "command_rt_p95", "command_presend_p95",
        "command_period_p50", "command_period_p95", "control_lost_cycles", "stop_endpoint_p50", "stop_answer_p50", "stop_answer_p95",
        "cold_usable_s", "cold_load_event_s", "warm_usable_s")}
    if live.get("control"):
        values["input_expired"] = g(live, "control", "endpoint_state_reasons", "input_expired") or 0
        waits = [g(item, "pre_send_ms", "max") for item in (g(live, "control", "per_op") or {}).values()]
        if any(v is not None for v in waits):
            values["control_presend_max"] = max(v for v in waits if v is not None)
    streams = list((g(live, "clients", 0, "ws") or {}).values())
    if streams:
        values["stream_closes"] = sum(item.get("closes_in_window") or 0 for item in streams)
    cold, warm = g(summary, "load", "cold") or {}, g(summary, "load", "warm") or {}
    if cold.get("by_kind") and cold.get("settled_ms") is not None and not cold.get("still_open") and not cold.get("timed_out"):
        # A first open that did not finish has fewer bytes for the wrong reason, so its weight is not compared.
        values["cold_kB"] = round(g(cold, "total", "transfer_bytes") / 1000, 1)
        values["cold_page_files"] = page_file_count(cold, "requests")
        values["cold_over_100kB"] = len(cold.get("files_over_100kB") or [])
    if warm.get("by_kind"):
        values["warm_page_files_on_network"] = page_file_count(warm, "on_network")
        values["warm_page_file_body_B"] = warm.get("page_file_body_bytes")
    values.update(stall_numbers(live.get("after_stall")))
    values.update(map_growth(live))
    map_render = g(live, "clients", 0, "render_delay_ms", "map:liveMapCanvas") or {}
    if live.get("live_map_start"):
        values["map_versions_per_s"] = map_render.get("distinct_versions_per_s")
        values["map_drawn_delay_p50"] = g(map_render, "published_to_drawn_ms", "p50")
    hidden = g(live, "hidden_phase", "clients", 0) or {}
    if hidden.get("ws"):
        values["hidden_stream_kB_s"] = round(sum(item.get("kB_per_s") or 0 for item in hidden["ws"].values()), 2)
        values["hidden_status_kB_s"] = g(hidden, "http_groups", "status_polling", "kB_per_s")
    if g(summary, "run", "scenario") == "session_expiry":
        gaps = [g(live, "clients", 0, "render_delay_ms", name, "gap_ms", "max") for name in ("camera", "lidar", "imu", "gps_text")]
        if any(v is not None for v in gaps):
            values["session_longest_gap_ms"] = max(v for v in gaps if v is not None)
    scenario = g(summary, "run", "scenario")
    return {key: values.get(key) for key, spec in METRICS.items() if scenario in spec["scenarios"]}


# ---------------------------------------------------------------- accepted values
def default_tolerances():
    out = {key: {name: spec[name] for name in ("rel", "abs", "stall_rel", "stall_abs")} for key, spec in METRICS.items()}
    for key in REPORT_ONLY:
        out[key]["mode"] = "report"
    return out


def load_accepted(path):
    try:
        accepted = read_json(path)
    except (OSError, ValueError) as error:
        raise InputError(f"받아들인 값 파일을 읽지 못했습니다: {path} ({error})") from None
    if accepted.get("schema") != ACCEPTED_SCHEMA or not isinstance(accepted.get("cells"), dict):
        raise InputError(f"받아들인 값 파일이 아닙니다: {path}")
    return accepted


def cell_sort_key(name):
    scenario, _, profile = name.partition("/")
    return (SCENARIO_ORDER.index(scenario) if scenario in SCENARIO_ORDER else 99, PROFILE_ORDER.index(profile) if profile in PROFILE_ORDER else 99, name)


def cell_from_run(name, summary):
    run = summary.get("run") or {}
    metrics = {key: value for key, value in metrics_of(summary).items() if value is not None}
    return dict(run=name, commit=g(run, "source", "commit"), short=g(run, "source", "short"), measured_utc=run.get("started_utc"),
                tool_commit=(g(run, "tool", "commit") or "")[:7] or None, conditions=conditions_of(summary), metrics=metrics)


def accept(run_paths, accepted_path, replace_all=False, reset_tolerances=False):
    """Write the key numbers of these runs into the accepted-values file. Returns the lines to print."""
    previous = load_accepted(accepted_path) if Path(accepted_path).is_file() else None
    cells = {} if replace_all or previous is None else dict(previous["cells"])
    lines, seen = [], {}
    for path in find_runs(run_paths):
        name, summary = load_run(path)
        reasons = unusable(summary)
        if reasons:
            raise InputError(f"{name}: 받아들일 수 없는 실행입니다 ({'; '.join(reasons)})")
        key = cell_of(summary)
        if key in seen:
            raise InputError(f"같은 칸({key})에 실행이 둘입니다: {seen[key]}, {name}. 하나만 주십시오")
        seen[key] = name
        cells[key] = cell_from_run(name, summary)
        if not cells[key]["metrics"]:
            raise InputError(f"{name}: 받아들일 수치가 없습니다({key})")
        lines.append(f"{key}: {name} ({cells[key]['short']}) 수치 {len(cells[key]['metrics'])}개")
    tolerances = default_tolerances()
    if previous is not None and not reset_tolerances:
        tolerances.update({key: value for key, value in (previous.get("tolerances") or {}).items() if key in tolerances})
    cells = {key: cells[key] for key in sorted(cells, key=cell_sort_key)}
    commits = sorted({cell["short"] for cell in cells.values() if cell.get("short")})
    accepted = dict(
        schema=ACCEPTED_SCHEMA, version=1,
        note_ko="느린 통신 시험대의 받아들인 값. compare.py --accept 로 만든다(손으로 고치지 않는다; tolerances만 손으로 고칠 수 있다). "
                "x86 PC 한 대에서 연결을 흉내 내어 잰 값이고 칸마다 실행 한 번뿐이라 허용 폭은 잠정이다.",
        provisional=True, commits=commits, measured_date=max((cell.get("measured_utc") or "")[:10] for cell in cells.values()),
        tolerance_rule="허용 폭 = max(abs, rel x 받아들인 값). 멈춤이 있는 프로필(bad, very_bad)은 stall_rel, stall_abs를 쓴다. 이보다 더 나빠지면 '나빠짐'. "
                       "mode가 report인 수치는 알리기만 하고 실패로 세지 않는다(--strict면 센다)",
        tolerances=tolerances, cells=cells)
    write_json(accepted_path, accepted)
    return lines


# ---------------------------------------------------------------- comparison
def allowance(tolerance, accepted_value, stalled):
    rel, absolute = (tolerance["stall_rel"], tolerance["stall_abs"]) if stalled else (tolerance["rel"], tolerance["abs"])
    return max(absolute, rel * abs(accepted_value))


def judge(better, accepted_value, measured, allowed):
    worse_by = measured - accepted_value if better == "lower" else accepted_value - measured
    if worse_by > allowed + 1e-9:
        return "worse"
    if worse_by < -allowed - 1e-9:
        return "better"
    return "same"


def compare_cell(cell, measured, tolerances, stalled=None):
    """One row per metric of the accepted cell (and one per measured metric that has no accepted value).

    A row with mode "report" is shown but does not fail the comparison (see failing())."""
    stalled = has_stalls(cell.get("conditions")) if stalled is None else stalled
    rows = []
    for key in list(METRICS) + [key for key in cell["metrics"] if key not in METRICS]:
        accepted_value, value = cell["metrics"].get(key), measured.get(key)
        spec = METRICS.get(key) or dict(label_ko=key, unit="", better="lower")
        tolerance = tolerances.get(key) or {name: (METRICS.get(key) or {}).get(name, 0) for name in ("rel", "abs", "stall_rel", "stall_abs")}
        row = dict(metric=key, label_ko=spec["label_ko"], unit=spec["unit"], better=spec["better"], accepted=accepted_value, measured=value,
                   mode=tolerance.get("mode", "fail"))
        if accepted_value is None and value is None:
            continue
        if accepted_value is None:
            row.update(verdict="new")
        elif value is None:
            row.update(verdict="missing")
        else:
            allowed = allowance(tolerance, accepted_value, stalled)
            row.update(change=round(value - accepted_value, 3), change_pct=round(100 * (value - accepted_value) / accepted_value, 1) if accepted_value else None,
                       allowed=round(allowed, 3), verdict=judge(spec["better"], accepted_value, value, allowed))
        rows.append(row)
    return rows


def failing(rows, allow_missing=False, strict=False):
    """The rows that make a comparison fail: worse beyond the tolerance, or missing (report-only rows only with strict)."""
    return [row for row in rows if (row["verdict"] == "worse" or (row["verdict"] == "missing" and not allow_missing))
            and (strict or row["mode"] != "report")]


def show(value, unit=""):
    if value is None:
        return "-"
    text = summarize.show(value)
    return text + ("" if unit in ("개", "배", "번", "") else " ") + unit


def signed(value):
    return ("+" if value > 0 else "") + short(value)


def short(value):
    return f"{value:.3g}" if abs(value) < 1000 else f"{value:.0f}"


def render_rows(rows):
    lines = []
    width = max((len(row["metric"]) for row in rows), default=0)
    for row in rows:
        change = ""
        if row.get("change") is not None:
            percent = f" ({'+' if row['change_pct'] > 0 else ''}{row['change_pct']:g} %)" if row.get("change_pct") is not None else ""
            change = f"변화 {signed(row['change'])}{percent}, 허용 ±{short(row['allowed'])}"
        lines.append(f"  {row['metric'].ljust(width)}  {row['label_ko']}: 받아들인 값 {show(row['accepted'], row['unit'])}, 이번 {show(row['measured'], row['unit'])}"
                     + (f", {change}" if change else "") + f" → {VERDICT_KO[row['verdict']]}"
                     + (" (알리기만 함)" if row["mode"] == "report" and row["verdict"] in ("worse", "missing") else ""))
    return lines


def conditions_difference(accepted_conditions, conditions):
    out = []
    for part in ("profile_settings", "scenario_settings", "clients"):
        a, b = (accepted_conditions or {}).get(part), (conditions or {}).get(part)
        if a != b:
            if isinstance(a, dict) and isinstance(b, dict):
                keys = sorted(key for key in set(a) | set(b) if a.get(key) != b.get(key))
                out.append(f"{part}: " + ", ".join(f"{key} {a.get(key)!r} → {b.get(key)!r}" for key in keys))
            else:
                out.append(f"{part}: {a!r} → {b!r}")
    return out


def compare_runs(run_paths, accepted, cell_override=None, allow_missing=False, ignore_conditions=False, strict=False):
    """Compare each run with its accepted cell. Returns (exit value, lines, records)."""
    lines, records = [], []
    for path in find_runs(run_paths):
        name, summary = load_run(path)
        key = cell_override or cell_of(summary)
        record = dict(run=name, cell=key, commit=g(summary, "run", "source", "short"))
        records.append(record)
        lines.append(f"{name} ({record['commit']}) ↔ 받아들인 값 {key}")
        reasons = unusable(summary)
        cell = accepted["cells"].get(key)
        if reasons:
            record.update(status="unusable", reasons=reasons)
            lines.append("  쓸 수 없는 실행: " + "; ".join(reasons))
        elif cell is None:
            record.update(status="no_accepted_cell")
            lines.append(f"  받아들인 값이 없는 칸입니다. 있는 칸: {', '.join(accepted['cells'])}")
        else:
            difference = [] if ignore_conditions or cell_override else conditions_difference(cell.get("conditions"), conditions_of(summary))
            if difference:
                record.update(status="different_conditions", reasons=difference)
                lines.append("  잰 조건이 받아들인 값과 다릅니다(--ignore-conditions로 그래도 견줄 수 있다): " + "; ".join(difference))
            else:
                rows = compare_cell(cell, metrics_of(summary), accepted.get("tolerances") or {})
                counts = {verdict: sum(1 for row in rows if row["verdict"] == verdict) for verdict in VERDICT_KO}
                failed = failing(rows, allow_missing, strict)
                record.update(status="worse" if failed else "ok", rows=rows, counts=counts, failed=[row["metric"] for row in failed])
                lines += render_rows(rows)
                lines.append(f"  = 좋아짐 {counts['better']}, 같음 {counts['same']}, 나빠짐 {counts['worse']}, 없음 {counts['missing']}, 새 수치 {counts['new']}"
                             + (" → 통과" if not failed else " → 실패: " + ", ".join(record["failed"])))
        lines.append("")
    statuses = {record["status"] for record in records}
    return (1 if "worse" in statuses else 2 if statuses - {"ok"} else 0), lines, records


# ---------------------------------------------------------------- targets
OPS = {"<=": lambda value, target: value <= target, "<": lambda value, target: value < target, ">=": lambda value, target: value >= target}
OP_KO = {"<=": "≤", "<": "<", ">=": "≥"}
KIND_KO = dict(keep="지킬 값", reach="닿을 값")


def load_targets(path):
    try:
        document = read_json(path)
    except (OSError, ValueError) as error:
        raise InputError(f"목표 파일을 읽지 못했습니다: {path} ({error})") from None
    if document.get("schema") != TARGETS_SCHEMA or not isinstance(document.get("targets"), list):
        raise InputError(f"목표 파일이 아닙니다: {path}")
    for target in document["targets"]:
        missing = [field for field in ("id", "what_ko", "scenario", "profile", "metric", "op", "target", "today", "kind", "tasks", "note_ko") if field not in target]
        if missing or target["metric"] not in METRICS or target["op"] not in OPS or target["kind"] not in KIND_KO:
            raise InputError(f"목표 항목이 잘못됐습니다: {target.get('id')} {missing}")
    return document


def target_met(target, value):
    return None if value is None else bool(OPS[target["op"]](value, target["target"]))


def target_text(target):
    unit = METRICS[target["metric"]]["unit"]
    if target["op"] == "<=" and target["target"] == 0:
        return show(0, unit)
    return f"{OP_KO[target['op']]} {show(target['target'], unit)}"


def targets_table(document):
    """The target table of docs/slow_link.md (the text between its two marker lines)."""
    heads = ["번호", "무엇을 재나", "시나리오/프로필", "지금", "목표", "구분", "닿게 할 과제", "초안과 견주어"]
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    for target in document["targets"]:
        unit = METRICS[target["metric"]]["unit"]
        lines.append("| " + " | ".join([target["id"], target["what_ko"], f"{target['scenario']}/{target['profile']}", show(target["today"], unit),
                                        target_text(target), KIND_KO[target["kind"]], ", ".join(target["tasks"]), target["note_ko"]]) + " |")
    return "\n".join(lines)


def check_targets(document, measured_by_cell, strict=False):
    """Which targets are met by these numbers. measured_by_cell: {"drive/normal": {metric: value}}. Returns (exit value, lines, rows)."""
    lines, rows, failed = [], [], 0
    for target in document["targets"]:
        key = f"{target['scenario']}/{target['profile']}"
        if key not in measured_by_cell:
            continue
        value = measured_by_cell[key].get(target["metric"])
        met = target_met(target, value)
        unit = METRICS[target["metric"]]["unit"]
        state = "수치 없음" if met is None else ("만족" if met else "못 미침")
        broken = (met is not True) if target["kind"] == "keep" else (strict and met is not True)
        failed += bool(broken)
        rows.append(dict(id=target["id"], cell=key, metric=target["metric"], measured=value, target=target["target"], op=target["op"], kind=target["kind"],
                         met=met, broken=bool(broken)))
        lines.append(f"  {target['id']}  {target['what_ko']} ({key}): {show(value, unit)} / 목표 {target_text(target)} → {state}"
                     + (" [지킬 값이 깨졌다]" if broken and target["kind"] == "keep" else ""))
    met_count = sum(1 for row in rows if row["met"])
    lines.append(f"  = 목표 {len(rows)}개 중 만족 {met_count}개, 못 미침 {sum(1 for row in rows if row['met'] is False)}개, 수치 없음 {sum(1 for row in rows if row['met'] is None)}개")
    return (1 if failed else 0), lines, rows


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="느린 통신 시험대: 받아들인 값과 견주기, 목표 확인, 받아들인 값 다시 만들기")
    parser.add_argument("--run", nargs="+", default=[], help="결과 폴더, SUMMARY.json, 또는 결과 폴더들이 든 폴더")
    parser.add_argument("--accepted", type=Path, default=ACCEPTED_FILE, help="받아들인 값 파일 (기본 accepted/baseline.json)")
    parser.add_argument("--cell", help="견줄 칸을 직접 정한다 (예: drive/normal). 주지 않으면 실행의 시나리오/프로필")
    parser.add_argument("--allow-missing", action="store_true", help="이번 실행에 없는 수치를 실패로 세지 않는다")
    parser.add_argument("--ignore-conditions", action="store_true", help="잰 조건(프로필 값, 실시간 길이 등)이 달라도 견준다")
    parser.add_argument("--targets", action="store_true", help="목표표의 목표를 만족하는지 본다 (--run이 없으면 받아들인 값으로)")
    parser.add_argument("--targets-file", type=Path, default=TARGETS_FILE)
    parser.add_argument("--strict", action="store_true", help="알리기만 하는 수치도 실패로 센다. --targets에서는 아직 닿지 못한 목표도 실패로 센다")
    parser.add_argument("--targets-table", action="store_true", help="목표표를 Markdown으로 찍는다")
    parser.add_argument("--accept", nargs="+", metavar="RUN", help="이 실행들의 수치를 받아들인 값 파일에 쓴다 (칸마다 실행 하나)")
    parser.add_argument("--replace-all", action="store_true", help="--accept에서 준 실행에 없는 칸은 지운다")
    parser.add_argument("--reset-tolerances", action="store_true", help="--accept에서 허용 폭을 도구의 기본값으로 되돌린다")
    parser.add_argument("--json", type=Path, help="판정 결과를 이 JSON 파일에도 쓴다")
    args = parser.parse_args(argv)
    try:
        if args.targets_table:
            print(targets_table(load_targets(args.targets_file)))
            return 0
        if args.accept:
            for line in accept(args.accept, args.accepted, args.replace_all, args.reset_tolerances):
                print(line)
            print(f"받아들인 값 파일을 썼습니다: {args.accepted}")
            return 0
        accepted = load_accepted(args.accepted)
        if args.targets:
            document = load_targets(args.targets_file)
            if args.run:
                measured, problems = {}, []
                for path in find_runs(args.run):
                    name, summary = load_run(path)
                    if unusable(summary):
                        problems.append(f"{name}: " + "; ".join(unusable(summary)))
                    else:
                        measured[cell_of(summary)] = metrics_of(summary)
                for line in problems:
                    print("쓸 수 없는 실행: " + line)
                print("목표 확인 (이번 실행): " + ", ".join(measured))
            else:
                measured = {key: cell["metrics"] for key, cell in accepted["cells"].items()}
                print(f"목표 확인 (받아들인 값 {', '.join(accepted.get('commits') or [])}, {accepted.get('measured_date')})")
            code, lines, rows = check_targets(document, measured, args.strict)
            print("\n".join(lines))
            if args.json:
                write_json(args.json, dict(mode="targets", rows=rows, exit=code))
            return 2 if args.run and not measured else code
        if not args.run:
            parser.error("--run, --targets, --targets-table, --accept 가운데 하나가 필요합니다")
        code, lines, records = compare_runs(args.run, accepted, args.cell, args.allow_missing, args.ignore_conditions, args.strict)
        print(f"받아들인 값: {args.accepted.name} ({', '.join(accepted.get('commits') or [])}, {accepted.get('measured_date')}, 허용 폭은 잠정)")
        print("\n".join(lines).rstrip())
        if args.json:
            write_json(args.json, dict(mode="compare", runs=records, exit=code))
        return code
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
