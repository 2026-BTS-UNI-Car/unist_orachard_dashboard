#!/usr/bin/env python3
"""Summarize one bench run: RAW.json of browser_runner.mjs plus the files the stack wrote next to it.

Every process of the bench and the browser share one clock (one PC), so times are compared directly.

  stream delay   = drawn / decoded in the page  minus  publish stamp of the replayed bridge
  arrival delay  = WebSocket message event      minus  publish stamp (bridge queue + dashboard relay + link)
  control round trip = the page called fetch()  until  the answer body was read
  control pre-send   = fetch() until the request left the browser (waiting for a connection; Resource Timing requestStart)
  control uplink     = fetch() until the recording control endpoint received the request

  python analyze.py <result folder>     prints SUMMARY as JSON
"""
import json
import statistics
import sys
from pathlib import Path

INPUT_TTL_MS = 750.0  # only used to count command gaps; the limit itself is read from control_endpoint.json when present
STATIC_PREFIXES = ("/static/", "/docs-assets/")
MAP_ASSET_PREFIXES = ("/maps/",)


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, max(0, int(round(q / 100 * (len(values) - 1)))))
    return round(values[k], 1)


def dist(values):
    values = [v for v in values if v is not None]
    return dict(n=len(values), p50=pct(values, 50), p90=pct(values, 90), p95=pct(values, 95), max=pct(values, 100)) if values else dict(n=0)


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- page load (first open / open again)
def classify(path, route):
    if path == route or path == "/":
        return "document"
    if path.startswith(STATIC_PREFIXES):
        return "static"
    if path.startswith(MAP_ASSET_PREFIXES):
        return "map_asset"
    if path.startswith("/api/"):
        return "api"
    return "other"


def summarize_load(load, route="/rover-control"):
    """Requests and bytes of one page load. `load` is one entry of client["loads"] (request rows from the browser's network events)."""
    if not load or "requests" not in load:
        return dict(error="no load record", timed_out=bool(load and load.get("timed_out")))
    nav_at = load["nav_at"]
    marks = load.get("milestones") or {}
    rel = {name: round(t - nav_at) for name, t in marks.items()}
    usable_at = marks.get("drive_ready")
    # Page files belong to the load until it ended; API calls only until the page was usable and loaded (later ones are polling).
    api_until = load.get("ready_at") or load.get("done_at") or load.get("t") or nav_at
    rows = []
    for r in load["requests"]:
        wire_status = r.get("wire_status") or r.get("status")
        rows.append(dict(path=r["path"], kind=classify(r["path"], route), start_ms=r["start"] - nav_at,
                         duration_ms=None if r.get("end") is None else r["end"] - r["start"], transfer=r.get("transfer") or 0,
                         decoded=r.get("decoded") or 0, status=wire_status, from_cache=bool(r.get("from_cache")), failed=r.get("failed"),
                         finished=r.get("end") is not None or bool(r.get("handed_off")), handed_off=bool(r.get("handed_off")) and r.get("end") is None,
                         pre_send_ms=None if r.get("send_start_ms") is None else round((r.get("queued_ms") or 0) + r["send_start_ms"], 1)))
    counted = [r for r in rows if r["kind"] != "api" or nav_at + r["start_ms"] <= api_until]
    until_usable = [r for r in counted if usable_at is not None and nav_at + r["start_ms"] <= usable_at]
    on_network = [r for r in counted if not r["from_cache"] or r["status"] == 304]

    def total(items):
        return dict(requests=len(items), transfer_bytes=sum(r["transfer"] for r in items), decoded_bytes=sum(r["decoded"] for r in items))
    by_kind = {}
    for kind in ("document", "static", "map_asset", "api", "other"):
        items = [r for r in counted if r["kind"] == kind]
        if items:
            by_kind[kind] = dict(total(items), on_network=sum(1 for r in items if r in on_network))
    page_files = [r for r in counted if r["kind"] != "api"]
    largest = sorted(counted, key=lambda r: -r["transfer"])[:10]
    return dict(
        timed_out=bool(load.get("timed_out")), navigate_error=load.get("navigate_error"),
        usable_ms=rel.get("drive_ready"), first_telemetry_ms=rel.get("first_telemetry"),
        dom_content_loaded_ms=rel.get("dom_content_loaded"), load_event_ms=rel.get("load_event"),
        settled_ms=load["done_at"] - nav_at if load.get("done_at") and load.get("settled", True) else None,
        still_open=load.get("still_open") or [],
        first_camera_decoded_ms=rel.get("first_camera_decoded"), first_lidar_drawn_ms=rel.get("first_lidar_drawn"),
        total=total(counted), until_usable=total(until_usable), by_kind=by_kind,
        requests_on_network=len(on_network), not_modified=sum(1 for r in counted if r["status"] == 304),
        from_cache_without_request=sum(1 for r in counted if r["from_cache"] and r["status"] != 304),
        page_file_body_bytes=sum(r["transfer"] for r in page_files if r["status"] not in (304, None) and not r["from_cache"]),
        failed=[dict(path=r["path"], error=r["failed"]) for r in counted if r["failed"]],
        completed_elsewhere=sorted(r["path"] for r in counted if r["handed_off"]),
        unfinished=sorted(r["path"] for r in counted if not r["finished"]),
        files_over_100kB=sorted(r["path"] for r in counted if r["transfer"] > 100_000),
        largest=[dict(path=r["path"], transfer=r["transfer"], decoded=r["decoded"], status=r["status"]) for r in largest],
        requests=[dict(path=r["path"], kind=r["kind"], start_ms=r["start_ms"], duration_ms=r["duration_ms"], transfer=r["transfer"],
                       status=r["status"], from_cache=r["from_cache"], pre_send_ms=r["pre_send_ms"]) for r in sorted(counted, key=lambda r: r["start_ms"])])


