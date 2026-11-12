#!/usr/bin/env python3
"""Tables from bench results (the SUMMARY.json of each result folder).

  python summarize.py <result folder or root> [...]            text tables
  python summarize.py <root> --markdown                        Markdown tables (for a notes file)
  python summarize.py <root> --json                            flat rows as JSON
  python summarize.py <root> --series <result folder name>     time series of one run (growth over time)
"""
import argparse
import json
import sys
from pathlib import Path


def g(data, *keys):
    for key in keys:
        if isinstance(data, list):
            data = data[key] if isinstance(key, int) and -len(data) <= key < len(data) else None
        elif isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def find_results(paths):
    folders = []
    for path in paths:
        path = Path(path)
        if (path / "SUMMARY.json").is_file():
            folders.append(path)
        elif path.is_dir():
            folders += sorted(child for child in path.iterdir() if (child / "SUMMARY.json").is_file())
    return folders


def row_of(folder):
    summary = json.loads((Path(folder) / "SUMMARY.json").read_text(encoding="utf-8"))
    return row_from_summary(summary, Path(folder).name)


def row_from_summary(summary, name=None):
    """Flat row of one run from its summary (the same numbers the tables show)."""
    run = summary.get("run") or {}
    live = summary.get("live") or {}
    client = g(live, "clients", 0) or {}
    render, ws, groups = client.get("render_delay_ms") or {}, client.get("ws") or {}, client.get("http_groups") or {}
    control = live.get("control") or {}
    command = g(control, "per_op", "command") or {}
    row = dict(
        folder=name, source=g(run, "source", "short"), source_rev=g(run, "source", "rev"), profile=run.get("profile"),
        scenario=run.get("scenario"), label=run.get("label"), failed=run.get("failed"), browser_error=summary.get("error"),
        host_paused=bool(summary.get("host_pauses")),
        live_s=live.get("duration_s"),
        camera_fps=g(render, "camera", "rate_hz"), camera_delay_p50=g(render, "camera", "p50"), camera_delay_p95=g(render, "camera", "p95"),
        camera_kB_s=g(ws, "/ws/camera", "kB_per_s"), camera_frame_B=g(render, "camera", "mean_frame_B"), camera_width=g(render, "camera", "frame_widths"),
        camera_reconnects=g(ws, "/ws/camera", "opens_in_window"),
        lidar_hz=g(render, "lidar", "rate_hz"), lidar_delay_p50=g(render, "lidar", "p50"), lidar_delay_p95=g(render, "lidar", "p95"),
        lidar_kB_s=g(ws, "/ws/lidar", "kB_per_s"), lidar_msg_B=g(ws, "/ws/lidar", "mean_msg_B"),
        imu_hz=g(render, "imu", "rate_hz"), imu_delay_p50=g(render, "imu", "p50"), imu_delay_p95=g(render, "imu", "p95"), imu_kB_s=g(ws, "/ws/imu", "kB_per_s"),
        gps_msgs_s=g(ws, "/ws/gps", "msgs_per_s"), gps_delay_p50=g(render, "gps_text", "p50"), gps_delay_p95=g(render, "gps_text", "p95"),
        gps_kB_s=g(ws, "/ws/gps", "kB_per_s"),
        status_kB_s=g(groups, "status_polling", "kB_per_s"), status_req_s=g(groups, "status_polling", "per_s"),
        map_kB_s=g(groups, "live_map", "kB_per_s"), map_req_s=g(groups, "live_map", "per_s"),
        link_down_kbit_s=g(live, "link", "down", "kbit_per_s"), link_share_pct=g(live, "link", "down", "share_of_rate_pct"),
        link_up_kbit_s=g(live, "link", "up", "kbit_per_s"), stalls=len(g(live, "link", "stalls_in_window") or []),
        command_n=command.get("n"), command_rt_p50=g(command, "round_trip_ms", "p50"), command_rt_p95=g(command, "round_trip_ms", "p95"),
        command_rt_max=g(command, "round_trip_ms", "max"), command_presend_p95=g(command, "pre_send_ms", "p95"),
        command_presend_max=g(command, "pre_send_ms", "max"), command_no_answer=command.get("no_answer"),
        command_period_p50=g(control, "command_period_at_endpoint_ms", "p50"), command_period_p95=g(control, "command_period_at_endpoint_ms", "p95"),
        command_gaps_over_ttl=g(control, "command_period_at_endpoint_ms", "over_input_ttl"),
        input_expired=g(control, "endpoint_state_reasons", "input_expired"),
        control_lost_cycles=g(control, "cycles", "control_lost_while_holding"), cycles=g(control, "cycles", "n"),
        activation_failed=g(control, "cycles", "activation_failed"), activation_p50=g(control, "cycles", "activation_ms", "p50"),
        stop_n=g(control, "stop", "clicks"), stop_endpoint_p50=g(control, "stop", "click_to_endpoint_ms", "p50"),
        stop_endpoint_max=g(control, "stop", "click_to_endpoint_ms", "max"), stop_answer_p50=g(control, "stop", "click_to_answer_ms", "p50"),
        stop_answer_p95=g(control, "stop", "click_to_answer_ms", "p95"), stop_answer_max=g(control, "stop", "click_to_answer_ms", "max"),
        stop_presend_max=g(control, "stop", "pre_send_ms", "max"), stop_not_seen=g(control, "stop", "not_seen_by_endpoint"),
        dashboard_cpu_pct=g(live, "cpu", "procs", "dashboard", "cpu_pct_mean"), host_cpu_pct=g(live, "cpu", "host_cpu_pct", "mean"),
        heap_MB=g(client, "renderer", "js_heap_MB"), page_busy_pct=g(client, "renderer", "main_thread_busy_pct"))
    for name in ("cold", "warm"):
        load = g(summary, "load", name) or {}
        row.update({f"{name}_requests": g(load, "total", "requests"), f"{name}_kB": kb(g(load, "total", "transfer_bytes")),
                    f"{name}_network_requests": load.get("requests_on_network"), f"{name}_304": load.get("not_modified"),
                    f"{name}_static_body_kB": kb(load.get("page_file_body_bytes")), f"{name}_from_cache": load.get("from_cache_without_request"),
                    f"{name}_usable_s": sec(load.get("usable_ms")), f"{name}_load_event_s": sec(load.get("load_event_ms")),
                    f"{name}_settled_s": sec(load.get("settled_ms")), f"{name}_still_open": len(load.get("still_open") or []) if load else None, f"{name}_failed": len(load.get("failed") or []) if load else None,
                    f"{name}_first_camera_s": sec(load.get("first_camera_decoded_ms")), f"{name}_timed_out": load.get("timed_out"),
                    f"{name}_until_usable_kB": kb(g(load, "until_usable", "transfer_bytes")), f"{name}_until_usable_requests": g(load, "until_usable", "requests"),
                    f"{name}_over_100kB": len(load.get("files_over_100kB") or []) if load else None})
    return row


