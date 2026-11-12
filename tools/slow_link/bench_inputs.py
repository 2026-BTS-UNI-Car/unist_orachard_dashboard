#!/usr/bin/env python3
"""Inputs of a bench run: the source under test as an export of one commit, and generated stand-ins.

Nothing here writes into the repository work tree. The source is taken with
`git archive` from a commit, so uncommitted changes are never measured.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path

EXPORT_PATHS = ("dashboard_server", "scripts", "static", "config", "docs")
PRODUCT_PATHS = ("dashboard_server", "scripts", "static")
# Mean size of the rover camera's 1920x1080 JPEG frames, from docs/CAMERA_ORIGINAL1080_PREVIEW.md (112,011 B).
ORIGINAL_FRAME_MEAN_BYTES = 112_000
ORIGINAL_FRAME_COUNT = 150
SYNTHETIC_LAT, SYNTHETIC_LON = 36.0, 128.0  # same synthetic origin as replay_bridges.py


def git(repo, *args, binary=False):
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()


def describe_source(repo, rev):
    """Full commit hash plus the tree hashes of the product folders (equal trees mean equal product code)."""
    commit = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}")
    trees = {}
    for name in PRODUCT_PATHS:
        try:
            trees[name] = git(repo, "rev-parse", "--verify", f"{commit}:{name}")
        except subprocess.CalledProcessError:
            trees[name] = None
    return dict(rev=str(rev), commit=commit, short=commit[:7], trees=trees,
                subject=git(repo, "log", "-1", "--format=%s", commit), committed=git(repo, "log", "-1", "--format=%cI", commit))


def export_source(repo, commit, dest):
    """Extract the exported folders of one commit into a new folder `dest`."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    present = [name for name in EXPORT_PATHS if git(repo, "ls-tree", "--name-only", commit, name)]
    process = subprocess.Popen(["git", "-c", "core.autocrlf=false", "-C", str(repo), "archive", "--format=tar", commit, *present],
                               stdout=subprocess.PIPE)
    with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
        archive.extractall(dest, filter="data")
    if process.wait() != 0:
        raise RuntimeError(f"git archive failed for {commit}")
    (dest / "runtime").mkdir(exist_ok=True)
    return dest


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------- offline map stand-in
def _png_1x1():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


def _empty_pmtiles():
    """Valid PMTiles v3 archive without tiles: the base map draws its background only."""
    root = b"\x00"  # varint entry count 0
    metadata = json.dumps({"vector_layers": [], "name": "slow-link-bench-empty"}).encode()
    header_len = 127
    root_off, meta_off = header_len, header_len + len(root)
    data_off = meta_off + len(metadata)
    header = b"PMTiles" + bytes([3])
    header += struct.pack("<QQQQQQQQQQQ", root_off, len(root), meta_off, len(metadata), data_off, 0, data_off, 0, 0, 0, 0)
    header += bytes([1, 1, 1, 1, 0, 15])  # clustered, no internal / tile compression, vector tiles, zoom 0..15
    header += struct.pack("<iiii", int(124.0e7), int(33.0e7), int(131.0e7), int(38.6e7))
    header += bytes([10]) + struct.pack("<ii", int(SYNTHETIC_LON * 1e7), int(SYNTHETIC_LAT * 1e7))
    assert len(header) == header_len, len(header)
    return header + root + metadata