# ---------------------------------------------------------------- live window: streams, polling, page work
def summarize_client(client, t_from, t_to):
    samples = [s for s in client["samples"] if s]
    out = dict(index=client["index"], hidden=client.get("hidden"), final=client.get("final"))
    base = next((s for s in samples if s["t"] >= t_from - 1500), None)
    inside = [s for s in samples if s["t"] <= t_to + 1500]
    last = inside[-1] if inside else None
    if not base or not last or last is base:
        out["error"] = "not enough samples"
        return out
    span = (last["t"] - base["t"]) / 1000
    out["span_s"] = round(span, 1)
    ws = {}
    for path, v in last["ws"].items():
        b = base["ws"].get(path, {})
        msgs = v["msgs"] - b.get("msgs", 0)
        nbytes = v["bytes"] - b.get("bytes", 0)
        ws[path] = dict(msgs_per_s=round(msgs / span, 2), kB_per_s=round(nbytes / span / 1000, 2),
                        mean_msg_B=round(nbytes / msgs) if msgs else None, max_msg_B=v.get("max_msg"),
                        sent_per_s=round((v["sent"] - b.get("sent", 0)) / span, 2),
                        opens_in_window=v["opened"] - b.get("opened", 0), closes_in_window=v["closed"] - b.get("closed", 0),
                        opens_total=v["opened"], close_codes=[c for c in v.get("close_codes", []) if base["t"] <= c[0] <= last["t"]][-8:])
    out["ws"] = ws
    win = [s for s in samples if base["t"] < s["t"] <= last["t"]]
    arrivals, renders = {}, {}
    for s in win:
        for k, rows in s["arrivals"].items():
            arrivals.setdefault(k, []).extend(rows)
        for k, rows in s["renders"].items():
            renders.setdefault(k, []).extend(rows)
    out["arrival_delay_ms"] = {k: dict(dist([r[0] - r[1] for r in rows if r[1]]), rate_hz=round(len(rows) / span, 2)) for k, rows in arrivals.items()}
    rl = {}
    for k, rows in renders.items():
        lags = [r[0] - r[1] for r in rows if r[1]]
        rl[k] = dict(dist(lags), rate_hz=round(len(rows) / span, 2))
        times = sorted(r[0] for r in rows)
        gaps = [b - a for a, b in zip(times, times[1:])]
        rl[k]["gap_ms"] = dict(p50=pct(gaps, 50), p95=pct(gaps, 95), max=pct(gaps, 100)) if gaps else None
        if k == "camera":
            widths = sorted({r[3] for r in rows if len(r) > 3 and r[3]})
            rl[k]["frame_widths"] = widths
            rl[k]["mean_frame_B"] = round(statistics.mean(r[2] for r in rows)) if rows else None
        if k.startswith("map:"):
            first = {}
            for r in rows:
                if r[1] and r[2] not in first:
                    first[r[2]] = r[0] - r[1]
            rl[k]["new_version_delay_ms"] = dist(list(first.values()))
            rl[k]["distinct_versions_per_s"] = round(len({r[2] for r in rows}) / span, 2)
            rl[k]["max_pose_age_s"] = round(max((r[0] - r[1] for r in rows if r[1]), default=0) / 1000, 1)
            first_pub = {}
            for r in rows:
                if len(r) > 6 and r[6] and r[2] not in first_pub:
                    first_pub[r[2]] = r
            if first_pub:
                rl[k]["published_to_drawn_ms"] = dist([r[0] - r[6] for r in first_pub.values()])
                rl[k]["sensor_to_published_ms"] = dist([r[6] - r[1] for r in first_pub.values() if r[1]])
    out["render_delay_ms"] = rl
    handler = {}
    for s in win:
        for k, rows in (s.get("handler") or {}).items():
            handler.setdefault(k, []).extend(rows)
    out["ws_handler_ms"] = {k: dict(dist(v), total_ms_per_s=round(sum(v) / span, 1)) for k, v in handler.items()}
    keep = ("effective_profile", "profile", "requested_max_points", "point_count", "source_point_count", "ack_latency_ms", "requested_fps",
            "budget_bytes_s", "delivery_state", "preview_quality", "source_fps", "source_width", "source_height", "max_inflight_frames",
            "effective_fps_limit", "flow", "camera_policy")
    out["last_status"] = {k: {x: v.get(x) for x in keep if x in v} for k, v in (last.get("status") or {}).items()}
    out["canvas_update_fps"] = {k: round((last["canvas"].get(k, 0) - base["canvas"].get(k, 0)) / span, 2) for k in last["canvas"]}
    out["raf_fps"] = round((last["frames"] - base["frames"]) / span, 1)
    lt = [r for s in win for r in s["longtasks"]]
    out["long_tasks"] = dict(count=len(lt), per_min=round(len(lt) / span * 60, 1), busy_pct=round(100 * sum(r[1] for r in lt) / (span * 1000), 1),
                             max_ms=max((r[1] for r in lt), default=0))
    out["event_loop_lag_ms"] = dist([v for s in win for v in s["lag"]])
    res = {}
    for s in win:
        for path, r in s["resources"].items():
            agg = res.setdefault(path, dict(n=0, bytes=0, durs=[]))
            agg["n"] += r["n"]
            agg["bytes"] += r["bytes"]
            agg["durs"] += r["durs"]
    out["http"] = {p: dict(per_s=round(v["n"] / span, 3), kB_per_s=round(v["bytes"] / span / 1000, 3), mean_B=round(v["bytes"] / v["n"]),
                           dur_p50=pct(v["durs"], 50), dur_p95=pct(v["durs"], 95), dur_max=pct(v["durs"], 100))
                   for p, v in sorted(res.items(), key=lambda kv: -kv[1]["bytes"]) if v["n"]}

    def group(predicate):
        items = [v for p, v in res.items() if predicate(p)]
        return dict(per_s=round(sum(v["n"] for v in items) / span, 2), kB_per_s=round(sum(v["bytes"] for v in items) / span / 1000, 3))
    is_map = lambda p: p.startswith("/api/live/map")  # noqa: E731
    is_control = lambda p: p.startswith("/api/rover/") and p != "/api/rover/status"  # noqa: E731
    out["http_groups"] = dict(
        live_map=group(is_map), control=group(is_control),
        status_polling=group(lambda p: p.startswith("/api/") and not is_map(p) and not is_control(p)),
        other=group(lambda p: not p.startswith("/api/")))
    metrics = [x for x in client["metrics"] if base["t"] <= x["t"] <= last["t"] + 1500]
    if len(metrics) >= 2:
        a, b = metrics[0], metrics[-1]
        wall = (b["t"] - a["t"]) / 1000
        out["renderer"] = dict(js_heap_MB=[round(a["JSHeapUsedSize"] / 1048576, 1), round(b["JSHeapUsedSize"] / 1048576, 1)],
                               heap_growth_MB_per_min=round((b["JSHeapUsedSize"] - a["JSHeapUsedSize"]) / 1048576 / (wall / 60), 2) if wall else None,
                               dom_nodes=[int(a["Nodes"]), int(b["Nodes"])], listeners=[int(a["JSEventListeners"]), int(b["JSEventListeners"])],
                               main_thread_busy_pct=round(100 * (b["TaskDuration"] - a["TaskDuration"]) / wall, 1),
                               script_pct=round(100 * (b["ScriptDuration"] - a["ScriptDuration"]) / wall, 1),
                               layouts_per_s=round((b["LayoutCount"] - a["LayoutCount"]) / wall, 1))
    out["page_errors"] = [e for s in samples for e in s["errors"]][:20]
    out["console_errors"] = client.get("console", [])[:20]
    out["map_events"] = [e for s in samples for e in s["mapEvents"]][:30]
    out["visibility_events"] = [v for s in samples for v in s.get("visEvents", [])][:10]
    return out


