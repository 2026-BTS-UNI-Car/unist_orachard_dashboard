const state = {
  telemetry: null,
  plan: null,
};

const $ = (id) => document.getElementById(id);

function text(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function fmtPercent(value) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(1)}%`;
}

function fmtTemp(value) {
  return value === null || value === undefined ? "N/A" : `${Number(value).toFixed(1)}°C`;
}

function fmtLoad(value) {
  if (!Array.isArray(value) || !value.length) return "N/A";
  return Number(value[0]).toFixed(2);
}

function fmtMeters(value) {
  return `${Number(value || 0).toFixed(2)} m`;
}

function fmtSeconds(value) {
  const seconds = Math.max(0, Number(value || 0));
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}분 ${s}초`;
}

async function getJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadPlan() {
  state.plan = await getJson("/api/plan");
  renderPlan(state.plan);
}

async function tick() {
  try {
    state.telemetry = await getJson("/api/telemetry");
    renderTelemetry(state.telemetry);
    setConnection("ok", "연결됨");
  } catch (err) {
    setConnection("bad", "연결 끊김");
    console.error(err);
  }
}

function setConnection(kind, label) {
  const el = $("connectionState");
  el.className = `status-pill ${kind}`;
  el.textContent = label;
}

function renderTelemetry(data) {
  const sys = data.system || {};
  const rover = data.rover || {};
  const sensors = data.sensors || {};
  const fusion = data.fusion || {};
  const exp = data.experiment || {};

  text("updatedAt", new Date((data.meta?.generated_at || Date.now() / 1000) * 1000).toLocaleString("ko-KR"));
  text("cpuPercent", fmtPercent(sys.cpu_percent));
  text("gpuPercent", sys.gpu_percent === null || sys.gpu_percent === undefined ? "N/A" : fmtPercent(sys.gpu_percent));
  text("memoryPercent", fmtPercent(sys.memory_percent));
  text("diskPercent", fmtPercent(sys.disk_percent));
  text("temperature", fmtTemp(sys.temperature_c));
  text("loadAvg", fmtLoad(sys.loadavg));
  text("hostname", sys.hostname || "--");
  text("powerMode", sys.power_mode || "N/A");
  text("throttleState", sys.throttle_state || "N/A");

  text("roverState", rover.state || "--");
  text("speed", `${Number(rover.speed_mps || 0).toFixed(2)} m/s`);
  text("battery", fmtPercent(rover.battery_percent));
  text("estop", rover.estop ? "ON" : "OFF");
  text("roverMode", rover.mode || "--");
  text("pose", `x ${Number(rover.pose?.x || 0).toFixed(2)}, y ${Number(rover.pose?.y || 0).toFixed(2)}, yaw ${Number(rover.pose?.yaw_deg || 0).toFixed(1)}°`);
  text("motorState", rover.motor_state || "--");

  renderSensors(sensors);
  setGauge("lidarReliability", "lidarReliabilityValue", fusion.lidar_reliability);
  setGauge("visionReliability", "visionReliabilityValue", fusion.vision_reliability);
  text("lidarWeight", Number(fusion.lidar_weight || 0).toFixed(2));
  text("visionWeight", Number(fusion.vision_weight || 0).toFixed(2));
  text("centerlineError", fmtMeters(fusion.centerline_error_m));
  text("trackingError", fmtMeters(fusion.path_tracking_error_m));

  text("runtime", fmtSeconds(exp.runtime_s));
  text("distance", `${Number(exp.distance_m || 0).toFixed(1)} m`);
  text("rowsCompleted", `${exp.rows_completed || 0} / 3`);
  text("coverage", fmtPercent(exp.map_coverage_percent));

  renderAlerts(data.alerts || []);
  drawMap(data.map);
}

function setGauge(barId, valueId, value) {
  const safe = Math.max(0, Math.min(1, Number(value || 0)));
  $(barId).style.width = `${safe * 100}%`;
  text(valueId, safe.toFixed(2));
}