def kb(value):
    return None if value is None else round(value / 1000, 1)


def sec(value):
    return None if value is None else round(value / 1000, 2)


def collect(paths):
    return [row_of(folder) for folder in find_results(paths)]


def show(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}" if abs(value) < 100 else f"{value:.0f}"
    if isinstance(value, list):
        return "/".join(show(v) for v in value)
    return str(value)


TABLES = [
    ("Streams during the live part (rate, delay p50/p95 in ms, kB/s as counted in the page)", [
        ("source", "source"), ("profile", "profile"), ("scenario", "scenario"),
        ("cam fps", "camera_fps"), ("cam p50", "camera_delay_p50"), ("cam p95", "camera_delay_p95"), ("cam kB/s", "camera_kB_s"), ("cam px", "camera_width"),
        ("lidar Hz", "lidar_hz"), ("lidar p50", "lidar_delay_p50"), ("lidar p95", "lidar_delay_p95"), ("lidar kB/s", "lidar_kB_s"),
        ("imu Hz", "imu_hz"), ("imu p50", "imu_delay_p50"), ("imu p95", "imu_delay_p95"),
        ("gps msg/s", "gps_msgs_s"), ("gps p50", "gps_delay_p50"), ("gps p95", "gps_delay_p95")]),
    ("Polling and link use during the live part", [
        ("source", "source"), ("profile", "profile"), ("scenario", "scenario"),
        ("status kB/s", "status_kB_s"), ("status req/s", "status_req_s"), ("map kB/s", "map_kB_s"), ("map req/s", "map_req_s"),
        ("link down kbit/s", "link_down_kbit_s"), ("% of rate", "link_share_pct"), ("link up kbit/s", "link_up_kbit_s"), ("stalls", "stalls"),
        ("dashboard CPU %", "dashboard_cpu_pct"), ("host CPU %", "host_cpu_pct")]),
    ("Manual drive and STOP under load (ms)", [
        ("source", "source"), ("profile", "profile"), ("scenario", "scenario"),
        ("cmd n", "command_n"), ("cmd rt p50", "command_rt_p50"), ("cmd rt p95", "command_rt_p95"), ("cmd rt max", "command_rt_max"),
        ("pre-send p95", "command_presend_p95"), ("pre-send max", "command_presend_max"),
        ("period p50", "command_period_p50"), ("period p95", "command_period_p95"), ("gaps>TTL", "command_gaps_over_ttl"),
        ("input expired", "input_expired"), ("cycles", "cycles"), ("lost while held", "control_lost_cycles"),
        ("STOP n", "stop_n"), ("STOP->endpoint p50", "stop_endpoint_p50"), ("max", "stop_endpoint_max"),
        ("STOP answer p50", "stop_answer_p50"), ("p95", "stop_answer_p95"), ("max", "stop_answer_max")]),
    ("First open (cold cache) and open again (warm cache) of the driving screen", [
        ("source", "source"), ("profile", "profile"), ("scenario", "scenario"),
        ("cold req", "cold_requests"), ("cold kB", "cold_kB"), ("usable s", "cold_usable_s"), ("load event s", "cold_load_event_s"),
        ("all files s", "cold_settled_s"), ("open", "cold_still_open"), ("camera s", "cold_first_camera_s"), (">100 kB", "cold_over_100kB"),
        ("warm req", "warm_requests"), ("on network", "warm_network_requests"), ("304", "warm_304"), ("warm kB", "warm_kB"),
        ("file body kB", "warm_static_body_kB"), ("usable s", "warm_usable_s"), ("load event s", "warm_load_event_s"), ("all files s", "warm_settled_s"), ("open", "warm_still_open")]),
]