def growth_series(client, t0):
    """One row per sample window: does anything grow with time? (live-map response size, heap, rates)"""
    samples = [s for s in client["samples"] if s]
    rows, prev = [], None
    for s in samples:
        if prev is None or s["t"] <= t0:
            prev = s
            continue
        span = (s["t"] - prev["t"]) / 1000
        if span <= 0:
            continue
        res = s["resources"].get("/api/live/map", {"n": 0, "bytes": 0, "durs": [], "sizes": []})
        sizes = res.get("sizes") or []
        cam = s["renders"].get("camera", [])
        lid = s["renders"].get("lidar", [])
        rows.append(dict(
            t_s=round((s["t"] - t0) / 1000),
            map_requests_per_s=round(res["n"] / span, 2), map_kB_per_s=round(res["bytes"] / span / 1000, 2),
            map_response_wire_B=round(statistics.mean(r[1] for r in sizes)) if sizes else None,
            map_response_json_B=round(statistics.mean(r[2] for r in sizes)) if sizes else None,
            map_http_p50_ms=pct(res["durs"], 50),
            camera_fps=round(len(cam) / span, 2), camera_delay_p50_ms=pct([r[0] - r[1] for r in cam], 50),
            lidar_hz=round(len(lid) / span, 2), lidar_delay_p50_ms=pct([r[0] - r[1] for r in lid], 50),
            heap_MB=round(s["heap"] / 1048576, 1) if s.get("heap") else None, dom_nodes=s.get("dom")))
        prev = s
    return rows


