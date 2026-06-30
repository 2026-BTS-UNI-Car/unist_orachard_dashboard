import * as THREE from "/static/vendor/three.module.min.js";

const DPR_LIMIT = 1.6;

const view = {
  canvas: null,
  renderer: null,
  scene: null,
  camera: null,
  gridGroup: null,
  pointCloud: null,
  roverMarker: null,
  range: 12,
  yaw: -0.65,
  pitch: 0.72,
  distance: 15,
  pointers: new Map(),
  pinchDistance: 0,
  renderFrame: 0,
  lastScan: null,
};

function init() {
  view.canvas = document.getElementById("lidarScanCanvas3d");
  if (!view.canvas) return false;
  try {
    view.renderer = new THREE.WebGLRenderer({
      canvas: view.canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
  } catch (error) {
    view.renderer = null;
    drawFallback("이 브라우저에서 3D 렌더링을 열 수 없습니다.");
    return false;
  }
  view.renderer.setClearColor(0x000000, 0);
  view.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, DPR_LIMIT));
  if (THREE.SRGBColorSpace) view.renderer.outputColorSpace = THREE.SRGBColorSpace;

  view.scene = new THREE.Scene();
  view.camera = new THREE.PerspectiveCamera(46, 1, 0.02, 220);
  view.gridGroup = new THREE.Group();
  view.scene.add(view.gridGroup);
  addRoverMarker();
  bindControls();
  rebuildGrid(view.range);
  resize();
  scheduleRender();
  return true;
}

function bindControls() {
  const canvas = view.canvas;
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    view.pointers.set(event.pointerId, pointer(event));
    if (view.pointers.size === 2) view.pinchDistance = pointerDistance([...view.pointers.values()]);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!view.pointers.has(event.pointerId)) return;
    const previous = view.pointers.get(event.pointerId);
    const current = pointer(event);
    view.pointers.set(event.pointerId, current);
    if (view.pointers.size === 1) {
      view.yaw -= (current.x - previous.x) * 0.006;
      view.pitch = clamp(view.pitch + (current.y - previous.y) * 0.004, 0.22, 1.32);
      scheduleRender();
      return;
    }
    if (view.pointers.size >= 2) {
      const points = [...view.pointers.values()];
      const nextDistance = pointerDistance(points);
      if (view.pinchDistance > 0) {
        const ratio = view.pinchDistance / Math.max(1, nextDistance);
        setDistance(view.distance * ratio);
      }
      view.pinchDistance = nextDistance;
    }
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
    canvas.addEventListener(name, (event) => {
      view.pointers.delete(event.pointerId);
      view.pinchDistance = view.pointers.size === 2 ? pointerDistance([...view.pointers.values()]) : 0;
    });
  }
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    setDistance(view.distance * (event.deltaY > 0 ? 1.08 : 0.92));
  }, { passive: false });
}

function pointer(event) {
  return { x: event.clientX, y: event.clientY };
}

function pointerDistance(points) {
  if (points.length < 2) return 0;
  const dx = points[0].x - points[1].x;
  const dy = points[0].y - points[1].y;
  return Math.hypot(dx, dy);
}

function setDistance(value) {
  view.distance = clamp(value, Math.max(3.2, view.range * 0.22), Math.max(24, view.range * 2.4));
  scheduleRender();
}

function setScan(result) {
  view.lastScan = result || null;
  if (!view.renderer && !init()) {
    drawFallback("WebGL을 사용할 수 없어 2D 지도에서 스캔을 확인하세요.");
    return;
  }
  const points = result?.ok ? result.points || [] : [];
  const metrics = result?.metrics || {};
  const maxPointRange = points.reduce((max, point) => Math.max(max, Number(point.distance_m) || 0), 0);
  const range = Math.min(30, Math.max(4, (Number(metrics.max_distance_m) || maxPointRange || 6) * 1.18));
  if (Math.abs(range - view.range) > 0.4) {
    view.range = range;
    view.distance = clamp(view.distance, Math.max(5, range * 0.65), Math.max(24, range * 2.4));
    rebuildGrid(range);
  }
  updatePoints(points);
  resize();
  scheduleRender();
}