function renderSensors(sensors) {
  const target = $("sensorList");
  target.innerHTML = "";
  for (const [key, sensor] of Object.entries(sensors)) {
    const item = document.createElement("div");
    item.className = "sensor-item";
    const dot = document.createElement("i");
    dot.className = `sensor-dot ${sensor.ok ? "ok" : ""}`;
    const body = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = sensorLabel(key);
    const topic = document.createElement("span");
    topic.textContent = `${sensor.topic || sensor.mode || "future"} · ${sensor.model || sensor.mode || ""}`;
    body.append(name, topic);
    const rate = document.createElement("div");
    rate.className = "sensor-rate";
    rate.textContent = sensor.rate_hz ? `${Number(sensor.rate_hz).toFixed(1)} Hz` : "대기";
    item.append(dot, body, rate);
    target.append(item);
  }
}

function sensorLabel(key) {
  return {
    lidar: "LiDAR",
    camera: "Camera",
    imu: "IMU",
    encoder: "Encoder",
    mavlink: "MAVLink",
  }[key] || key;
}

function renderAlerts(alerts) {
  const target = $("alerts");
  target.innerHTML = "";
  if (!alerts.length) {
    const div = document.createElement("div");
    div.className = "alert";
    div.textContent = "현재 알림 없음";
    target.append(div);
    return;
  }
  for (const alert of alerts) {
    const div = document.createElement("div");
    div.className = `alert ${alert.level || "info"}`;
    div.textContent = alert.message;
    target.append(div);
  }
}

function renderPlan(plan) {
  const target = $("milestoneList");
  target.innerHTML = "";
  for (const item of plan.milestones || []) {
    const div = document.createElement("div");
    div.className = "milestone";
    const month = document.createElement("strong");
    month.textContent = item.month;
    const label = document.createElement("p");
    label.textContent = item.label;
    const status = document.createElement("small");
    status.className = item.status;
    status.textContent = statusLabel(item.status);
    div.append(month, label, status);
    target.append(div);
  }
}

function statusLabel(status) {
  return {
    done: "완료",
    in_progress: "진행 중",
    planned: "예정",
  }[status] || status;
}

function drawMap(map) {
  const canvas = $("mapCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#dfe9e4";
  ctx.fillRect(0, 0, w, h);

  if (!map || !map.bounds) return;
  const b = map.bounds;
  const sx = w / (b.x_max - b.x_min);
  const sy = h / (b.y_max - b.y_min);
  const toPx = (p) => ({
    x: (p.x - b.x_min) * sx,
    y: h - (p.y - b.y_min) * sy,
  });

  drawGrid(ctx, w, h, b, sx, sy);
  drawRoute(ctx, map.route || [], toPx, "#1d72b8", 4);
  drawRoute(ctx, map.centerline || [], toPx, "#a43c76", 5);
  drawPoints(ctx, map.trees || [], toPx, "#3d7f45", 4);
  drawPoints(ctx, map.clutter || [], toPx, "#b7791f", 6);
  drawRobot(ctx, map.robot || { x: 0, y: 0, yaw_deg: 0 }, toPx);
}

function drawGrid(ctx, w, h, b, sx, sy) {
  ctx.save();
  ctx.strokeStyle = "rgba(60, 74, 88, 0.16)";
  ctx.lineWidth = 1;
  for (let x = Math.ceil(b.x_min / 2) * 2; x <= b.x_max; x += 2) {
    const px = (x - b.x_min) * sx;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, h);
    ctx.stroke();
  }
  for (let y = Math.ceil(b.y_min); y <= b.y_max; y += 1) {
    const py = h - (y - b.y_min) * sy;
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(w, py);
    ctx.stroke();
  }
  ctx.restore();
}

function drawRoute(ctx, points, toPx, color, width) {
  if (points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  const first = toPx(points[0]);
  ctx.moveTo(first.x, first.y);
  for (const point of points.slice(1)) {
    const p = toPx(point);
    ctx.lineTo(p.x, p.y);
  }
  ctx.stroke();
  ctx.restore();
}

function drawPoints(ctx, points, toPx, color, radius) {
  ctx.save();
  ctx.fillStyle = color;
  for (const point of points) {
    const p = toPx(point);
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawRobot(ctx, robot, toPx) {
  const p = toPx(robot);
  const yaw = (Number(robot.yaw_deg || 0) * Math.PI) / 180;
  ctx.save();
  ctx.translate(p.x, p.y);
  ctx.rotate(-yaw);
  ctx.fillStyle = "#18212a";
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(14, 0);
  ctx.lineTo(-10, -9);
  ctx.lineTo(-7, 0);
  ctx.lineTo(-10, 9);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

loadPlan().catch(console.error);
tick();
setInterval(tick, 2000);