# ---------------------------------------------------------------- manual drive and STOP
def match_timing(row, timings, used):
    """Resource Timing row of one control request: same operation, same start (both use the page's performance clock)."""
    best, best_gap = None, 8.0
    for index, timing in enumerate(timings):
        if index in used or timing[0] != row["op"]:
            continue
        gap = abs(timing[1] - row["p_call"])
        if gap < best_gap:
            best, best_gap = index, gap
    if best is None:
        return None
    used.add(best)
    return timings[best]


def match_endpoint(row, events, used):
    """The endpoint's record of one control request: same operation and sequence number, received after the call."""
    answered = row["t_call"] + ((row["p_done"] - row["p_call"]) if row.get("p_done") is not None else 5000.0)
    for index, event in enumerate(events):
        if index in used or event.get("op") != row["wire_op"]:
            continue
        t = event["t"] * 1000
        if t < row["t_call"] - 5 or t > answered + 50:
            continue
        if row["wire_op"] != "stop" and event.get("seq") != row.get("seq"):
            continue
        used.add(index)
        return event
    return None


WIRE_OPS = {"arm": "arm", "command": "drive", "keepalive": "keepalive", "release": "release", "stop": "stop"}


def summarize_control(samples, events, marks, drive_log, t_from, t_to, input_ttl_ms=INPUT_TTL_MS):
    rows = [dict(r) for s in samples if s for r in s.get("control", []) if t_from <= r["t_call"] <= t_to]
    timings = [t for s in samples if s for t in s.get("control_timings", [])]
    requests = [e for e in events if e.get("kind") == "request"]
    used_t, used_e = set(), set()
    for row in rows:
        row["wire_op"] = WIRE_OPS.get(row["op"], row["op"])
        timing = match_timing(row, timings, used_t)
        event = match_endpoint(row, requests, used_e)
        row["round_trip_ms"] = round(row["p_done"] - row["p_call"], 1) if row.get("p_done") is not None and not row.get("error") else None
        row["pre_send_ms"] = timing[4] if timing else None
        row["connect_ms"] = timing[3] if timing else None
        row["uplink_ms"] = round(event["t"] * 1000 - row["t_call"], 1) if event else None
        row["reached_endpoint"] = event is not None
    per_op = {}
    for op in sorted({r["op"] for r in rows}):
        items = [r for r in rows if r["op"] == op]
        failures = {}
        for r in items:
            reason = r.get("error") and "network: " + r["error"] or (r.get("status") not in (200, None) and f"http {r['status']}") or (r.get("ok") is False and r.get("answer_error")) or None
            if reason:
                failures[reason] = failures.get(reason, 0) + 1
        per_op[op] = dict(n=len(items), round_trip_ms=dist([r["round_trip_ms"] for r in items]), pre_send_ms=dist([r["pre_send_ms"] for r in items]),
                          uplink_ms=dist([r["uplink_ms"] for r in items]), no_answer=sum(1 for r in items if r["round_trip_ms"] is None),
                          not_seen_by_endpoint=sum(1 for r in items if not r["reached_endpoint"]), failures=failures)
    # How often a drive command reached the endpoint while the key was held (nominal period 140 ms). Refused commands count
    # too: the one that arrives after an input expiry is refused, and its gap is the one that let the input expire.
    drive_times = sorted(e["t"] * 1000 for e in requests if e.get("op") == "drive" and t_from <= e["t"] * 1000 <= t_to)
    gaps = []
    holds = [(c["t_keydown"], c["t_keyup"]) for c in (drive_log or []) if c.get("t_keydown") and c.get("t_keyup")]
    for start, end in holds:
        inside = [t for t in drive_times if start <= t <= end + 500]
        gaps += [b - a for a, b in zip(inside, inside[1:])]
    states = {}
    for e in events:
        if e.get("kind") == "state" and t_from <= e["t"] * 1000 <= t_to:
            states[e.get("reason")] = states.get(e.get("reason"), 0) + 1
    # STOP: from the click on the page's STOP button.
    stop_rows = [r for r in rows if r["op"] == "stop"]
    stops = []
    for when, name in marks:
        if name not in ("click:stop", "click:frame_stop") or not t_from <= when <= t_to:
            continue
        row = next((r for r in stop_rows if 0 <= r["t_call"] - when < 250 and not r.get("claimed")), None)
        if row is None:
            stops.append(dict(t=when, request=None))
            continue
        row["claimed"] = True
        stops.append(dict(t=when, click_to_request_ms=row["t_call"] - when,
                          click_to_endpoint_ms=round(row["uplink_ms"] + row["t_call"] - when, 1) if row["uplink_ms"] is not None else None,
                          click_to_answer_ms=round(row["round_trip_ms"] + row["t_call"] - when, 1) if row["round_trip_ms"] is not None else None,
                          pre_send_ms=row["pre_send_ms"]))
    cycles = drive_log or []
    return dict(
        requests=len(rows), per_op=per_op,
        command_period_at_endpoint_ms=dict(dist(gaps), nominal=140, over_input_ttl=sum(1 for g in gaps if g > input_ttl_ms), input_ttl_ms=input_ttl_ms),
        endpoint_state_reasons=states,
        stop=dict(clicks=len(stops), click_to_request_ms=dist([s.get("click_to_request_ms") for s in stops]),
                  click_to_endpoint_ms=dist([s.get("click_to_endpoint_ms") for s in stops]),
                  click_to_answer_ms=dist([s.get("click_to_answer_ms") for s in stops]),
                  pre_send_ms=dist([s.get("pre_send_ms") for s in stops]),
                  not_answered=sum(1 for s in stops if s.get("click_to_answer_ms") is None),
                  not_seen_by_endpoint=sum(1 for s in stops if s.get("click_to_endpoint_ms") is None)),
        cycles=dict(n=len(cycles), activation_failed=sum(1 for c in cycles if not c.get("armed_at")),
                    activation_ms=dist([c["armed_at"] - c["t_arm_click"] for c in cycles if c.get("armed_at")]),
                    control_lost_while_holding=sum(1 for c in cycles if c.get("armed_at") and c.get("lease_at_hold_end") is False)))


