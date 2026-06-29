const state = {
  telemetry: null,
  plan: null,
  history: {
    cpu: [],
    memory: [],
    gpu: [],
    temp: [],
    rx: [],
    tx: [],
    dockerCpu: [],
    dockerMemory: [],
  },
};

const MAX_POINTS = 90;
const $ = (id) => document.getElementById(id);

function text(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function fmtPercent(value, digits = 1) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(digits)}%`;
}

function fmtBytes(value) {
  if (value === null || value === undefined) return "--";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = Number(value);
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtBps(value) {
  if (value === null || value === undefined) return "--";
  return `${fmtBytes(value)}/s`;
}

function fmtTemp(value) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(1)}°C`;
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "--";
  const s = Math.max(0, Number(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : `${h}h ${m}m`;
}

async function getJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function currentRoute() {
  return ["/", "/system", "/network", "/docker", "/research"].includes(location.pathname) ? location.pathname : "/";
}

function renderRoute() {
  const route = currentRoute();
  document.querySelectorAll("[data-page]").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === route);
  });
  document.querySelectorAll("[data-route]").forEach((link) => {
    link.classList.toggle("active", link.dataset.route === route);
  });
}

async function loadPlan() {
  state.plan = await getJson("/api/plan");
  renderPlan(state.plan);
}

async function tick() {
  try {
    state.telemetry = await getJson("/api/telemetry");
    pushHistory(state.telemetry);
    renderTelemetry(state.telemetry);
    setConnection("ok", "online");
  } catch (err) {
    setConnection("bad", "offline");
    console.error(err);
  }
}

function pushHistory(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const cpu = sys.cpu?.total_percent;
  const memory = sys.memory?.percent;
  const gpu = sys.gpu_percent;
  const temp = sys.temperature_c;
  const dockerCpu = docker.summary?.cpu_percent;
  const dockerMemory = docker.summary?.memory_limit ? (docker.summary.memory_usage / docker.summary.memory_limit) * 100 : null;
  addPoint("cpu", cpu);
  addPoint("memory", memory);
  addPoint("gpu", gpu);
  addPoint("temp", temp);
  addPoint("rx", net.total_rx_bps);
  addPoint("tx", net.total_tx_bps);
  addPoint("dockerCpu", dockerCpu);
  addPoint("dockerMemory", dockerMemory);
}

function addPoint(key, value) {
  const list = state.history[key];
  list.push(value === null || value === undefined ? null : Number(value));
  if (list.length > MAX_POINTS) list.shift();
}

function setConnection(kind, label) {
  const el = $("connectionState");
  el.className = `status-pill ${kind}`;
  el.textContent = label;
}

function renderTelemetry(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const services = data.services || {};

  text("updatedAt", new Date((data.meta?.generated_at || Date.now() / 1000) * 1000).toLocaleString("ko-KR"));
  text("overviewCpu", fmtPercent(sys.cpu?.total_percent));
  text("overviewLoad", `load ${(sys.loadavg || [0])[0]?.toFixed ? sys.loadavg[0].toFixed(2) : "--"}`);
  text("overviewMemory", fmtPercent(sys.memory?.percent));
  text("overviewMemoryText", `${fmtBytes(sys.memory?.used)} / ${fmtBytes(sys.memory?.total)}`);
  text("overviewNetwork", `${fmtBps(net.total_rx_bps)} RX`);
  text("overviewNetworkText", `${fmtBps(net.total_tx_bps)} TX`);
  text("overviewDocker", docker.available ? `${docker.summary?.running || 0}/${docker.summary?.total || 0}` : "N/A");
  text("overviewDockerText", docker.available ? "running / total" : docker.error || "socket unavailable");
  text("hostName", sys.hostname || "--");
  text("hostUptime", fmtDuration(sys.os_uptime_s));
  text("hostTemp", fmtTemp(sys.temperature_c));
  text("hostIp", firstActiveIp(net));

  text("systemCpuValue", fmtPercent(sys.cpu?.total_percent));
  text("systemMemoryValue", fmtPercent(sys.memory?.percent));
  text("systemGpuValue", fmtPercent(sys.gpu_percent));
  text("systemTempValue", fmtTemp(sys.temperature_c));
  setProgress("memoryBar", sys.memory?.percent);
  text("memoryDetail", `${fmtBytes(sys.memory?.used)} used · ${fmtBytes(sys.memory?.available)} available · ${fmtBytes(sys.memory?.total)} total`);

  text("networkThroughput", `${fmtBps(net.total_rx_bps)} RX · ${fmtBps(net.total_tx_bps)} TX`);
  text("dockerContainerCount", docker.available ? docker.summary?.total ?? "--" : "N/A");
  text("dockerRunningText", docker.available ? `${docker.summary?.running || 0} running · ${docker.summary?.stopped || 0} stopped` : docker.error || "--");
  text("dockerImageCount", docker.available ? docker.engine?.images ?? "--" : "N/A");
  text("dockerVersion", docker.available ? `Docker ${docker.engine?.server_version || "--"}` : "--");
  text("dockerCpuTotal", fmtPercent(docker.summary?.cpu_percent));
  text("dockerCpuChartValue", fmtPercent(docker.summary?.cpu_percent));
  const dockerMemPercent = docker.summary?.memory_limit ? (docker.summary.memory_usage / docker.summary.memory_limit) * 100 : null;
  text("dockerMemTotal", fmtPercent(dockerMemPercent));
  text("dockerMemChartValue", fmtPercent(dockerMemPercent));
  text("dockerMemText", `${fmtBytes(docker.summary?.memory_usage)} / ${fmtBytes(docker.summary?.memory_limit)}`);

  text("roverState", data.rover?.state || "연결 전");
  text("fusionState", data.fusion?.state || "연결 전");
  text("experimentState", data.experiment?.state || "연결 전");

  renderServices(services);
  renderSensors(data.sensors || {});
  renderCores(sys.cpu?.cores || []);
  renderTemperatures(sys.temperatures || []);
  renderDisks(sys.disks || []);
  renderInterfaces(net.interfaces || []);
  renderContainers(docker);
  drawCharts();
}