def table_rows(rows, columns):
    kept = [row for row in rows if any(row.get(key) is not None for _, key in columns[3:])]
    return [[show(row.get(key)) for _, key in columns] for row in kept]


def render_text(rows):
    out = []
    for title, columns in TABLES:
        body = table_rows(rows, columns)
        if not body:
            continue
        heads = [head for head, _ in columns]
        widths = [max(len(heads[i]), *(len(line[i]) for line in body)) for i in range(len(heads))]
        out.append(title)
        out.append("  ".join(head.ljust(widths[i]) for i, head in enumerate(heads)))
        out += ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(line)) for line in body]
        out.append("")
    problems = [f"{row['folder']}: {row['failed'] or row['browser_error'] or 'the PC paused during the run (see host_pauses)'}"
                for row in rows if row.get("failed") or row.get("browser_error") or row.get("host_paused")]
    if problems:
        out += ["Runs with a failure:"] + ["  " + line[:240] for line in problems]
    return "\n".join(out)


def render_markdown(rows):
    out = []
    for title, columns in TABLES:
        body = table_rows(rows, columns)
        if not body:
            continue
        out += [f"### {title}", "", "| " + " | ".join(head for head, _ in columns) + " |", "|" + "---|" * len(columns)]
        out += ["| " + " | ".join(line) + " |" for line in body]
        out.append("")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--series", help="print the time series of this result folder")
    args = parser.parse_args(argv)
    if args.series:
        for folder in find_results(args.paths):
            if folder.name == args.series:
                summary = json.loads((folder / "SUMMARY.json").read_text(encoding="utf-8"))
                print(json.dumps(g(summary, "live", "series"), ensure_ascii=False, indent=1))
                return 0
        print(f"no result folder named {args.series}", file=sys.stderr)
        return 2
    rows = collect(args.paths)
    if not rows:
        print("no result folders with SUMMARY.json", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(rows) if args.markdown else render_text(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