# ---------------------------------------------------------------- link, stalls, processes
def link_window(link, t_from, t_to):
    if not link:
        return None
    out = dict(profile={k: link.get(k) for k in ("rate_kbps", "up_rate_kbps", "rtt_ms", "queue_bytes", "stall_every_s", "stall_ms", "setup_rtts", "segment_bytes")},
               connections=link.get("connections"))
    span = max(1.0, (t_to - t_from) / 1000)
    for direction in ("down", "up"):
        series = link[direction].get("per_second") or []
        inside = [count for sec, count in series if t_from / 1000 <= sec < t_to / 1000]
        out[direction] = dict(kB_per_s=round(sum(inside) / span / 1000, 2), kbit_per_s=round(sum(inside) * 8 / span / 1000, 1),
                              busiest_second_kB=round(max(inside, default=0) / 1000, 1), max_queue_delay_s=link[direction].get("max_queue_delay_s"),
                              share_of_rate_pct=round(100 * sum(inside) * 8 / span / 1000 / (link.get("rate_kbps") or 1), 1))
    out["stalls_in_window"] = [[a, b] for a, b in link.get("stalls_wall", []) if t_from / 1000 <= a and b <= t_to / 1000]
    return out


FRESH_HORIZON_MS = 15000.0  # how long after a stall a stream is watched for data made after the stall