def make_map_stub(dest, app):
    """The files dashboard_server.offline_maps asks for, with no map content."""
    dest = Path(dest)
    (dest / "fonts/Noto Sans Regular").mkdir(parents=True, exist_ok=True)
    (dest / "sprites/v4").mkdir(parents=True, exist_ok=True)
    (dest / "korea.pmtiles").write_bytes(_empty_pmtiles())
    (dest / "fonts/Noto Sans Regular/0-255.pbf").write_bytes(b"")
    font = Path(app) / "static/vendor/korean-font/korean.woff2"
    (dest / "fonts/korean.woff2").write_bytes(font.read_bytes() if font.is_file() else b"")
    for theme in ("light", "dark"):
        (dest / f"sprites/v4/{theme}.json").write_bytes(b"{}")
        (dest / f"sprites/v4/{theme}.png").write_bytes(_png_1x1())
    manifest = {"source": "slow-link bench empty base map", "data_date": "stub", "bounds": [124.0, 33.0, 131.0, 38.6], "maxzoom": 15}
    (dest / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
    return dest


# ---------------------------------------------------------------- camera original-preview frames
def ensure_original_frames(work, camera_dir, mean_bytes=ORIGINAL_FRAME_MEAN_BYTES, count=ORIGINAL_FRAME_COUNT):
    """1920x1080 JPEG frames for the original-preview path, sized like the rover camera's frames.

    The fixtures only hold 1280x720 frames. They are enlarged and encoded at the
    JPEG quality whose mean size is closest to `mean_bytes`. The picture content
    is therefore softer than a real 1080p frame; the byte size is what matters to
    the link. The format line declares them as re-encoded, not as camera originals.
    """
    import cv2
    import numpy as np
    sources = sorted(Path(camera_dir).glob("*.jpg"))[:count]
    if not sources:
        raise SystemExit(f"no *.jpg frames in {camera_dir}")
    key = hashlib.sha256(("|".join(f"{p.name}:{p.stat().st_size}" for p in sources) + f"|{mean_bytes}").encode()).hexdigest()[:12]
    dest = Path(work) / "inputs" / f"camera1080_{key}"
    report = dest / "REPORT.json"
    if report.is_file():
        return dest, json.loads(report.read_text(encoding="utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    images = [cv2.resize(cv2.imread(str(path)), (1920, 1080), interpolation=cv2.INTER_CUBIC) for path in sources]
    sample = images[:: max(1, len(images) // 12)]

    def mean_size(quality):
        return float(np.mean([len(cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])[1]) for image in sample]))
    low, high = 20, 100
    while high - low > 1:
        middle = (low + high) // 2
        if mean_size(middle) < mean_bytes:
            low = middle
        else:
            high = middle
    quality = low if abs(mean_size(low) - mean_bytes) <= abs(mean_size(high) - mean_bytes) else high
    sizes = []
    for index, image in enumerate(images):
        data = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])[1].tobytes()
        (dest / f"{index:05d}.jpg").write_bytes(data)
        sizes.append(len(data))
    line = f"jpeg; reencoded_bgr_q{quality}; rotation=0; width=1920; height=1080"
    (dest / "FORMAT.txt").write_bytes((line + "\n").encode("utf-8"))
    info = dict(frames=len(sizes), quality=quality, mean_bytes=int(np.mean(sizes)), max_bytes=int(max(sizes)), target_mean_bytes=mean_bytes,
                format=line, source="1280x720 fixture frames enlarged to 1920x1080 and re-encoded")
    report.write_bytes(json.dumps(info, indent=1).encode("utf-8"))
    return dest, info


# ---------------------------------------------------------------- native raster kernel
def ensure_raster_kernel(work, app, python=sys.executable):
    """Build dashboard_server/native_raster.cc once per source hash (the dashboard image builds the same file).

    Returns (path or None, how). Without a compiler the dashboard uses its numpy reference path.
    """
    source = Path(app) / "dashboard_server/native_raster.cc"
    if not source.is_file():
        return None, "no native_raster.cc in this source"
    digest = sha256_file(source)
    target = Path(work) / "inputs" / f"raster_kernel_{digest[:16]}" / "_native_raster.so"
    if target.is_file():
        return target, "cached"
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        compiler = [str(python), "-m", "ziglang", "c++", "-target", "x86_64-windows-gnu", "-w"]
    else:
        compiler = ["g++", "-fPIC"]
    build = target.with_suffix(".build")
    try:
        result = subprocess.run(compiler + ["-O3", "-std=c++17", "-shared", str(source), "-o", str(build)], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"compiler not usable: {exc}"
    if result.returncode != 0 or not build.is_file():
        return None, "compile failed: " + result.stderr.decode("utf-8", "replace")[-300:]
    build.replace(target)
    return target, "built"