function firstActiveIp(network) {
  for (const iface of network.interfaces || []) {
    if (iface.name !== "lo" && iface.ipv4?.length) return `${iface.name} ${iface.ipv4[0]}`;
  }
  return "--";
}

function setProgress(id, value) {
  const fill = $(`${id}`)?.querySelector("i");
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, Number(value || 0)))}%`;
}

function renderServices(services) {
  const rows = Object.entries(services).map(([key, item]) => ({
    title: serviceLabel(key),
    sub: [item.host, item.port ? `:${item.port}` : "", item.state].filter(Boolean).join(" "),
    ok: Boolean(item.ok),
    value: item.ok ? "OK" : "대기",
  }));
  renderStatusRows("overviewServices", rows);
}

function renderSensors(sensors) {
  const rows = Object.entries(sensors).map(([key, item]) => ({
    title: sensorLabel(key),
    sub: [item.topic, item.state].filter(Boolean).join(" · "),
    ok: Boolean(item.ok),
    value: item.rate_hz ? `${Number(item.rate_hz).toFixed(1)} Hz` : "연결 전",
  }));
  renderStatusRows("sensorList", rows);
}

function renderStatusRows(targetId, rows) {
  const target = $(targetId);
  if (!target) return;
  target.innerHTML = "";
  for (const row of rows) {
    const div = document.createElement("div");
    div.className = "status-item";
    div.innerHTML = `
      <i class="status-dot ${row.ok ? "ok" : ""}"></i>
      <span><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.sub || "")}</small></span>
      <b>${escapeHtml(row.value || "")}</b>
    `;
    target.append(div);
  }
}

function renderCores(cores) {
  const target = $("coreList");
  if (!target) return;
  target.innerHTML = "";
  for (const core of cores) {
    const div = document.createElement("div");
    div.className = "core";
    div.innerHTML = `<span>${escapeHtml(core.name)}</span><div class="progress"><i style="width:${Number(core.percent || 0)}%"></i></div><strong>${fmtPercent(core.percent)}</strong>`;
    target.append(div);
  }
}

function renderTemperatures(rows) {
  const target = $("temperatureList");
  if (!target) return;
  target.innerHTML = "";
  for (const row of rows) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${row.name} ${fmtTemp(row.temperature_c)}`;
    target.append(chip);
  }
}

function renderDisks(disks) {
  const target = $("diskList");
  if (!target) return;
  target.innerHTML = "";
  for (const disk of disks) {
    const div = document.createElement("div");
    div.className = "disk-row";
    div.innerHTML = `
      <div><strong>${escapeHtml(disk.mount)}</strong><small>${fmtBytes(disk.used)} / ${fmtBytes(disk.total)}</small></div>
      <div class="progress"><i style="width:${Number(disk.percent || 0)}%"></i></div>
      <b>${fmtPercent(disk.percent)}</b>
    `;
    target.append(div);
  }
}

function renderInterfaces(interfaces) {
  const target = $("interfaceList");
  if (!target) return;
  target.innerHTML = "";
  for (const iface of interfaces) {
    const div = document.createElement("article");
    div.className = "interface-card";
    div.innerHTML = `
      <div class="card-title">
        <strong>${escapeHtml(iface.name)}</strong>
        <span class="tag ${iface.state === "up" ? "ok" : ""}">${escapeHtml(iface.state || "unknown")}</span>
      </div>
      <dl class="detail-list compact">
        <div><dt>IPv4</dt><dd>${escapeHtml((iface.ipv4 || []).join(", ") || "--")}</dd></div>
        <div><dt>MAC</dt><dd>${escapeHtml(iface.mac || "--")}</dd></div>
        <div><dt>Link speed</dt><dd>${iface.speed_mbps ? `${iface.speed_mbps} Mbps` : "--"}</dd></div>
        <div><dt>RX / TX</dt><dd>${fmtBps(iface.rx_bps)} / ${fmtBps(iface.tx_bps)}</dd></div>
      </dl>
    `;
    target.append(div);
  }
}