def stall_recovery(client, stalls, t_from, t_to):
    """After each stall: how long until each stream delivered again, and how old that first delivery was.

    Two more numbers tell backlog from recovery (a message row is [arrival, publish stamp, bytes]):
      backlog_messages   messages that arrived after the stall but were made before it ended (old data delivered late)
      fresh_after_ms     stall end until the first message arrived that was made after the stall ended
    A stream is watched until the next stall starts, at most FRESH_HORIZON_MS. A stall with no fresh message in that
    time counts in not_fresh_within_horizon (not when the record itself ends earlier).
    """
    arrivals = {}
    for s in client["samples"]:
        if not s:
            continue
        for k, rows in s["arrivals"].items():
            arrivals.setdefault(k, []).extend(rows)
    record_end = max((r[0] for rows in arrivals.values() for r in rows), default=0)
    stalls = sorted(stalls)
    out = {}
    for stream, rows in arrivals.items():
        rows.sort()
        waits, ages, worst_after, backlog, fresh, not_fresh = [], [], [], [], [], 0
        for index, (start, end) in enumerate(stalls):
            end_ms = end * 1000
            watch_end = end_ms + FRESH_HORIZON_MS
            if index + 1 < len(stalls):
                watch_end = min(watch_end, stalls[index + 1][0] * 1000)
            watched = [r for r in rows if end_ms <= r[0] < watch_end and r[1]]
            first_fresh = next((r for r in watched if r[1] >= end_ms), None)
            if first_fresh is not None:
                fresh.append(first_fresh[0] - end_ms)
            elif record_end >= watch_end:
                not_fresh += 1
            if first_fresh is not None or record_end >= watch_end:
                backlog.append(sum(1 for r in watched if r[1] < end_ms))
            after = [r for r in rows if r[0] >= end_ms and r[0] < end_ms + 5000]
            if not after:
                continue
            waits.append(after[0][0] - end_ms)
            ages.append(after[0][0] - after[0][1])
            within = [r for r in after if r[0] < end_ms + 3000] or after[:1]  # the first message may come later than 3 s
            worst_after.append(max(r[0] - r[1] for r in within))
        if waits or backlog:
            out[stream] = dict(stalls=len(waits), first_message_after_ms=dist(waits), age_of_first_message_ms=dist(ages),
                               worst_age_within_3s_ms=dist(worst_after), backlog_messages=dist(backlog), fresh_after_ms=dist(fresh),
                               not_fresh_within_horizon=not_fresh, fresh_horizon_ms=FRESH_HORIZON_MS)
    return out