function updatePoints(points) {
  if (view.pointCloud) {
    view.pointCloud.geometry.dispose();
    view.pointCloud.material.dispose();
    view.scene.remove(view.pointCloud);
    view.pointCloud = null;
  }
  if (!points.length) return;

  const positions = new Float32Array(points.length * 3);
  const colors = new Float32Array(points.length * 3);
  const near = new THREE.Color(cssVar("--chart-cyan", "#38d8ff"));
  const far = new THREE.Color(cssVar("--chart-rose", "#f21b58"));
  const temp = new THREE.Color();

  points.forEach((point, index) => {
    const x = Number(point.x_m) || 0;
    const z = -(Number(point.y_m) || 0);
    const quality = clamp((Number(point.quality) || 0) / 63, 0, 1);
    const i = index * 3;
    positions[i] = x;
    positions[i + 1] = 0.035 + quality * 0.12;
    positions[i + 2] = z;
    temp.copy(near).lerp(far, clamp((Number(point.distance_m) || 0) / Math.max(view.range, 1), 0, 1));
    temp.lerp(new THREE.Color(cssVar("--chart-good", "#55e2b2")), quality * 0.28);
    colors[i] = temp.r;
    colors[i + 1] = temp.g;
    colors[i + 2] = temp.b;
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const material = new THREE.PointsMaterial({
    size: Math.max(0.035, view.range * 0.006),
    vertexColors: true,
    transparent: true,
    opacity: 0.94,
    sizeAttenuation: true,
    depthWrite: false,
  });
  view.pointCloud = new THREE.Points(geometry, material);
  view.scene.add(view.pointCloud);
}

function rebuildGrid(range) {
  clearGroup(view.gridGroup);
  const gridColor = new THREE.Color(cssVar("--chart-grid", "#2d3a68"));
  const axisBlue = new THREE.Color(cssVar("--chart-blue", "#2f76ff"));
  const axisRose = new THREE.Color(cssVar("--chart-rose", "#f21b58"));

  const grid = new THREE.GridHelper(range * 2, 12, gridColor, gridColor);
  grid.material.transparent = true;
  grid.material.opacity = 0.36;
  view.gridGroup.add(grid);

  for (let i = 1; i <= 4; i += 1) {
    const radius = (range / 4) * i;
    const points = [];
    for (let step = 0; step < 160; step += 1) {
      const theta = (step / 160) * Math.PI * 2;
      points.push(new THREE.Vector3(Math.cos(theta) * radius, 0.012, Math.sin(theta) * radius));
    }
    const ring = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: gridColor, transparent: true, opacity: 0.34 }),
    );
    view.gridGroup.add(ring);
  }

  const axes = [
    [new THREE.Vector3(-range, 0.03, 0), new THREE.Vector3(range, 0.03, 0), axisBlue],
    [new THREE.Vector3(0, 0.03, -range), new THREE.Vector3(0, 0.03, range), axisRose],
  ];
  for (const [start, end, color] of axes) {
    view.gridGroup.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([start, end]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.74 }),
    ));
  }
}

function addRoverMarker() {
  const body = new THREE.Mesh(
    new THREE.CylinderGeometry(0.22, 0.22, 0.08, 40),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(cssVar("--text", "#f7f9ff")), transparent: true, opacity: 0.9 }),
  );
  body.position.y = 0.07;
  view.roverMarker = body;
  view.scene.add(body);
}

function clearGroup(group) {
  while (group.children.length) {
    const child = group.children.pop();
    child.geometry?.dispose?.();
    child.material?.dispose?.();
  }
}

function resize() {
  if (!view.renderer || !view.canvas || view.canvas.hidden) {
    if (view.canvas && !view.renderer && !view.canvas.hidden) drawFallback("WebGL을 사용할 수 없습니다.");
    return;
  }
  const rect = view.canvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  view.renderer.setSize(width, height, false);
  view.camera.aspect = width / height;
  view.camera.updateProjectionMatrix();
  scheduleRender();
}

function scheduleRender() {
  if (view.renderFrame) return;
  view.renderFrame = requestAnimationFrame(render);
}

function render() {
  view.renderFrame = 0;
  if (!view.renderer || !view.scene || !view.camera || !view.canvas || view.canvas.hidden) return;
  const horizontal = Math.cos(view.pitch) * view.distance;
  view.camera.position.set(
    Math.sin(view.yaw) * horizontal,
    Math.sin(view.pitch) * view.distance,
    Math.cos(view.yaw) * horizontal,
  );
  view.camera.lookAt(0, 0, 0);
  view.renderer.render(view.scene, view.camera);
}

function refreshTheme() {
  if (!view.renderer) {
    view.canvas = view.canvas || document.getElementById("lidarScanCanvas3d");
    if (view.canvas && !view.canvas.hidden && view.lastScan) {
      drawFallback("WebGL을 사용할 수 없어 2D 지도에서 스캔을 확인하세요.");
    }
    return;
  }
  addRoverMarkerTheme();
  rebuildGrid(view.range);
  updatePoints(view.lastScan?.ok ? view.lastScan.points || [] : []);
  scheduleRender();
}

function drawFallback(message) {
  if (!view.canvas) return;
  const rect = view.canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, DPR_LIMIT);
  const width = Math.max(320, Math.round((rect.width || 520) * dpr));
  const height = Math.max(320, Math.round((rect.height || 520) * dpr));
  view.canvas.width = width;
  view.canvas.height = height;
  const ctx = view.canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const w = width / dpr;
  const h = height / dpr;
  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createLinearGradient(0, 0, w, h);
  gradient.addColorStop(0, cssVar("--chart-bg-top", "rgba(255,255,255,0.05)"));
  gradient.addColorStop(1, cssVar("--chart-bg-bottom", "rgba(255,255,255,0.01)"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,0.1)");
  ctx.lineWidth = 1;
  for (let i = 1; i <= 4; i += 1) {
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, (Math.min(w, h) * 0.42 * i) / 4, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = cssVar("--muted", "#a9b4d2");
  ctx.font = "900 14px Inter, Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(message, w / 2, h / 2);
}

function addRoverMarkerTheme() {
  if (!view.roverMarker) return;
  view.roverMarker.material.color = new THREE.Color(cssVar("--text", "#f7f9ff"));
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

window.ubtsLidar3d = {
  setScan,
  resize,
  render: scheduleRender,
  setTheme: refreshTheme,
  isReady: () => Boolean(view.renderer),
};

window.addEventListener("resize", resize);
function announceReady() {
  window.dispatchEvent(new CustomEvent("ubts-lidar3d-ready"));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", announceReady, { once: true });
} else {
  announceReady();
}