function renderContainers(docker) {
  const target = $("containerList");
  if (!target) return;
  target.innerHTML = "";
  if (!docker.available) {
    target.innerHTML = `<div class="empty-state">${escapeHtml(docker.error || "Docker 정보를 읽을 수 없습니다.")}</div>`;
    return;
  }
  for (const container of docker.containers || []) {
    const row = document.createElement("div");
    row.className = "container-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(container.name || container.id)}</strong>
        <small>${escapeHtml(container.image || "")}</small>
      </div>
      <span class="tag ${container.state === "running" ? "ok" : ""}">${escapeHtml(container.state || "--")}</span>
      <b>${fmtPercent(container.cpu_percent)}</b>
      <b>${fmtBytes(container.memory_usage)}</b>
      <small>${escapeHtml(container.status || "")}</small>
    `;
    target.append(row);
  }
}

function renderPlan(plan) {
  const target = $("milestoneList");
  if (!target) return;
  target.innerHTML = "";
  for (const item of plan.milestones || []) {
    const div = document.createElement("div");
    div.className = "milestone";
    div.innerHTML = `<strong>${escapeHtml(item.month)}</strong><p>${escapeHtml(item.label)}</p><small class="${escapeHtml(item.status)}">${statusLabel(item.status)}</small>`;
    target.append(div);
  }
}

function statusLabel(status) {
  return { done: "완료", in_progress: "진행 중", planned: "예정" }[status] || status;
}

function sensorLabel(key) {
  return { lidar: "LiDAR", camera: "Camera", imu: "IMU", encoder: "Encoder", mavlink: "MAVLink" }[key] || key;
}

function serviceLabel(key) {
  return { dashboard: "Dashboard", cloudflared: "Cloudflare", ros2_bridge: "ROS2 Bridge", data_recorder: "Data Recorder" }[key] || key;
}

function drawCharts() {
  drawMultiLine("chartOverviewSystem", [
    { values: state.history.cpu, color: "#70e2b4", max: 100 },
    { values: state.history.memory, color: "#68d8ee", max: 100 },
  ]);
  drawMultiLine("chartOverviewNetwork", [
    { values: normalizeBps(state.history.rx), color: "#70e2b4", max: 100 },
    { values: normalizeBps(state.history.tx), color: "#f2b84b", max: 100 },
  ]);
  drawMultiLine("chartCpu", [{ values: state.history.cpu, color: "#70e2b4", max: 100 }]);
  drawMultiLine("chartMemory", [{ values: state.history.memory, color: "#68d8ee", max: 100 }]);
  drawMultiLine("chartGpu", [{ values: state.history.gpu, color: "#d977bc", max: 100 }]);
  drawMultiLine("chartTemp", [{ values: state.history.temp, color: "#f2b84b", max: Math.max(90, ...state.history.temp.filter((v) => v !== null)) }]);
  drawMultiLine("chartNetwork", [
    { values: normalizeBps(state.history.rx), color: "#70e2b4", max: 100 },
    { values: normalizeBps(state.history.tx), color: "#f2b84b", max: 100 },
  ]);
  drawMultiLine("chartDockerCpu", [{ values: state.history.dockerCpu, color: "#70e2b4", max: 100 }]);
  drawMultiLine("chartDockerMem", [{ values: state.history.dockerMemory, color: "#68d8ee", max: 100 }]);
}

function normalizeBps(values) {
  const max = Math.max(1, ...values.filter((v) => v !== null));
  return values.map((value) => (value === null ? null : (value / max) * 100));
}

function drawMultiLine(id, series) {
  const canvas = $(id);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createLinearGradient(0, 0, 0, h);
  gradient.addColorStop(0, "rgba(255,255,255,0.045)");
  gradient.addColorStop(1, "rgba(255,255,255,0.01)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  drawGrid(ctx, w, h);
  for (const item of series) drawLine(ctx, item.values, item.color, item.max || 100, w, h);
}

function drawGrid(ctx, w, h) {
  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const y = (h / 4) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawLine(ctx, values, color, max, w, h) {
  const points = values.map((value, index) => {
    if (value === null || Number.isNaN(value)) return null;
    const x = values.length <= 1 ? w : (index / (MAX_POINTS - 1)) * w;
    const safe = Math.max(0, Math.min(max, value));
    const y = h - (safe / max) * (h - 18) - 9;
    return { x, y };
  });
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.shadowColor = color;
  ctx.shadowBlur = 10;
  ctx.beginPath();
  let started = false;
  for (const point of points) {
    if (!point) {
      started = false;
      continue;
    }
    if (!started) {
      ctx.moveTo(point.x, point.y);
      started = true;
    } else {
      ctx.lineTo(point.x, point.y);
    }
  }
  ctx.stroke();
  ctx.restore();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[ch]);
}

window.addEventListener("popstate", renderRoute);
document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link) return;
  event.preventDefault();
  history.pushState({}, "", link.getAttribute("href"));
  renderRoute();
  drawCharts();
});

renderRoute();
loadPlan().catch(console.error);
tick();
setInterval(tick, 1000);