def summarize_cpu(rows, t_from, t_to):
    rows = [r for r in rows if t_from / 1000 <= r["t"] <= t_to / 1000]
    if not rows:
        return None
    out = dict(samples=len(rows), host_cpu_pct=dict(mean=round(statistics.mean(r["host_cpu_pct"] for r in rows), 1),
                                                    max=max(r["host_cpu_pct"] for r in rows), ncpu=rows[0]["ncpu"]), procs={}, chrome={})
    for name in rows[0]["procs"]:
        cpu = [r["procs"][name]["cpu_pct"] for r in rows if r["procs"].get(name)]
        rss = [r["procs"][name]["rss_mb"] for r in rows if r["procs"].get(name)]
        if cpu:
            out["procs"][name] = dict(cpu_pct_mean=round(statistics.mean(cpu), 1), cpu_pct_p95=pct(cpu, 95), rss_mb=[rss[0], rss[-1]])
    for role in {k for r in rows for k in r["chrome"]}:
        cpu = [r["chrome"][role]["cpu_pct"] for r in rows if role in r["chrome"]]
        rss = [r["chrome"][role]["rss_mb"] for r in rows if role in r["chrome"]]
        out["chrome"][role] = dict(cpu_pct_mean=round(statistics.mean(cpu), 1), cpu_pct_p95=pct(cpu, 95), rss_mb_first_max_last=[rss[0], max(rss), rss[-1]])
    return out


def host_pauses(rows, limit_s=3.0):
    """Gaps in the once-per-second CPU samples: the whole PC stopped (sleep, suspend). A run with one is not used."""
    return [[round(a["t"], 1), round(b["t"] - a["t"], 1)] for a, b in zip(rows, rows[1:]) if b["t"] - a["t"] > limit_s]


def summarize_process_stalls(folder, t_from, t_to):
    out = {}
    for path in sorted(Path(folder).glob("stall_*.jsonl")):
        rows = [r for r in read_jsonl(path) if t_from / 1000 <= r["t"] <= t_to / 1000]
        if rows:
            out[path.stem[6:]] = dict(seconds=len(rows), max_ms=max(r["max_ms"] for r in rows), p95_of_1s_max_ms=pct([r["max_ms"] for r in rows], 95),
                                      over_100ms_per_min=round(sum(r["over100"] for r in rows) / (len(rows) / 60), 1))
    return out


