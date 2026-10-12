#!/usr/bin/env python3
"""Write a display-only recording_clock.json from recorded receiver UTC.

The archive name, metadata, bag and every sensor timestamp stay untouched:
the dashboard shows display_started_at next to the original host time. The
offset is the median of (NAV-PVT receiver UTC - driver host receipt) over
distinct epochs that pass the same validity rules as the start-clock
snapshot (valid date/time, fully resolved, tAcc <= 50 ms). An existing
recording_clock.json is never overwritten.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import statistics
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard_server.recording_clock import clock_epoch, clock_sample, timestamp  # noqa: E402

MIN_EPOCHS = 5
MAX_SPREAD_S = 0.2
MAX_HOST_JUMP_S = 0.25


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _samples(archive):
    """Every recorded /gps/data sample: unthrottled mapping source + sidecar."""
    mapping = archive / "mapping_source/sensors.jsonl.gz"
    if mapping.is_file():
        with gzip.open(mapping, "rt", encoding="utf-8") as stream:
            for line in stream:
                if '"/gps/data"' not in line[:48]:  # skip large scan/IMU rows unparsed
                    continue
                try:
                    row = json.loads(line)
                    if row.get("topic") == "/gps/data":
                        yield json.loads(row["message"]["data"])
                except (ValueError, KeyError, TypeError):
                    continue
    sidecar = archive / "gps.jsonl"
    if sidecar.is_file():
        with sidecar.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    yield json.loads(line)["sample"]
                except (ValueError, KeyError, TypeError):
                    continue


def audit(archive):
    archive = Path(archive)
    metadata = json.loads((archive / "metadata.json").read_text(encoding="utf-8"))
    original = metadata.get("started_at")
    if metadata.get("id") != archive.name or timestamp(original) is None:
        return dict(ok=False, reason="invalid_metadata")
    snapshot = metadata.get("clock_quality_snapshot") or {}
    if snapshot.get("clock_quality") == "gnss_receiver_estimated":
        return dict(ok=False, reason="start_snapshot_already_gnss_estimated")
    epochs = {}
    tacc = []
    for sample in _samples(archive):
        if not isinstance(sample, dict) or sample.get("protocol") != "UBX_NAV_PVT":
            continue
        epoch = clock_epoch(clock_sample(sample))
        received = sample.get("received_at")
        if epoch is None or type(received) not in (int, float):
            continue
        epochs.setdefault(epoch, float(received))  # first receipt of an epoch
        tacc.append(sample.get("gps_time_accuracy_s"))
    rows = sorted(epochs.items())
    if len(rows) < MIN_EPOCHS:
        return dict(ok=False, reason="insufficient_valid_receiver_epochs", epochs=len(rows))
    for (e0, r0), (e1, r1) in zip(rows, rows[1:]):
        if abs((e1 - e0) - (r1 - r0)) > MAX_HOST_JUMP_S:
            return dict(ok=False, reason="host_clock_jump_inside_recording", epochs=len(rows))
    offsets = [e - r for e, r in rows]
    spread = max(offsets) - min(offsets)
    if spread > MAX_SPREAD_S:
        return dict(ok=False, reason="offset_spread_too_large", spread_s=spread, epochs=len(rows))
    offset = statistics.median(offsets)
    display = datetime.fromtimestamp(timestamp(original) + offset, timezone.utc).isoformat()
    sources = {name: _sha(archive / name) for name in ("mapping_source/sensors.jsonl.gz", "gps.jsonl")
               if (archive / name).is_file()}
    return dict(ok=True, value=dict(
        schema="ubts.recording_clock.v1", original_started_at=original, offset_s=offset,
        display_started_at=display,
        evidence=dict(
            source="recorded_receiver_UTC_minus_host_receipt", protocol="UBX_NAV_PVT", epochs=len(rows),
            spread_s=spread, offset_min_s=min(offsets), offset_max_s=max(offsets),
            first_receiver_utc=datetime.fromtimestamp(rows[0][0], timezone.utc).isoformat(),
            last_receiver_utc=datetime.fromtimestamp(rows[-1][0], timezone.utc).isoformat(),
            receiver_time_valid_flags_recorded=True, receiver_time_accuracy_max_s=max(tacc),
            start_snapshot_reason=snapshot.get("reason"), source_sha256=sources,
            transport_latency_verified=False, host_clock_sync_verified=False,
            original_clock_and_playback_preserved=True, generator="scripts/recording_clock_audit.py",
            generated_at=datetime.now(timezone.utc).isoformat())))


def write(archive, value):
    target = Path(archive) / "recording_clock.json"
    if target.exists():
        return dict(ok=False, reason="recording_clock_json_exists_not_overwritten")
    temp = target.with_name(f".recording_clock.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    try:
        os.link(temp, target)  # complete file appears atomically; never replaces
    except FileExistsError:
        return dict(ok=False, reason="recording_clock_json_exists_not_overwritten")
    finally:
        temp.unlink()
    return dict(ok=True, path=str(target))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = {}
    for archive in args.archives:
        result = audit(archive)
        if result.get("ok") and args.write:
            result["write"] = write(archive, result["value"])
        report[archive.name] = result
    print(json.dumps(report, indent=1, allow_nan=False))
