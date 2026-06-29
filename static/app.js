const state = {
  telemetry: null,
  plan: null,
};

const $ = (id) => document.getElementById(id);

function text(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function fmtPercent(value, digits = 1) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(digits)}%`;
}

function fmtRatio(value) {
  return value === null || value === undefined ? "--" : Number(value).toFixed(2);
}

function fmtTemp(value) {
  return value === null || value === undefined ? "N/A" : `${Number(value).toFixed(1)}°C`;
}

function fmtLoad(value) {
  if (!Array.isArray(value) || !value.length) return "load N/A";
  return `load ${Number(value[0]).toFixed(2)}`;
}

function fmtMeters(value, digits = 2) {
  return `${Number(value || 0).toFixed(digits)} m`;
}

function fmtSeconds(value) {
  const seconds = Math.max(0, Number(value || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
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
    setConnection("ok", "online");
  } catch (err) {
    setConnection("bad", "offline");
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
  const mission = data.mission || {};
  const env = data.environment || {};

  text("updatedAt", new Date((data.meta?.generated_at || Date.now() / 1000) * 1000).toLocaleString("ko-KR"));
  text("missionName", mission.name || "과수원 행간 중심선 검증");
  text("missionStage", mission.stage || "--");
  text("missionProgress", fmtPercent(mission.progress_percent));
  text("missionRow", `${mission.active_row || "--"} / ${mission.total_rows || "--"}`);
  text("missionOperator", mission.operator || "--");

  text("cpuPercent", fmtPercent(sys.cpu_percent));
  text("loadAvg", fmtLoad(sys.loadavg));
  text("temperature", fmtTemp(sys.temperature_c));
  text("powerMode", `${sys.power_mode || "N/A"} · ${sys.throttle_state || "N/A"}`);
  text("hostname", sys.hostname || "--");

  text("speed", `${Number(rover.speed_mps || 0).toFixed(2)} m/s`);
  text("roverState", rover.state || "--");
  text("battery", fmtPercent(rover.battery_percent));
  text("estop", `E-stop ${rover.estop ? "ON" : "OFF"}`);
  text("roverMode", rover.mode || "--");
  text("autonomy", rover.autonomy_enabled ? "enabled" : "standby");
  text("pose", `x ${Number(rover.pose?.x || 0).toFixed(2)}, y ${Number(rover.pose?.y || 0).toFixed(2)}, yaw ${Number(rover.pose?.yaw_deg || 0).toFixed(1)}°`);
  text("motorState", rover.motor_state || "--");
  text("targetSpeed", `${Number(mission.target_speed_mps || 0).toFixed(2)} m/s`);
  text("nextAction", mission.next_action || "--");

  setGauge("lidarReliability", "lidarReliabilityValue", fusion.lidar_reliability);
  setGauge("visionReliability", "visionReliabilityValue", fusion.vision_reliability);
  text("fusionState", fusion.fusion_state || "--");
  text("lidarWeight", fmtRatio(fusion.lidar_weight));
  text("visionWeight", fmtRatio(fusion.vision_weight));
  text("rowDetectionCount", fusion.row_detection_count ?? "--");
  text("sensorLatency", `${Number(fusion.sensor_latency_ms || 0).toFixed(1)} ms`);
  text("centerlineError", fmtMeters(fusion.centerline_error_m, 3));
  text("trackingError", `tracking ${fmtMeters(fusion.path_tracking_error_m, 3)}`);

  text("runtime", fmtSeconds(exp.runtime_s));
  text("distance", `${Number(exp.distance_m || 0).toFixed(1)} m`);
  text("rowsCompleted", `${exp.rows_completed || 0} / ${mission.total_rows || 3}`);
  text("coverage", `coverage ${fmtPercent(exp.map_coverage_percent)}`);
  text("dataQuality", fmtPercent(exp.data_quality_percent));
  text("sessionId", exp.session_id || "--");
  text("gpsDependency", exp.gps_dependency || "--");
  text("centerlineMae", fmtMeters(exp.centerline_mae_m, 3));
  text("trackingRmse", fmtMeters(exp.tracking_rmse_m, 3));

  text("illumination", `${Number(env.illumination || 0).toFixed(0)} lx`);
  text("canopyDensity", fmtRatio(env.canopy_density));
  text("slope", `${Number(env.slope_deg || 0).toFixed(1)}°`);
  text("surface", env.surface || "--");
  text("networkState", env.network || "--");

  renderStatusList("sensorList", sensors, sensorLabel, sensorSubtitle);
  renderStatusList("serviceList", data.services || {}, serviceLabel, serviceSubtitle);
  renderAlerts(data.alerts || []);
  drawMap(data.map);
}

function setGauge(barId, valueId, value) {
  const safe = Math.max(0, Math.min(1, Number(value || 0)));
  const bar = $(barId);
  if (bar) bar.style.width = `${safe * 100}%`;
  text(valueId, safe.toFixed(2));
}

function renderStatusList(targetId, entries, labeler, subtitler) {
  const target = $(targetId);
  if (!target) return;
  target.innerHTML = "";
  for (const [key, item] of Object.entries(entries)) {
    const row = document.createElement("div");
    row.className = "status-item";
    const dot = document.createElement("i");
    dot.className = `status-dot ${item.ok ? "ok" : ""}`;
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = labeler(key);
    const sub = document.createElement("span");
    sub.textContent = subtitler(key, item);
    body.append(title, sub);
    const rate = document.createElement("div");
    rate.className = "status-rate";
    rate.textContent = item.rate_hz ? `${Number(item.rate_hz).toFixed(1)} Hz` : item.state || item.mode || "standby";
    row.append(dot, body, rate);
    target.append(row);
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

function sensorSubtitle(_key, sensor) {
  return [sensor.topic, sensor.model, sensor.mode].filter(Boolean).join(" · ") || "not wired";
}

function serviceLabel(key) {
  return {
    dashboard: "Dashboard",
    cloudflared: "Cloudflare Tunnel",
    ros2_bridge: "ROS2 Bridge",
    data_recorder: "Data Recorder",
  }[key] || key;
}

function serviceSubtitle(_key, service) {
  return [service.host, service.port ? `:${service.port}` : "", service.state].filter(Boolean).join(" ");
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
    done: "complete",
    in_progress: "active",
    planned: "planned",
  }[status] || status;
}

function drawMap(map) {
  const canvas = $("mapCanvas");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  drawBackdrop(ctx, w, h);

  if (!map || !map.bounds) return;
  const b = map.bounds;
  const sx = w / (b.x_max - b.x_min);
  const sy = h / (b.y_max - b.y_min);
  const toPx = (p) => ({
    x: (p.x - b.x_min) * sx,
    y: h - (p.y - b.y_min) * sy,
  });

  drawGrid(ctx, w, h, b, sx, sy);
  drawRoute(ctx, map.route || [], toPx, "#f2b84b", 5, [12, 14]);
  drawRoute(ctx, map.centerline || [], toPx, "#d977bc", 6);
  drawPoints(ctx, map.scan || [], toPx, "#68d8ee", 2.8, 0.64);
  drawPoints(ctx, map.trees || [], toPx, "#70c96f", 5.2, 0.92);
  drawPoints(ctx, map.clutter || [], toPx, "#9a7a52", 7, 0.9);
  drawRobot(ctx, map.robot || { x: 0, y: 0, yaw_deg: 0 }, toPx);
}

function drawBackdrop(ctx, w, h) {
  const gradient = ctx.createLinearGradient(0, 0, w, h);
  gradient.addColorStop(0, "#101615");
  gradient.addColorStop(0.58, "#17201d");
  gradient.addColorStop(1, "#111312");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
}

function drawGrid(ctx, w, h, b, sx, sy) {
  ctx.save();
  ctx.strokeStyle = "rgba(243, 246, 239, 0.08)";
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

function drawRoute(ctx, points, toPx, color, width, dash = []) {
  if (points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.setLineDash(dash);
  ctx.shadowColor = color;
  ctx.shadowBlur = 12;
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

function drawPoints(ctx, points, toPx, color, radius, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur = 10;
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
  ctx.fillStyle = "#f3f6ef";
  ctx.strokeStyle = "#111312";
  ctx.lineWidth = 2;
  ctx.shadowColor = "rgba(243, 246, 239, 0.56)";
  ctx.shadowBlur = 18;
  ctx.beginPath();
  ctx.moveTo(18, 0);
  ctx.lineTo(-12, -11);
  ctx.lineTo(-8, 0);
  ctx.lineTo(-12, 11);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

loadPlan().catch(console.error);
tick();
setInterval(tick, 2000);