# ---------------------------------------------------------------- one run
def analyze(folder):
    folder = Path(folder)
    raw = read_json(folder / "RAW.json")
    run = read_json(folder / "run.json", {})
    if raw is None:
        return dict(schema="ubts.slow_link.summary", version=1, run=run, error="RAW.json is missing (the browser part did not finish)",
                    host_pauses=host_pauses(read_jsonl(folder / "cpu.jsonl")))
    marks = {m["mark"].split(":")[0]: m["t"] for m in raw.get("phase_marks", [])}
    route = raw["config"].get("route", "/rover-control")
    clients = raw.get("clients", [])
    summary = dict(schema="ubts.slow_link.summary", version=1, run=run, chrome=raw.get("chrome"), clock_check=raw.get("clock"),
                   error=raw.get("error"), notes=raw.get("notes"), lost_tabs=raw.get("lost_tabs") or [],
                   host_pauses=host_pauses(read_jsonl(folder / "cpu.jsonl")))
    link_stats = read_json(folder / "link.json")
    link = (link_stats or {}).get("links", [None])[0]
    summary["load"] = {name: summarize_load(load, route) for name, load in (clients[0]["loads"].items() if clients else [])}
    for name, load in (clients[0]["loads"].items() if clients else []):
        if load.get("nav_at") and (load.get("done_at") or load.get("t")):
            summary["load"][name]["link"] = link_window(link, load["nav_at"], load.get("done_at") or load["t"])
    t_from, t_to = marks.get("measure_start"), marks.get("measure_end")
    if t_from and t_to and clients:
        live = dict(duration_s=round((t_to - t_from) / 1000, 1))
        hide_at = marks.get("client0_hidden")
        if hide_at:
            live["visible_phase"] = dict(duration_s=round((hide_at - t_from) / 1000, 1), clients=[summarize_client(clients[0], t_from, hide_at - 500)],
                                         link=link_window(link, t_from, hide_at))
            live["hidden_phase"] = dict(duration_s=round((t_to - hide_at) / 1000, 1), clients=[summarize_client(clients[0], hide_at + 3000, t_to)],
                                        link=link_window(link, hide_at + 3000, t_to))
        live["clients"] = [summarize_client(c, t_from, t_to) for c in clients]
        live["series"] = growth_series(clients[0], t_from)
        live["link"] = link_window(link, t_from, t_to)
        if link and live["link"]["stalls_in_window"]:
            live["after_stall"] = stall_recovery(clients[0], live["link"]["stalls_in_window"], t_from, t_to)
        live["cpu"] = summarize_cpu(read_jsonl(folder / "cpu.jsonl"), t_from, t_to)
        live["process_stalls"] = summarize_process_stalls(folder, t_from, t_to)
        if raw["config"].get("phases", {}).get("drive"):
            proof = read_json(folder / "control_endpoint.json", {})
            ttl = (proof.get("core_limits") or {}).get("input_ttl_s")
            page_marks = [m for s in clients[0]["samples"] if s for m in s.get("marks", [])]
            live["control"] = summarize_control(clients[0]["samples"], read_jsonl(folder / "control_events.jsonl"), page_marks,
                                                raw.get("drive_log"), t_from, t_to, ttl * 1000 if ttl else INPUT_TTL_MS)
            live["control"]["limits"] = dict(core=proof.get("core_limits"), bridge=proof.get("bridge_limits"))
        if "live_map_start" in raw:
            live["live_map_start"] = raw["live_map_start"]
        summary["live"] = live
    summary["bridges"] = {path.stem[7:]: {k: v for k, v in read_json(path).items() if k not in ("pid",)} for path in sorted(folder.glob("bridge_*.json"))}
    return summary


if __name__ == "__main__":
    print(json.dumps(analyze(sys.argv[1]), ensure_ascii=False, indent=1))
