const ROUTES = ["/", "/connection", "/system", "/network", "/docker", "/fan", "/research", "/docs"];
const HISTORY_KEYS = ["cpu", "memory", "swap", "power", "temp", "rx", "tx", "dockerCpu", "dockerMemory", "fanTemp", "fanPwm"];
const CORE_COLORS = ["#55e2b2", "#38d8ff", "#2f76ff", "#8238c9", "#f21b58", "#f4bd4f", "#7dd3fc", "#fb7185"];
const MAX_POINTS = 90;
const POLL_INTERVAL_MS = 1000;
const DPR_LIMIT = 1.5;
const CPU_MODE_KEY = "ubts-cpu-chart-mode";
const LIDAR_RANGE_KEY = "ubts-lidar-map-range-v2";
const LIDAR_RANGE_AUTO_KEY = "ubts-lidar-map-range-auto-v2";
const LIDAR_POINTS_KEY = "ubts-lidar-points-v1";
const LIDAR_SPEC_MAX_POINTS = 3200;
const LIDAR_RECONNECT_DEBOUNCE_MS = 650;

const state = {
  telemetry: null,
  plan: null,
  docs: [],
  activeDoc: null,
  history: Object.fromEntries(HISTORY_KEYS.map((key) => [key, []])),
  visualHistory: Object.fromEntries(HISTORY_KEYS.map((key) => [key, []])),
  coreHistory: {},
  visualCoreHistory: {},
  cpuMode: "combined",
  lidarScan: null,
  lidarLive: {
    enabled: false,
    socket: null,
    connected: false,
    scanTopicAlive: false,
    source: "waiting",
    lastMessageAt: null,
    lastScanAgeSec: null,
    pointCount: 0,
    sourcePointCount: 0,
    requestedPointCount: 900,
    error: "",
    messageCount: 0,
    userStopped: false,
    reconnectTimer: null,
    pendingRestart: false,
  },
  lidarExport: {
    latestScan: null,
    lastUpdatedAt: null,
    source: "ros2_scan",
    frameId: "laser",
    format: "json",
  },
  lidarMap: {
    range: 25,
    displayRange: 25,
    auto: true,
    autoPending: true,
    minRange: 1,
    maxRange: 30,
  },
  lidarPoints: {
    value: 900,
    min: 50,
    max: LIDAR_SPEC_MAX_POINTS,
    step: 50,
  },
  selectedContainerId: null,
  containerDetail: null,
  containerLogs: null,
  containerRequestInFlight: false,
  containerLogInFlight: false,
  containerLogsUpdatedAt: null,
  chartShift: 0,
  chartShiftStartedAt: 0,
  animationFrame: null,
  apiLogCounter: 0,
  tickInFlight: false,
  telemetryFailures: 0,
};

const $ = (id) => document.getElementById(id);

function text(id, value) {
  const el = $(id);
  if (!el) return;
  const next = String(value);
  if (el.textContent !== next) el.textContent = next;
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

function fmtPower(value) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(2)} W`;
}

function fmtNumber(value, digits = 1) {
  return value === null || value === undefined || Number.isNaN(Number(value)) ? "--" : Number(value).toFixed(digits);
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "--";
  const s = Math.max(0, Number(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : `${h}h ${m}m`;
}

function memoryPercent(memory) {
  return memory?.display_percent ?? memory?.percent;
}

function memoryTotal(memory) {
  return memory?.display_total ?? memory?.total;
}

function memoryLabel(memory) {
  return memory?.display_label || `${fmtBytes(memoryTotal(memory))} 기준`;
}

function cpuLabel(core) {
  if (core?.label) return core.label;
  const name = core?.name || "";
  if (name.startsWith("cpu") && /^\d+$/.test(name.slice(3))) return `CPU ${Number(name.slice(3)) + 1}`;
  return name.toUpperCase() || "CPU";
}

function coreKey(core) {
  return core?.name || cpuLabel(core);
}

function debugLog(scope, message, detail) {
  if (!window.console?.debug) return;
  if (detail === undefined) {
    console.debug(`[UBTS:${scope}] ${message}`);
  } else {
    console.debug(`[UBTS:${scope}] ${message}`, detail);
  }
}

async function getJson(path, options = {}) {
  const started = performance.now();
  try {
    const res = await fetch(path, { cache: "no-store", signal: options.signal });
    const ms = Math.round(performance.now() - started);
    state.apiLogCounter += 1;
    if (path !== "/api/telemetry" || state.apiLogCounter % 10 === 0) {
      debugLog("api", `${path} -> ${res.status} (${ms}ms)`);
    }
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  } catch (err) {
    debugLog("api", `${path} failed`, err);
    throw err;
  }
}

function currentRoute() {
  if (location.pathname === "/docs" || location.pathname.startsWith("/docs/")) return "/docs";
  return ROUTES.includes(location.pathname) ? location.pathname : "/";
}

function renderRoute() {
  const route = currentRoute();
  document.querySelectorAll("[data-page]").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === route);
  });
  document.querySelectorAll("[data-route]").forEach((link) => {
    link.classList.toggle("active", link.dataset.route === route);
  });
  if (route === "/docs") renderDocsRoute();
  if (route === "/docker" && state.telemetry?.docker) {
    renderContainers(state.telemetry.docker);
    maybeRefreshContainerDetail(true);
  }
  if (route === "/connection") {
    renderActiveLidarMap();
    if (!state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
  } else if (state.lidarLive.socket || state.lidarLive.enabled) {
    closeLidarLive("Connection 페이지 밖에서는 LiDAR WebSocket을 닫습니다.", { userStopped: false });
  }
  requestChartAnimation();
}

function initMobileNav() {
  const button = $("mobileMenuButton");
  const backdrop = $("mobileNavBackdrop");
  button?.addEventListener("click", () => setMobileNav(!document.body.classList.contains("nav-open")));
  backdrop?.addEventListener("click", () => setMobileNav(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMobileNav(false);
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 1180) setMobileNav(false);
  });
}

function setMobileNav(open) {
  document.body.classList.toggle("nav-open", Boolean(open));
  const button = $("mobileMenuButton");
  if (button) {
    button.setAttribute("aria-expanded", String(Boolean(open)));
    button.setAttribute("aria-label", open ? "사이트맵 닫기" : "사이트맵 열기");
  }
}

function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem("ubts-theme");
  } catch (_) {
    stored = null;
  }
  const theme = stored || "dark";
  applyTheme(theme);
  $("themeToggle")?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("ubts-theme", theme);
  } catch (_) {
    // Ignore storage failures; theme switching should remain usable.
  }
  const isLight = theme === "light";
  const toggle = $("themeToggle");
  if (toggle) toggle.setAttribute("aria-pressed", String(isLight));
  text("themeLabel", isLight ? "Light" : "Dark");
  requestChartAnimation();
}

function initCpuMode() {
  try {
    state.cpuMode = localStorage.getItem(CPU_MODE_KEY) || "combined";
  } catch (_) {
    state.cpuMode = "combined";
  }
  document.querySelectorAll("[data-cpu-mode]").forEach((button) => {
    button.addEventListener("click", () => setCpuMode(button.dataset.cpuMode));
  });
  setCpuMode(state.cpuMode);
}

function setCpuMode(mode) {
  state.cpuMode = mode === "separate" ? "separate" : "combined";
  try {
    localStorage.setItem(CPU_MODE_KEY, state.cpuMode);
  } catch (_) {
    // Ignore storage failures; the visible toggle still works for this session.
  }
  document.querySelectorAll("[data-cpu-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.cpuMode === state.cpuMode);
  });
  const combined = $("cpuCombinedView");
  const separate = $("cpuSeparateView");
  if (combined) combined.hidden = state.cpuMode !== "combined";
  if (separate) separate.hidden = state.cpuMode !== "separate";
  text("cpuModeText", state.cpuMode === "separate" ? "코어별 개별 그래프" : "전체 코어 통합 그래프");
  requestChartAnimation();
}

function initLidarTest() {
  $("lidarTestButton")?.addEventListener("click", runLidarTest);
  $("lidarLiveButton")?.addEventListener("click", toggleLidarLive);
  $("lidarDownloadButton")?.addEventListener("click", downloadCurrentLidarMap);
  document.querySelectorAll("[data-lidar-download]").forEach((button) => {
    button.addEventListener("click", () => setLidarDownloadFormat(button.dataset.lidarDownload));
  });
  document.querySelectorAll("[data-lidar-range]").forEach((button) => {
    button.addEventListener("click", () => changeLidarRange(button.dataset.lidarRange));
  });
  $("lidarRangeSlider")?.addEventListener("input", (event) => {
    setLidarRange(Number(event.target.value), { auto: false, persist: true });
    requestChartAnimation();
  });
  document.querySelectorAll("[data-lidar-points]").forEach((button) => {
    button.addEventListener("click", () => changeLidarPointLimit(button.dataset.lidarPoints));
  });
  $("lidarPointSlider")?.addEventListener("input", (event) => {
    setLidarPointLimit(Number(event.target.value), { persist: true, reconnect: true });
  });
  initLidarRange();
  initLidarPointLimit();
  setLidarDownloadFormat(state.lidarExport.format);
  renderLidarLiveState();
}

function initLidarRange() {
  let stored = null;
  try {
    const rawRange = localStorage.getItem(LIDAR_RANGE_KEY);
    stored = rawRange === null ? null : Number(rawRange);
    const storedAuto = localStorage.getItem(LIDAR_RANGE_AUTO_KEY);
    state.lidarMap.auto = storedAuto === null ? true : storedAuto !== "0";
    state.lidarMap.autoPending = state.lidarMap.auto;
  } catch (_) {
    stored = null;
  }
  if (Number.isFinite(stored) && !state.lidarMap.auto) {
    state.lidarMap.range = clampLidarRange(stored);
    state.lidarMap.displayRange = state.lidarMap.range;
  }
  updateLidarRangeControls();
}

function changeLidarRange(action) {
  const current = state.lidarMap.range;
  let next = current;
  if (action === "in") next = current <= 5 ? current - 0.5 : current - 1;
  else if (action === "out") next = current < 5 ? current + 0.5 : current + 1;
  else {
    state.lidarMap.auto = true;
    state.lidarMap.autoPending = true;
    persistLidarRange();
    maybeAutoSetLidarRange(state.lidarScan, { force: true });
    updateLidarRangeControls();
    requestChartAnimation();
    return;
  }
  setLidarRange(next, { auto: false, persist: true });
  requestChartAnimation();
}

function setLidarRange(value, options = {}) {
  state.lidarMap.range = clampLidarRange(value);
  state.lidarMap.auto = Boolean(options.auto);
  state.lidarMap.autoPending = Boolean(options.autoPending);
  if (options.persist) persistLidarRange();
  updateLidarRangeControls();
}

function persistLidarRange() {
  try {
    localStorage.setItem(LIDAR_RANGE_KEY, String(state.lidarMap.range));
    localStorage.setItem(LIDAR_RANGE_AUTO_KEY, state.lidarMap.auto ? "1" : "0");
  } catch (_) {
    // Range control remains usable without localStorage.
  }
}

function clampLidarRange(value) {
  return Math.max(state.lidarMap.minRange, Math.min(state.lidarMap.maxRange, Number(value) || 25));
}

function updateLidarRangeControls() {
  const rangeLabel = `${fmtNumber(state.lidarMap.range, state.lidarMap.range % 1 ? 1 : 0)} m`;
  text("lidarRangeValue", state.lidarMap.auto ? `${rangeLabel} auto` : rangeLabel);
  const slider = $("lidarRangeSlider");
  if (slider && document.activeElement !== slider) slider.value = String(state.lidarMap.range);
  document.querySelectorAll("[data-lidar-range]").forEach((button) => {
    const action = button.dataset.lidarRange;
    button.disabled = (action === "in" && state.lidarMap.range <= state.lidarMap.minRange)
      || (action === "out" && state.lidarMap.range >= state.lidarMap.maxRange);
    if (action === "reset") button.textContent = state.lidarMap.auto ? "Auto" : "Auto";
  });
}

function initLidarPointLimit() {
  let stored = null;
  try {
    const raw = localStorage.getItem(LIDAR_POINTS_KEY);
    stored = raw === null ? null : Number(raw);
  } catch (_) {
    stored = null;
  }
  if (Number.isFinite(stored)) state.lidarPoints.value = clampLidarPointLimit(stored);
  updateLidarPointControls();
}

function changeLidarPointLimit(action) {
  const current = state.lidarPoints.value;
  let next = current;
  if (action === "down") next = current - state.lidarPoints.step;
  else if (action === "up") next = current + state.lidarPoints.step;
  else next = 900;
  setLidarPointLimit(next, { persist: true, reconnect: true });
}

function setLidarPointLimit(value, options = {}) {
  state.lidarPoints.value = clampLidarPointLimit(value);
  if (options.persist) {
    try {
      localStorage.setItem(LIDAR_POINTS_KEY, String(state.lidarPoints.value));
    } catch (_) {
      // Point limit remains session-local when storage is unavailable.
    }
  }
  updateLidarPointControls();
  if (options.reconnect && (state.lidarLive.socket || state.lidarLive.enabled)) scheduleLidarReconnectForSettings();
}

function clampLidarPointLimit(value) {
  const step = state.lidarPoints.step;
  const rounded = Math.round((Number(value) || 900) / step) * step;
  return Math.max(state.lidarPoints.min, Math.min(state.lidarPoints.max, rounded));
}

function updateLidarPointControls() {
  text("lidarPointValue", `${state.lidarPoints.value} pts`);
  const slider = $("lidarPointSlider");
  if (slider && document.activeElement !== slider) slider.value = String(state.lidarPoints.value);
  document.querySelectorAll("[data-lidar-points]").forEach((button) => {
    const action = button.dataset.lidarPoints;
    button.disabled = (action === "down" && state.lidarPoints.value <= state.lidarPoints.min)
      || (action === "up" && state.lidarPoints.value >= state.lidarPoints.max);
  });
}

async function loadPlan() {
  state.plan = await getJson("/api/plan");
  renderPlan(state.plan);
}

async function loadDocs() {
  const payload = await getJson("/api/docs");
  state.docs = payload.documents || [];
  renderDocsIndex(state.docs);
  renderDocsRoute();
}

async function tick() {
  if (state.tickInFlight) {
    debugLog("api", "/api/telemetry skipped: previous request still running");
    return;
  }
  state.tickInFlight = true;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 6000);
  try {
    state.telemetry = await getJson("/api/telemetry", { signal: controller.signal });
    pushHistory(state.telemetry);
    renderTelemetry(state.telemetry);
    state.telemetryFailures = 0;
    setConnection("ok", "online");
    requestChartAnimation();
  } catch (err) {
    state.telemetryFailures += 1;
    if (state.telemetryFailures >= 3) setConnection("bad", "offline");
    else debugLog("api", `/api/telemetry transient failure ${state.telemetryFailures}`, err);
  } finally {
    window.clearTimeout(timeout);
    state.tickInFlight = false;
  }
}

function pushHistory(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const fan = data.fan || {};
  const cores = sys.cpu?.cores || [];
  const dockerCpu = docker.summary?.cpu_percent;
  const dockerMemory = docker.summary?.memory_limit ? (docker.summary.memory_usage / docker.summary.memory_limit) * 100 : null;
  addPoint("cpu", sys.cpu?.total_percent);
  addPoint("memory", memoryPercent(sys.memory));
  addPoint("swap", sys.swap?.percent);
  addPoint("power", sys.power?.total_w);
  addPoint("temp", sys.temperature_c);
  addPoint("rx", net.total_rx_bps);
  addPoint("tx", net.total_tx_bps);
  addPoint("dockerCpu", dockerCpu);
  addPoint("dockerMemory", dockerMemory);
  addPoint("fanTemp", fan.temperature_c);
  addPoint("fanPwm", fan.pwm);
  for (const core of cores) addCorePoint(coreKey(core), core.percent);
  pruneCoreHistory(cores.map(coreKey));
  state.chartShift = 1;
  state.chartShiftStartedAt = performance.now();
}

function addPoint(key, value) {
  addPointTo(state.history, state.visualHistory, key, value);
}

function addCorePoint(key, value) {
  addPointTo(state.coreHistory, state.visualCoreHistory, key, value);
}

function addPointTo(historyStore, visualStore, key, value) {
  const target = historyStore[key] || [];
  const visual = visualStore[key] || [];
  historyStore[key] = target;
  visualStore[key] = visual;
  const numeric = value === null || value === undefined ? null : Number(value);
  if (target.length >= MAX_POINTS) {
    target.shift();
    visual.shift();
  }
  target.push(Number.isFinite(numeric) ? numeric : null);
  visual.push(Number.isFinite(numeric) ? numeric : null);
}

function pruneCoreHistory(keys) {
  const alive = new Set(keys);
  for (const key of Object.keys(state.coreHistory)) {
    if (!alive.has(key)) {
      delete state.coreHistory[key];
      delete state.visualCoreHistory[key];
    }
  }
}

function setConnection(kind, label) {
  const el = $("connectionState");
  if (!el) return;
  el.className = `status-pill ${kind}`;
  el.textContent = label;
}

function renderTelemetry(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const fan = data.fan || {};
  const services = data.services || {};
  const connection = data.connection || {};

  text("updatedAt", new Date((data.meta?.generated_at || Date.now() / 1000) * 1000).toLocaleString("ko-KR"));
  text("overviewCpu", fmtPercent(sys.cpu?.total_percent));
  text("overviewLoad", `load ${(sys.loadavg || [0])[0]?.toFixed ? sys.loadavg[0].toFixed(2) : "--"}`);
  text("overviewMemory", fmtPercent(memoryPercent(sys.memory)));
  text("overviewMemoryText", `${fmtBytes(sys.memory?.used)} / ${fmtBytes(memoryTotal(sys.memory))} · ${memoryLabel(sys.memory)}`);
  text("overviewNetwork", net.external_ip || "확인 중");
  text("overviewNetworkText", `host ${net.primary_ipv4 || "--"} · RX ${fmtBps(net.total_rx_bps)} · TX ${fmtBps(net.total_tx_bps)}`);
  text("overviewDocker", docker.available ? `${docker.summary?.running || 0}/${docker.summary?.total || 0}` : "N/A");
  text("overviewDockerText", docker.available ? "running / total" : docker.error || "socket unavailable");
  text("hostName", sys.hostname || "--");
  text("hostUptime", fmtDuration(sys.os_uptime_s));
  text("hostTemp", fmtTemp(sys.temperature_c));
  text("hostPower", fmtPower(sys.power?.total_w));
  text("hostExternalIp", net.external_ip || net.external_ip_error || "--");
  text("hostInternalIp", hostIpLabel(net));

  text("systemCpuValue", fmtPercent(sys.cpu?.total_percent));
  text("systemMemoryValue", fmtPercent(memoryPercent(sys.memory)));
  text("systemSwapValue", fmtPercent(sys.swap?.percent));
  text("systemNetworkValue", `${fmtBps(net.total_rx_bps)} / ${fmtBps(net.total_tx_bps)}`);
  text("systemPowerValue", fmtPower(sys.power?.total_w));
  setProgress("memoryBar", memoryPercent(sys.memory));
  text("memoryDetail", `${fmtBytes(sys.memory?.used)} used / ${fmtBytes(memoryTotal(sys.memory))} · ${memoryLabel(sys.memory)} · ${fmtBytes(sys.memory?.available)} available`);

  text("networkExternalIp", net.external_ip || "--");
  text("networkExternalIpText", net.external_ip ? "public endpoint" : "확인 대기");
  text("networkInternalIp", net.primary_ipv4 || "--");
  text("networkInternalIpText", [net.primary_interface, net.hostname].filter(Boolean).join(" · ") || "host route");
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
  renderFan(fan);

  renderServices(services);
  renderConnection(connection);
  renderSensors(data.sensors || {});
  renderCores(sys.cpu?.cores || []);
  renderCoreCharts(sys.cpu?.cores || []);
  renderTemperatures(sys.temperatures || []);
  renderDisks(sys.disks || []);
  renderInterfaces(net.interfaces || [], net.primary_interface);
  renderContainers(docker);
  maybeRefreshContainerDetail();
  renderChartReadouts(data);
}

async function runLidarTest() {
  const button = $("lidarTestButton");
  if (button) button.disabled = true;
  setTestResult("testing", "LiDAR serial port를 열지 않고 장치 표시와 ROS2 소유 상태만 확인합니다.");
  try {
    const res = await fetch("/api/lidar/test", { method: "POST", cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    renderLidarTest(payload);
    await tick();
  } catch (err) {
    setTestResult("bad", `상태 확인 실패: ${err.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

function toggleLidarLive() {
  if (state.lidarLive.connected || state.lidarLive.socket) {
    stopLidarLive("사용자가 실시간 연결을 중지했습니다.");
    return;
  }
  startLidarLive();
}

function startLidarLive() {
  if (currentRoute() !== "/connection") {
    debugLog("ws", "skip start outside /connection");
    return;
  }
  if (state.lidarLive.socket) return;
  clearLidarReconnectTimer();
  const url = lidarWebSocketUrl();
  state.lidarLive.enabled = true;
  state.lidarLive.userStopped = false;
  state.lidarLive.error = "";
  state.lidarLive.connected = false;
  state.lidarLive.scanTopicAlive = false;
  state.lidarLive.source = "waiting";
  state.lidarLive.requestedPointCount = state.lidarPoints.value;
  renderLidarLiveState("연결 중");
  setScanResult("testing", "LiDAR WebSocket 연결 중");
  debugLog("ws", `connecting ${url}`);
  try {
    const socket = new WebSocket(url);
    state.lidarLive.socket = socket;
    socket.addEventListener("open", () => {
      if (socket !== state.lidarLive.socket) return;
      state.lidarLive.connected = true;
      state.lidarLive.error = "";
      debugLog("ws", "open");
      renderLidarLiveState();
      if (state.lidarLive.pendingRestart) {
        state.lidarLive.pendingRestart = false;
        scheduleLidarReconnectForSettings(80);
      }
    });
    socket.addEventListener("message", (event) => {
      if (socket !== state.lidarLive.socket || currentRoute() !== "/connection") return;
      handleLidarLiveMessage(event.data);
    });
    socket.addEventListener("error", (event) => {
      if (socket !== state.lidarLive.socket) return;
      state.lidarLive.error = "WebSocket 연결 오류";
      debugLog("ws", "error", event);
      renderLidarLiveState();
    });
    socket.addEventListener("close", (event) => {
      if (socket !== state.lidarLive.socket) return;
      const shouldReconnect = state.lidarLive.enabled && !state.lidarLive.userStopped && currentRoute() === "/connection";
      state.lidarLive.socket = null;
      state.lidarLive.connected = false;
      state.lidarLive.scanTopicAlive = false;
      debugLog("ws", `close code=${event.code} reason=${event.reason || "-"}`);
      renderLidarLiveState(state.lidarLive.error || "disconnected");
      if (state.lidarLive.pendingRestart && shouldReconnect) {
        state.lidarLive.pendingRestart = false;
        scheduleLidarReconnectForSettings(120);
      } else if (shouldReconnect) {
        state.lidarLive.reconnectTimer = window.setTimeout(() => {
          state.lidarLive.reconnectTimer = null;
          if (currentRoute() === "/connection" && !state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
        }, 1500);
      }
    });
  } catch (err) {
    state.lidarLive.socket = null;
    state.lidarLive.connected = false;
    state.lidarLive.scanTopicAlive = false;
    state.lidarLive.source = "waiting";
    state.lidarLive.error = err.message;
    debugLog("ws", "create failed", err);
    renderLidarLiveState();
  }
}

function stopLidarLive(message = "disconnected") {
  closeLidarLive(message, { userStopped: true });
}

function closeLidarLive(message = "disconnected", options = {}) {
  const userStopped = options.userStopped !== undefined ? Boolean(options.userStopped) : true;
  if (options.clearReconnect !== false) clearLidarReconnectTimer();
  const socket = state.lidarLive.socket;
  state.lidarLive.enabled = false;
  state.lidarLive.userStopped = userStopped;
  state.lidarLive.socket = null;
  state.lidarLive.connected = false;
  state.lidarLive.scanTopicAlive = false;
  state.lidarLive.source = "waiting";
  state.lidarLive.error = "";
  state.lidarLive.pendingRestart = false;
  if (socket) {
    closeSocketAfterConnect(socket, message);
  }
  debugLog("ws", `stopped: ${message}`);
  renderLidarLiveState(message);
}

function clearLidarReconnectTimer() {
  if (!state.lidarLive.reconnectTimer) return;
  window.clearTimeout(state.lidarLive.reconnectTimer);
  state.lidarLive.reconnectTimer = null;
}

function closeSocketAfterConnect(socket, reason = "closed") {
  const closeReason = String(reason || "closed").slice(0, 120);
  try {
    if (socket.readyState === WebSocket.CONNECTING) {
      socket.addEventListener("open", () => socket.close(1000, closeReason), { once: true });
    } else if (socket.readyState === WebSocket.OPEN) {
      socket.close(1000, closeReason);
    } else if (socket.readyState === WebSocket.CLOSING || socket.readyState === WebSocket.CLOSED) {
      return;
    }
  } catch (err) {
    debugLog("ws", "socket close skipped", err);
  }
}

function scheduleLidarReconnectForSettings(delayMs = LIDAR_RECONNECT_DEBOUNCE_MS) {
  if (currentRoute() !== "/connection") return;
  if (!state.lidarLive.socket && !state.lidarLive.enabled) return;
  clearLidarReconnectTimer();
  state.lidarLive.pendingRestart = true;
  state.lidarLive.reconnectTimer = window.setTimeout(() => {
    state.lidarLive.reconnectTimer = null;
    restartLidarLiveForSettings();
  }, Math.max(0, delayMs));
  debugLog("ws", `settings restart scheduled max_points=${state.lidarPoints.value}`);
}

function restartLidarLiveForSettings() {
  if (currentRoute() !== "/connection") return;
  const wasEnabled = state.lidarLive.enabled && !state.lidarLive.userStopped;
  const socket = state.lidarLive.socket;
  if (socket?.readyState === WebSocket.CONNECTING) {
    state.lidarLive.pendingRestart = true;
    debugLog("ws", "settings restart deferred until socket opens");
    return;
  }
  clearLidarReconnectTimer();
  state.lidarLive.pendingRestart = false;
  if (socket) {
    state.lidarLive.socket = null;
    state.lidarLive.connected = false;
    state.lidarLive.scanTopicAlive = false;
    closeSocketAfterConnect(socket, "point limit changed");
  }
  if (wasEnabled) {
    window.setTimeout(() => {
      if (currentRoute() === "/connection" && !state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
    }, 160);
  }
}

function lidarWebSocketUrl() {
  const params = new URLSearchParams({ max_points: String(state.lidarPoints.value) });
  if (window.UBTS_LIDAR_WS_URL) {
    const separator = String(window.UBTS_LIDAR_WS_URL).includes("?") ? "&" : "?";
    return `${window.UBTS_LIDAR_WS_URL}${separator}${params.toString()}`;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws/lidar?${params.toString()}`;
}

function handleLidarLiveMessage(raw) {
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    state.lidarLive.error = "잘못된 WebSocket 메시지";
    debugLog("ws", "invalid message", raw);
    renderLidarLiveState();
    return;
  }
  state.lidarLive.lastMessageAt = Date.now();
  state.lidarLive.messageCount += 1;
  if (payload.type === "status") {
    state.lidarLive.lastScanAgeSec = payload.last_scan_age_sec;
    state.lidarLive.pointCount = payload.point_count || 0;
    state.lidarLive.sourcePointCount = payload.source_point_count || payload.point_count || 0;
    state.lidarLive.requestedPointCount = payload.requested_max_points || state.lidarPoints.value;
    state.lidarLive.scanTopicAlive = Boolean(payload.scan_topic_alive);
    state.lidarLive.source = payload.source || "waiting";
    state.lidarLive.error = payload.error || (payload.scan_topic_alive === false ? "ROS2 /scan 대기" : "");
    if (state.lidarLive.messageCount % 10 === 1 || payload.error) debugLog("ws", "status", payload);
    if (!state.lidarScan || !payload.scan_topic_alive) {
      setScanResult("testing", payload.error || "ROS2 /scan 수신 대기");
    }
    renderLidarLiveState();
    renderLiveSensorLinks();
    return;
  }
  if (payload.type === "scan") {
    const result = lidarScanFromLive(payload);
    maybeAutoSetLidarRange(result);
    state.lidarLive.lastScanAgeSec = 0;
    state.lidarLive.pointCount = result.metrics.rendered_points || result.points.length;
    state.lidarLive.sourcePointCount = result.metrics.source_points || state.lidarLive.pointCount;
    state.lidarLive.requestedPointCount = result.metrics.requested_points || state.lidarPoints.value;
    state.lidarLive.scanTopicAlive = true;
    state.lidarLive.source = payload.source || "ros2";
    state.lidarLive.error = "";
    state.lidarScan = result;
    if (state.lidarLive.messageCount % 10 === 1) debugLog("ws", "scan", { source: payload.source, points: result.points.length });
    renderLidarScan(result, "live_websocket");
    renderLidarLiveState();
    renderLiveSensorLinks();
    return;
  }
  if (payload.type === "socket") {
    state.lidarLive.error = payload.ok ? "" : payload.message || "socket status error";
    debugLog("ws", "socket", payload);
    renderLidarLiveState();
    renderLiveSensorLinks();
  }
}

function lidarScanFromLive(payload) {
  const points = (payload.points || []).map(normalizeLidarExportPoint).filter(Boolean);
  const distances = points.map((point) => point.distance_m).filter((value) => Number.isFinite(value));
  const metrics = {
    points: payload.point_count || points.length,
    source_points: payload.source_point_count || payload.point_count || points.length,
    requested_points: payload.requested_max_points || state.lidarPoints.value,
    rendered_points: points.length,
    min_distance_m: distances.length ? Math.min(...distances) : null,
    max_distance_m: distances.length ? Math.max(...distances) : null,
    coverage_deg: angleCoverage(points),
    range_limit_m: 30.0,
  };
  return {
    ok: true,
    state: "live_scan",
    protocol: "ROS2 /scan WebSocket",
    frame_id: payload.frame_id || "laser",
    stamp: payload.stamp,
    tested_at: Math.floor(Date.now() / 1000),
    points,
    metrics,
    source: "live_websocket",
  };
}

function maybeAutoSetLidarRange(result, options = {}) {
  if (!result?.ok || !result.points?.length) return;
  if (!state.lidarMap.auto && !options.force) return;
  if (!state.lidarMap.autoPending && !options.force) return;
  const suggested = suggestedLidarRange(result.points);
  if (!Number.isFinite(suggested)) return;
  state.lidarMap.range = clampLidarRange(suggested);
  state.lidarMap.auto = true;
  state.lidarMap.autoPending = false;
  persistLidarRange();
  updateLidarRangeControls();
  debugLog("lidar", "auto range selected", { range: state.lidarMap.range, points: result.points.length });
}

function suggestedLidarRange(points) {
  const extents = points
    .map((point) => Math.max(Math.abs(Number(point.x_m) || 0), Math.abs(Number(point.y_m) || 0)))
    .filter((value) => Number.isFinite(value) && value > 0.05)
    .sort((a, b) => a - b);
  if (!extents.length) return null;
  const p85 = quantile(extents, 0.85);
  const p95 = quantile(extents, 0.95);
  const denseFit = Math.max(p85 * 1.28, p95 * 1.12, 1);
  return Math.ceil(clampLidarRange(denseFit));
}

function quantile(sortedValues, q) {
  if (!sortedValues.length) return null;
  const index = Math.min(sortedValues.length - 1, Math.max(0, Math.floor((sortedValues.length - 1) * q)));
  return sortedValues[index];
}

function renderLidarLiveState(overrideLabel) {
  const button = $("lidarLiveButton");
  if (button) {
    button.textContent = state.lidarLive.socket && !state.lidarLive.connected
      ? "연결 중"
      : state.lidarLive.connected ? "실시간 중지" : "실시간 연결";
    button.classList.toggle("active", state.lidarLive.connected);
  }
  text("lidarLiveDevice", state.telemetry?.connection?.lidar?.device || "--");
  text("lidarLiveTopic", state.lidarLive.scanTopicAlive ? `${lidarSourceLabel(state.lidarLive.source)} live` : lidarSourceLabel(state.lidarLive.source));
  text("lidarLiveSocket", overrideLabel || (state.lidarLive.connected ? "connected" : state.lidarLive.error || "disconnected"));
  text("lidarLiveAge", lidarAgeLabel());
  const pointLabel = state.lidarLive.pointCount
    ? `${state.lidarLive.pointCount}/${state.lidarLive.requestedPointCount || state.lidarPoints.value}`
    : "--";
  text("lidarLivePoints", pointLabel);
}

function renderLiveSensorLinks() {
  if (state.telemetry?.connection?.sensors) {
    renderStatusRows("connectionSensors", sensorRows(state.telemetry.connection.sensors));
  }
  if (state.telemetry?.sensors) {
    renderSensors(state.telemetry.sensors);
  }
}

function lidarSourceLabel(source) {
  return { ros2: "ROS2 /scan", serial: "Serial direct", bridge: "Bridge", waiting: "대기" }[source] || source || "대기";
}

function lidarAgeLabel() {
  if (state.lidarLive.scanTopicAlive) return "live";
  const age = state.lidarLive.lastScanAgeSec;
  return age === null || age === undefined ? "--" : `${fmtNumber(age, 1)}s`;
}

function setLidarDownloadFormat(format) {
  state.lidarExport.format = ["json", "csv", "png"].includes(format) ? format : "json";
  document.querySelectorAll("[data-lidar-download]").forEach((button) => {
    button.classList.toggle("active", button.dataset.lidarDownload === state.lidarExport.format);
  });
  updateLidarDownloadState();
}

function updateLidarExport(result, source) {
  if (!result?.ok || !(result.points || []).length) {
    updateLidarDownloadState();
    return;
  }
  const points = (result.points || []).map(normalizeLidarExportPoint).filter(Boolean);
  const latest = { ...result, points };
  state.lidarExport.latestScan = latest;
  state.lidarExport.lastUpdatedAt = new Date();
  state.lidarExport.source = source || result.source || "ros2_scan";
  state.lidarExport.frameId = result.frame_id || "laser";
  updateLidarDownloadState();
}

function updateLidarDownloadState() {
  const button = $("lidarDownloadButton");
  if (!button) return;
  const hasData = Boolean(state.lidarExport.latestScan?.points?.length);
  button.disabled = !hasData;
  button.textContent = hasData ? `현재 지도 다운로드 (${state.lidarExport.format.toUpperCase()})` : "현재 지도 다운로드";
}

function downloadCurrentLidarMap() {
  if (!state.lidarExport.latestScan) return;
  const stamp = formatTimestampForFilename();
  const format = state.lidarExport.format;
  if (format === "png") {
    downloadLidarPng(`ubts_lidar_2d_view_${stamp}.png`);
    return;
  }
  const points = state.lidarExport.latestScan.points;
  if (format === "csv") {
    const rows = points.map(normalizeLidarExportPoint).filter(Boolean);
    downloadCsv(`ubts_lidar_2d_scan_${stamp}.csv`, rows);
    return;
  }
  downloadJson(`ubts_lidar_2d_scan_${stamp}.json`, lidarExportPayload(points));
}

function lidarExportPayload(points) {
  const scan = state.lidarExport.latestScan || {};
  const metrics = scan.metrics || {};
  const range = lidarRangeFor(scan);
  return {
    type: "ubts_lidar_2d_scan",
    version: 1,
    created_at: new Date().toISOString(),
    source: state.lidarExport.source,
    frame_id: state.lidarExport.frameId || "laser",
    coordinate_system: {
      x_m: "right",
      y_m: "front",
      angle_zero_deg: "front/up on dashboard after 180deg display correction",
      angle_direction: "clockwise on dashboard if using x=sin(theta), y=cos(theta)",
    },
    scan: {
      point_count: points.length,
      metrics,
      points: points.map(normalizeLidarExportPoint).filter(Boolean),
    },
    view: {
      range_m: range,
      grid_step_m: lidarGridStep(range),
    },
  };
}

function formatTimestampForFilename(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

function downloadBlob(filename, content, mimeType) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function downloadJson(filename, data) {
  downloadBlob(filename, JSON.stringify(data, null, 2), "application/json;charset=utf-8");
}

function downloadCsv(filename, rows) {
  const header = "angle_deg,distance_m,quality,x_m,y_m";
  const body = rows.map((point) => [
    point.angle_deg,
    point.distance_m,
    point.quality,
    point.x_m,
    point.y_m,
  ].join(","));
  downloadBlob(filename, [header, ...body].join("\n"), "text/csv;charset=utf-8");
}

function downloadLidarPng(filename) {
  const source = $("lidarScanCanvas");
  if (!source) return;
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  const ctx = canvas.getContext("2d");
  const isLight = document.documentElement.dataset.theme === "light";
  ctx.fillStyle = isLight ? "#ffffff" : "#050816";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(source, 0, 0);
  canvas.toBlob((blob) => {
    if (blob) downloadBlob(filename, blob, "image/png");
  }, "image/png");
}

function normalizeLidarExportPoint(point) {
  if (!point) return null;
  const angleDeg = Number(point.angle_deg);
  const distanceM = Number(point.distance_m);
  const xM = Number(point.x_m);
  const yM = Number(point.y_m);
  if (!Number.isFinite(distanceM) || !Number.isFinite(xM) || !Number.isFinite(yM)) return null;
  const displayAngle = Number.isFinite(angleDeg) ? (angleDeg + 180) % 360 : null;
  const displayX = -xM;
  const displayY = -yM;
  return {
    angle_deg: displayAngle === null ? null : Number(displayAngle.toFixed(3)),
    distance_m: Number(distanceM.toFixed(3)),
    quality: Number.isFinite(Number(point.quality)) ? Number(point.quality) : 0,
    x_m: Number(displayX.toFixed(3)),
    y_m: Number(displayY.toFixed(3)),
  };
}

function angleCoverage(points) {
  const bins = new Set();
  for (const point of points) {
    if (Number.isFinite(point.angle_deg)) bins.add(Math.floor(point.angle_deg) % 360);
  }
  return bins.size;
}

function renderConnection(connection) {
  const lidar = connection.lidar || {};
  const bridge = connection.lidar_bridge || {};
  const last = lidar.last_test || {};
  const lastScan = lidar.last_scan || {};
  const scanLive = Boolean(state.lidarLive.scanTopicAlive || bridge.scan_topic_alive);
  text("connectionLidarState", scanLive ? "수신 중" : (lidar.present ? lidarStateLabel(lidar.state) : "미인식"));
  text("connectionLidarDevice", lidar.device || "--");
  text("connectionLidarProtocol", "ROS2 /scan");
  text("connectionLidarSubtext", "subscribe-only");
  text("connectionLidarHealth", scanLive ? "수신 중" : (lidar.present ? "ROS2 대기" : "--"));
  text("connectionLidarHealthText", scanLive ? "ROS2 /scan live" : "ROS2 /scan 대기");
  text("lidarDeviceDetail", lidar.present ? shortPath(lidar.device) : "장치 미인식");
  text("lidarByIdDetail", lidar.by_id ? shortPath(lidar.by_id, 42) : "stable ID 대기");
  text("lidarDriverDetail", driverLabel(lidar.driver_hint));
  text("lidarLastTestDetail", lidarLastTestLabel(last));
  text("lidarPolicyDetail", policyLabel(lidar.conflict_policy));
  renderStatusRows("connectionServices", serviceRows(connection.services || {}));
  renderStatusRows("connectionSensors", sensorRows(connection.sensors || {}));
  renderLidarLiveState();
  if (last.ok || last.message) renderLidarTest(last);
  if (lastScan.ok || lastScan.message) {
    if (!state.lidarScan || (lastScan.tested_at || 0) >= (state.lidarScan.tested_at || 0)) {
      state.lidarScan = lastScan;
      renderLidarScan(lastScan, "ros2_scan");
    }
  }
}

function renderLidarTest(result) {
  if (!result) return;
  const state = result.ok ? "ok" : "bad";
  setTestResult(state, lidarTestMessage(result));
}

function setTestResult(kind, message) {
  const target = $("lidarTestResult");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
}

function renderLidarScan(result, source = result?.source || "ros2_scan") {
  if (!result) return;
  const metrics = result.metrics || {};
  const points = result.points || [];
  const ok = Boolean(result.ok && points.length);
  const renderedPoints = metrics.rendered_points || points.length || 0;
  const requestedPoints = metrics.requested_points || state.lidarPoints.value;
  const sourcePoints = metrics.source_points || metrics.points || points.length || 0;
  const message = ok
    ? `실시간 수신 중 · 최대 ${requestedPoints}개 표시 · ${fmtNumber(metrics.coverage_deg, 0)}도 커버리지`
    : cleanLidarError(result, "실시간 LiDAR 데이터를 기다리는 중입니다.");
  setScanResult(ok ? "ok" : "bad", message);
  const colors = chartColors();
  renderReadout("lidarScanStats", [
    { label: "Rendered", value: String(renderedPoints), sub: `limit ${requestedPoints} · source ${sourcePoints}`, color: colors.cyan },
    { label: "Coverage", value: `${fmtNumber(metrics.coverage_deg, 0)}°`, sub: "1° bins", color: colors.good },
    { label: "Range", value: `${fmtNumber(metrics.min_distance_m, 2)}-${fmtNumber(metrics.max_distance_m, 2)} m`, sub: "valid points", color: colors.blue },
  ]);
  updateLidarExport(result, source);
  renderActiveLidarMap();
}

function setScanResult(kind, message) {
  const target = $("lidarScanResult");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
}

function lidarStateLabel(state) {
  return {
    detected: "인식됨",
    in_use: "점유 중",
    scan_live: "수신 중",
    waiting_for_scan: "스캔 대기",
    waiting_for_ros2: "ROS2 대기",
    not_found: "미인식",
  }[state] || state || "--";
}

function holderLabel(holders) {
  return holders.map((item) => `${item.pid} ${item.cmd || ""}`.trim()).join(", ");
}

function shortPath(path, maxLength = 34) {
  if (!path) return "--";
  const value = String(path);
  const byIdPrefix = "/dev/serial/by-id/";
  const label = value.startsWith(byIdPrefix) ? `by-id/${value.slice(byIdPrefix.length)}` : value;
  if (label.length <= maxLength) return label;
  const keep = Math.max(8, Math.floor((maxLength - 3) / 2));
  return `${label.slice(0, keep)}...${label.slice(-keep)}`;
}

function driverLabel(driver) {
  const value = String(driver || "").toLowerCase();
  if (value.includes("cp210")) return "CP210x USB-UART 연결";
  if (value.includes("cdc") || value.includes("ttyacm")) return "USB CDC ACM 연결";
  if (value.includes("ttyusb")) return "USB-UART 직렬 연결";
  return "직렬 드라이버 확인 전";
}

function lidarLastTestLabel(result) {
  if (!result || (!result.ok && !result.message)) return "아직 확인 전";
  if (!result.ok) return cleanLidarError(result, "응답 없음");
  return result.state === "ros2_owned" ? "ROS2 driver 소유 중" : "장치 확인됨 · subscribe-only";
}

function policyLabel(policy) {
  return policy || "Dashboard는 LiDAR serial port를 열지 않고 ROS2 /scan만 구독합니다.";
}

function lidarTestMessage(result) {
  if (!result) return "정보 없음";
  if (!result.ok) return cleanLidarError(result, "LiDAR 상태를 확인하지 못했습니다.");
  return result.message || "LiDAR 장치 상태를 확인했습니다. Dashboard는 serial port를 열지 않습니다.";
}

function cleanLidarError(result, fallback) {
  const stateName = result?.state || result?.status?.state;
  const messages = {
    not_found: "LiDAR 장치를 찾지 못했습니다. USB 연결과 전원을 확인하세요.",
    in_use: "다른 프로세스가 LiDAR 포트를 사용 중이라 테스트를 건너뛰었습니다.",
    busy: "이미 다른 LiDAR 테스트가 실행 중입니다.",
    no_protocol_response: "직렬 장치는 보이지만 RPLIDAR 진단 응답이 없습니다.",
    no_scan_data: "스캔 데이터가 들어오지 않았습니다. 모터 전원과 baudrate를 확인하세요.",
    no_scan_points: "스캔 명령 후 유효한 거리 점을 받지 못했습니다.",
    disabled: "Dashboard serial scan은 비활성화되어 있습니다. ROS2 /scan을 사용하세요.",
  };
  return messages[stateName] || result?.message || fallback || "확인 실패";
}

function hostIpLabel(network) {
  if (network.primary_ipv4) {
    return [network.primary_interface, network.primary_ipv4].filter(Boolean).join(" ");
  }
  for (const iface of network.interfaces || []) {
    if (iface.name !== "lo" && iface.ipv4?.length) return `${iface.name} ${iface.ipv4[0]}`;
  }
  return "--";
}

function setProgress(id, value) {
  setProgressFill($(`${id}`)?.querySelector("i"), value);
}

function setProgressFill(fill, value) {
  if (!fill) return;
  const pct = Math.max(0, Math.min(100, Number(value || 0)));
  fill.style.transform = `scaleX(${pct / 100})`;
  fill.setAttribute("aria-valuenow", pct.toFixed(1));
}

function renderServices(services) {
  renderStatusRows("overviewServices", serviceRows(services));
}

function serviceRows(services) {
  return Object.entries(services).map(([key, item]) => ({
    title: serviceLabel(key),
    sub: [item.host, item.port ? `:${item.port}` : "", item.state].filter(Boolean).join(" "),
    ok: Boolean(item.ok),
    level: item.ok ? "ok" : "warn",
    value: item.ok ? "OK" : "대기",
  }));
}

function renderSensors(sensors) {
  renderStatusRows("sensorList", sensorRows(sensors));
}

function sensorRows(sensors) {
  return Object.entries(sensors).map(([key, item]) => ({
    title: sensorLabel(key),
    sub: [
      item.topic,
      item.frame_id ? `frame ${item.frame_id}` : "",
      item.link ? `link ${item.link}` : "",
      item.owner ? `owner ${item.owner}` : "",
      item.dashboard_access ? item.dashboard_access : "",
      item.state,
    ].filter(Boolean).join(" · "),
    ok: Boolean(item.ok || (key === "lidar" && lidarBridgeLive())),
    level: sensorLevel(key, item),
    value: sensorValue(key, item),
  }));
}

function sensorLevel(key, item) {
  if (key === "lidar" && lidarBridgeLive()) return "ok";
  if (item.ok) return "ok";
  if (item.severity === "waiting" || item.state === "waiting_for_ros2") return "warn";
  if (key === "lidar" && item.dashboard_access === "subscribe_only") return "warn";
  return "bad";
}

function sensorValue(key, item) {
  if (key === "lidar" && lidarBridgeLive()) return `${lidarSourceLabel(item.source || state.lidarLive.source)} live`;
  if (item.rate_hz) return `${Number(item.rate_hz).toFixed(1)} Hz`;
  if (key === "lidar" && item.dashboard_access === "subscribe_only") return "ROS2 대기";
  return "연결 전";
}

function lidarBridgeLive() {
  return Boolean(state.lidarLive.scanTopicAlive || state.telemetry?.connection?.lidar_bridge?.scan_topic_alive);
}

function renderStatusRows(targetId, rows) {
  const target = $(targetId);
  if (!target) return;
  const renderKey = JSON.stringify(rows.map((row) => [row.title, row.sub, row.value, row.ok, row.level]));
  if (target.dataset.renderKey === renderKey) return;
  target.dataset.renderKey = renderKey;
  target.innerHTML = "";
  for (const row of rows) {
    const level = row.ok ? "ok" : row.level || "bad";
    const div = document.createElement("div");
    div.className = "status-item";
    div.innerHTML = `
      <i class="status-dot ${escapeHtml(level)}"></i>
      <span><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.sub || "")}</small></span>
      <b>${escapeHtml(row.value || "")}</b>
    `;
    target.append(div);
  }
}

function syncItems(targetId, rows, keyFn, createFn, updateFn) {
  const target = $(targetId);
  if (!target) return;
  const existing = new Map([...target.children].map((child) => [child.dataset.key, child]));
  const seen = new Set();
  for (const row of rows) {
    const key = String(keyFn(row));
    let el = existing.get(key);
    if (!el) {
      el = createFn();
      el.dataset.key = key;
    }
    updateFn(el, row);
    target.append(el);
    seen.add(key);
  }
  for (const [key, el] of existing) {
    if (!seen.has(key)) el.remove();
  }
}

function renderCores(cores) {
  syncItems("coreList", cores, coreKey, createCoreRow, updateCoreRow);
}

function createCoreRow() {
  const div = document.createElement("div");
  div.className = "core";
  div.innerHTML = `
    <span data-role="name"></span>
    <div class="progress"><i></i></div>
    <strong data-role="value"></strong>
  `;
  return div;
}

function updateCoreRow(el, core) {
  el.querySelector('[data-role="name"]').textContent = cpuLabel(core);
  el.querySelector('[data-role="value"]').textContent = fmtPercent(core.percent);
  setProgressFill(el.querySelector(".progress i"), core.percent);
}

function renderCoreCharts(cores) {
  syncItems("cpuSeparateView", cores, coreKey, createCoreChart, updateCoreChart);
}

function createCoreChart() {
  const article = document.createElement("article");
  article.className = "core-chart";
  article.innerHTML = `
    <canvas class="resource-chart" width="520" height="150"></canvas>
    <div class="core-chart-head">
      <span data-role="name"></span>
      <b data-role="value"></b>
      <small>Y 0-100%<br />X 최근 90초</small>
    </div>
  `;
  return article;
}

function updateCoreChart(el, core) {
  el.querySelector('[data-role="name"]').textContent = cpuLabel(core);
  el.querySelector('[data-role="value"]').textContent = fmtPercent(core.percent);
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
  const primary = disks[0];
  text("diskSummary", primary ? `${fmtBytes(primary.used)} / ${fmtBytes(primary.total)} · ${fmtPercent(primary.percent)}` : "--");
  syncItems("diskList", disks, (disk) => disk.mount, createDiskRow, updateDiskRow);
}

function createDiskRow() {
  const div = document.createElement("div");
  div.className = "disk-row";
  div.innerHTML = `
    <div><strong data-role="mount"></strong><small data-role="usage"></small></div>
    <div class="progress"><i></i></div>
    <b data-role="percent"></b>
  `;
  return div;
}

function updateDiskRow(el, disk) {
  el.querySelector('[data-role="mount"]').textContent = disk.mount;
  el.querySelector('[data-role="usage"]').textContent = `${fmtBytes(disk.used)} used · ${fmtBytes(disk.free)} free · ${fmtBytes(disk.total)} total`;
  el.querySelector('[data-role="percent"]').textContent = fmtPercent(disk.percent);
  setProgressFill(el.querySelector(".progress i"), disk.percent);
}

function renderInterfaces(interfaces, primaryInterface) {
  const target = $("interfaceList");
  if (!target) return;
  const rows = usefulInterfaces(interfaces, primaryInterface);
  if (!rows.length) {
    target.innerHTML = `<div class="empty-state">활성 네트워크 인터페이스를 찾지 못했습니다.</div>`;
    return;
  }
  if (target.querySelector(".empty-state")) target.innerHTML = "";
  syncItems("interfaceList", rows, (iface) => iface.name, createInterfaceCard, (el, iface) => updateInterfaceCard(el, iface, primaryInterface));
}

function usefulInterfaces(interfaces, primaryInterface) {
  return interfaces
    .filter((iface) => iface.name !== "lo")
    .filter((iface) => iface.name === primaryInterface || (iface.state === "up" && ((iface.ipv4 || []).length || iface.speed_mbps || iface.rx_bps || iface.tx_bps)))
    .sort((a, b) => {
      if (a.name === primaryInterface) return -1;
      if (b.name === primaryInterface) return 1;
      if (a.state === "up" && b.state !== "up") return -1;
      if (b.state === "up" && a.state !== "up") return 1;
      return a.name.localeCompare(b.name);
    })
    .slice(0, 6);
}

function createInterfaceCard() {
  const div = document.createElement("article");
  div.className = "interface-card";
  div.innerHTML = `
    <div class="card-title">
      <strong data-role="name"></strong>
      <span data-role="state" class="tag"></span>
    </div>
    <div class="interface-metrics">
      <div><span>IPv4</span><b data-role="ip"></b></div>
      <div><span>Link</span><b data-role="link"></b></div>
      <div><span>RX / TX</span><b data-role="traffic"></b></div>
    </div>
  `;
  return div;
}

function updateInterfaceCard(el, iface, primaryInterface) {
  el.querySelector('[data-role="name"]').textContent = iface.name === primaryInterface ? `${iface.name} · primary` : iface.name;
  const stateEl = el.querySelector('[data-role="state"]');
  stateEl.className = `tag ${iface.state === "up" ? "ok" : ""}`;
  stateEl.textContent = iface.state || "unknown";
  el.querySelector('[data-role="ip"]').textContent = (iface.ipv4 || []).join(", ") || "--";
  el.querySelector('[data-role="link"]').textContent = iface.speed_mbps ? `${iface.speed_mbps} Mbps` : "--";
  el.querySelector('[data-role="traffic"]').textContent = `${fmtBps(iface.rx_bps)} / ${fmtBps(iface.tx_bps)}`;
}

function renderContainers(docker) {
  const target = $("containerList");
  if (!target) return;
  if (!docker.available) {
    target.innerHTML = `<div class="empty-state">${escapeHtml(docker.error || "Docker 정보를 읽을 수 없습니다.")}</div>`;
    state.selectedContainerId = null;
    renderContainerDetailError(docker.error || "Docker 정보를 읽을 수 없습니다.");
    return;
  }
  const containers = docker.containers || [];
  if (!containers.length) {
    target.innerHTML = `<div class="empty-state">표시할 컨테이너가 없습니다.</div>`;
    state.selectedContainerId = null;
    renderContainerDetailError("컨테이너가 없습니다.");
    return;
  }
  if (target.querySelector(".empty-state")) target.innerHTML = "";
  const ids = containers.map((container) => container.id || container.name).filter(Boolean);
  if (state.selectedContainerId && !ids.includes(state.selectedContainerId)) {
    state.selectedContainerId = null;
    state.containerDetail = null;
    state.containerLogs = null;
  }
  if (!state.selectedContainerId && currentRoute() === "/docker") state.selectedContainerId = ids[0] || null;
  syncItems("containerList", containers, (container) => container.id || container.name, createContainerRow, updateContainerRow);
}

function createContainerRow() {
  const row = document.createElement("div");
  row.className = "container-row";
  row.setAttribute("role", "button");
  row.setAttribute("tabindex", "0");
  row.addEventListener("click", () => selectContainer(row.dataset.key));
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectContainer(row.dataset.key);
    }
  });
  row.innerHTML = `
    <div>
      <strong data-role="name"></strong>
      <small data-role="image"></small>
    </div>
    <span data-role="state" class="tag"></span>
    <b data-role="cpu"></b>
    <b data-role="memory"></b>
    <small data-role="status"></small>
  `;
  return row;
}

function updateContainerRow(row, container) {
  const key = container.id || container.name;
  row.classList.toggle("active", Boolean(key && key === state.selectedContainerId));
  row.setAttribute("aria-pressed", key && key === state.selectedContainerId ? "true" : "false");
  row.querySelector('[data-role="name"]').textContent = container.name || container.id;
  row.querySelector('[data-role="image"]').textContent = container.image || "";
  const stateEl = row.querySelector('[data-role="state"]');
  stateEl.className = `tag ${container.state === "running" ? "ok" : ""}`;
  stateEl.textContent = container.state || "--";
  row.querySelector('[data-role="cpu"]').textContent = fmtPercent(container.cpu_percent);
  row.querySelector('[data-role="memory"]').textContent = fmtBytes(container.memory_usage);
  row.querySelector('[data-role="status"]').textContent = container.status || "";
}

function selectContainer(containerId) {
  if (!containerId || containerId === state.selectedContainerId) return;
  state.selectedContainerId = containerId;
  state.containerDetail = null;
  state.containerLogs = null;
  state.containerLogsUpdatedAt = null;
  document.querySelectorAll(".container-row").forEach((row) => {
    row.classList.toggle("active", row.dataset.key === containerId);
    row.setAttribute("aria-pressed", row.dataset.key === containerId ? "true" : "false");
  });
  renderContainerDetailLoading(containerId);
  maybeRefreshContainerDetail(true);
}

function maybeRefreshContainerDetail(force = false) {
  if (currentRoute() !== "/docker" || !state.selectedContainerId) return;
  const now = Date.now();
  if (!force && state.containerLogsUpdatedAt && now - state.containerLogsUpdatedAt < 2500) return;
  if (state.containerRequestInFlight || state.containerLogInFlight) return;
  loadSelectedContainer(force).catch((err) => {
    debugLog("docker", "container detail failed", err);
    renderContainerDetailError(err.message);
  });
}

async function loadSelectedContainer(force = false) {
  const id = state.selectedContainerId;
  if (!id) return;
  state.containerRequestInFlight = true;
  state.containerLogInFlight = true;
  if (force || !state.containerDetail) renderContainerDetailLoading(id);
  try {
    const encoded = encodeURIComponent(id);
    const [detail, logs] = await Promise.all([
      getJson(`/api/docker/container/${encoded}`),
      getJson(`/api/docker/container/${encoded}/logs?tail=180`),
    ]);
    if (id !== state.selectedContainerId) return;
    state.containerDetail = detail;
    state.containerLogs = logs;
    state.containerLogsUpdatedAt = Date.now();
    renderContainerDetail(detail, logs);
  } finally {
    state.containerRequestInFlight = false;
    state.containerLogInFlight = false;
  }
}

function renderContainerDetailLoading(containerId) {
  text("containerDetailTitle", containerId ? `컨테이너 상세 · ${containerId}` : "컨테이너 상세");
  const detail = $("containerDetail");
  const logs = $("containerLogs");
  if (detail) detail.innerHTML = `<div class="empty-state">컨테이너 정보를 불러오는 중입니다.</div>`;
  if (logs) logs.textContent = "로그를 불러오는 중입니다.";
}

function renderContainerDetailError(message) {
  text("containerDetailTitle", "컨테이너 상세");
  const detail = $("containerDetail");
  const logs = $("containerLogs");
  if (detail) detail.innerHTML = `<div class="empty-state">${escapeHtml(message || "컨테이너를 선택하세요.")}</div>`;
  if (logs) logs.textContent = "로그 대기 중";
}

function renderContainerDetail(detailPayload, logsPayload) {
  const detail = $("containerDetail");
  const logs = $("containerLogs");
  if (!detailPayload?.ok) {
    renderContainerDetailError(detailPayload?.error || "컨테이너 정보를 읽을 수 없습니다.");
    return;
  }
  text("containerDetailTitle", `컨테이너 상세 · ${detailPayload.name || detailPayload.id}`);
  const stats = detailPayload.stats || {};
  const stateInfo = detailPayload.state || {};
  const restartPolicy = detailPayload.restart_policy || {};
  if (detail) {
    detail.innerHTML = `
      <div class="container-detail-grid">
        ${containerDetailCell("State", containerStateLabel(stateInfo), stateInfo.StartedAt ? `since ${formatShortDate(stateInfo.StartedAt)}` : "")}
        ${containerDetailCell("Image", detailPayload.image || "--", detailPayload.full_id || detailPayload.id || "")}
        ${containerDetailCell("CPU", fmtPercent(stats.cpu_percent), "one-shot stat")}
        ${containerDetailCell("Memory", `${fmtBytes(stats.memory_usage)} / ${fmtBytes(stats.memory_limit)}`, fmtPercent(stats.memory_percent))}
        ${containerDetailCell("Network", `${fmtBytes(stats.rx_bytes)} RX / ${fmtBytes(stats.tx_bytes)} TX`, detailPayload.network_mode || "")}
        ${containerDetailCell("Restart", restartPolicyLabel(restartPolicy), "policy")}
      </div>
    `;
  }
  if (logs) {
    if (!logsPayload?.ok) {
      logs.textContent = `로그 읽기 실패: ${logsPayload?.error || "unknown"}`;
    } else if ((logsPayload.lines || []).length) {
      logs.textContent = logsPayload.lines.join("\n");
    } else {
      logs.textContent = "로그가 없습니다.";
    }
  }
}

function containerDetailCell(label, value, sub = "") {
  return `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(sub || "")}</small>
    </article>
  `;
}

function containerStateLabel(stateInfo) {
  if (!stateInfo) return "--";
  if (stateInfo.Running) return "running";
  return stateInfo.Status || (stateInfo.ExitCode !== undefined ? `exited ${stateInfo.ExitCode}` : "--");
}

function restartPolicyLabel(policy) {
  const name = policy?.Name || "--";
  const retry = policy?.MaximumRetryCount;
  return retry ? `${name} (${retry})` : name;
}

function formatShortDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR");
}

function initFanControls() {
  $("fanAutoToggle")?.addEventListener("change", async (event) => {
    await postFanJson("/api/fan/mode", { auto: Boolean(event.target.checked) });
  });
  $("fanPwmSlider")?.addEventListener("input", (event) => {
    text("fanSliderValue", event.target.value);
  });
  $("fanPwmSlider")?.addEventListener("change", async (event) => {
    await postFanJson("/api/fan/pwm", { pwm: Number(event.target.value) });
  });
  document.querySelectorAll("[data-fan-preset]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postFanJson("/api/fan/preset", { preset: button.dataset.fanPreset });
    });
  });
}

function initDockerControls() {
  $("containerRefreshButton")?.addEventListener("click", () => maybeRefreshContainerDetail(true));
}

async function postFanJson(path, payload) {
  setFanStatus("testing", "요청을 적용하는 중입니다.");
  debugLog("fan", `POST ${path}`, payload || {});
  try {
    const res = await fetch(path, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const result = await res.json();
    debugLog("fan", `${path} result`, result);
    setFanStatus(result.ok ? "ok" : "bad", fanResultMessage(result));
    await tick();
  } catch (err) {
    debugLog("fan", `${path} failed`, err);
    setFanStatus("bad", `Fan API 실패: ${err.message}`);
  }
}

function fanResultMessage(result) {
  if (result.ok && result.action === "auto") return "자동 제어로 전환했습니다.";
  if (result.ok && result.action === "manual") return "수동 제어로 전환했습니다.";
  if (result.ok) return result.message || result.state || "적용 완료";
  const writeError = result.write?.error || result.enable?.error || result.systemctl?.error;
  const stateText = result.message || result.state || result.systemctl?.state || "적용 실패";
  return writeError ? `${stateText}: ${writeError}` : stateText;
}

function setFanStatus(kind, message) {
  const target = $("fanControlStatus");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
}

function renderFan(fan) {
  text("fanTempValue", fmtTemp(fan.temperature_c));
  text("fanTempText", fanTemperatureLabel(fan));
  text("fanPwmValue", fan.pwm === null || fan.pwm === undefined ? "--" : `${fan.pwm}`);
  text("fanModeValue", fan.mode === "auto" ? "자동" : fan.mode === "manual" ? "수동" : "--");
  text("fanModeText", fan.mode === "auto" ? "nvfancontrol 사용" : "PWM 직접 제어");
  text("fanTempChartValue", fmtTemp(fan.temperature_c));

  const auto = $("fanAutoToggle");
  if (auto) {
    auto.disabled = !fan.available;
    auto.checked = fan.mode === "auto";
  }
  const manualDisabled = !fan.available || fan.mode === "auto";
  const slider = $("fanPwmSlider");
  if (slider) {
    slider.disabled = manualDisabled;
    if (fan.pwm !== null && fan.pwm !== undefined) slider.value = fan.pwm;
  }
  text("fanSliderValue", fan.pwm === null || fan.pwm === undefined ? "0" : String(fan.pwm));
  document.querySelectorAll("[data-fan-preset]").forEach((button) => {
    button.disabled = manualDisabled;
  });

  if ($("fanControlStatus") && !$("fanControlStatus").classList.contains("testing")) {
    setFanStatus(fan.available ? "ok" : "bad", fan.available ? fanHoldLabel(fan) : "팬 제어 장치를 찾지 못했습니다.");
  }
}

function fanTemperatureLabel(fan) {
  const level = fan.safety?.level;
  if (level === "critical") return "위험 온도";
  if (level === "hot") return "높은 온도";
  if (level === "warm") return "주의 온도";
  if (level === "unknown") return "온도 확인 불가";
  return "정상 범위";
}

function fanHoldLabel(fan) {
  if (fan.mode === "auto") return "자동 제어 중";
  if (fan.desired?.mode === "manual" && fan.desired?.pwm !== undefined) {
    return `수동 PWM ${fan.desired.pwm} 유지 중`;
  }
  return `수동 PWM ${fan.pwm ?? "--"}`;
}

function renderChartReadouts(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const fan = data.fan || {};
  const colors = chartColors();
  const dockerMemPercent = docker.summary?.memory_limit ? (docker.summary.memory_usage / docker.summary.memory_limit) * 100 : null;
  renderReadout("overviewSystemReadout", [
    { label: "CPU", value: fmtPercent(sys.cpu?.total_percent), sub: "전체 사용률", color: colors.good },
    { label: "Memory", value: fmtPercent(memoryPercent(sys.memory)), sub: `${fmtBytes(sys.memory?.used)} / ${fmtBytes(memoryTotal(sys.memory))}`, color: colors.cyan },
  ]);
  renderReadout("overviewNetworkReadout", [
    { label: "RX", value: fmtBps(net.total_rx_bps), sub: "수신", color: colors.blue },
    { label: "TX", value: fmtBps(net.total_tx_bps), sub: "송신", color: colors.rose },
  ]);
  renderReadout("cpuReadout", [
    { label: "Total", value: fmtPercent(sys.cpu?.total_percent), sub: `${(sys.cpu?.cores || []).length} cores · 0-100%`, color: colors.good },
    ...((sys.cpu?.cores || []).slice(0, 6).map((core, index) => ({
      label: cpuLabel(core),
      value: fmtPercent(core.percent),
      sub: "개별 사용률",
      color: CORE_COLORS[index % CORE_COLORS.length],
    }))),
  ]);
  renderReadout("memoryReadout", [
    { label: "Usage", value: fmtPercent(memoryPercent(sys.memory)), sub: memoryLabel(sys.memory), color: colors.cyan },
    { label: "Used", value: fmtBytes(sys.memory?.used), sub: `of ${fmtBytes(memoryTotal(sys.memory))}`, color: colors.blue },
    { label: "Available", value: fmtBytes(sys.memory?.available), sub: "실제 MemAvailable", color: colors.good },
  ]);
  renderReadout("swapReadout", [
    { label: "Usage", value: fmtPercent(sys.swap?.percent), sub: `${fmtBytes(sys.swap?.used)} / ${fmtBytes(sys.swap?.total)}`, color: colors.rose },
    { label: "Free", value: fmtBytes(sys.swap?.free), sub: "swap available", color: colors.good },
  ]);
  renderReadout("systemNetworkReadout", [
    { label: "RX", value: fmtBps(net.total_rx_bps), sub: "수신", color: colors.blue },
    { label: "TX", value: fmtBps(net.total_tx_bps), sub: "송신", color: colors.rose },
  ]);
  renderReadout("powerReadout", [
    { label: "Power", value: fmtPower(sys.power?.total_w), sub: sys.power?.source || "ina3221", color: colors.amber },
    { label: "Rails", value: String((sys.power?.rails || []).length || 0), sub: sys.power?.available ? "available" : "unavailable", color: colors.cyan },
  ]);
  renderReadout("networkReadout", [
    { label: "RX", value: fmtBps(net.total_rx_bps), sub: "수신 속도", color: colors.blue },
    { label: "TX", value: fmtBps(net.total_tx_bps), sub: "송신 속도", color: colors.rose },
    { label: "Host", value: net.primary_ipv4 || "--", sub: net.primary_interface || "interface", color: colors.cyan },
  ]);
  renderReadout("dockerCpuReadout", [
    { label: "CPU", value: fmtPercent(docker.summary?.cpu_percent), sub: "컨테이너 합산", color: colors.good },
  ]);
  renderReadout("dockerMemReadout", [
    { label: "Memory", value: fmtPercent(dockerMemPercent), sub: `${fmtBytes(docker.summary?.memory_usage)} / ${fmtBytes(docker.summary?.memory_limit)}`, color: colors.cyan },
  ]);
  renderReadout("fanTempReadout", [
    { label: "Temp", value: fmtTemp(fan.temperature_c), sub: fanTemperatureLabel(fan), color: colors.amber },
    { label: "PWM", value: fan.pwm === null || fan.pwm === undefined ? "--" : `${fan.pwm}`, sub: "수동 출력", color: colors.rose },
  ]);
}

function renderReadout(targetId, rows) {
  const target = $(targetId);
  if (!target) return;
  const renderKey = JSON.stringify(rows.map((row) => [row.label, row.value, row.sub, row.color]));
  if (target.dataset.renderKey === renderKey) return;
  target.dataset.renderKey = renderKey;
  target.innerHTML = "";
  for (const row of rows) {
    const div = document.createElement("div");
    div.className = "readout-row";
    div.innerHTML = `
      <i class="swatch"></i>
      <span>${escapeHtml(row.label)}</span>
      <b>${escapeHtml(row.value)}</b>
      <small>${escapeHtml(row.sub || "")}</small>
    `;
    div.querySelector(".swatch").style.background = row.color;
    div.querySelector(".swatch").style.color = row.color;
    target.append(div);
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

function renderDocsIndex(docs) {
  const target = $("docsIndex");
  if (!target) return;
  if (!docs.length) {
    target.innerHTML = `<div class="empty-state">표시할 문서가 없습니다.</div>`;
    return;
  }
  target.innerHTML = "";
  const groups = new Map();
  for (const doc of docs) {
    const key = doc.category || "Docs";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(doc);
  }
  for (const [category, rows] of groups) {
    const section = document.createElement("section");
    section.className = "doc-group";
    const title = document.createElement("h3");
    title.textContent = category;
    section.append(title);
    for (const doc of rows) {
      const link = document.createElement("a");
      link.className = "doc-link";
      link.href = doc.url || `/docs/${doc.slug}`;
      link.dataset.docLink = "true";
      link.dataset.docSlug = doc.slug;
      link.innerHTML = `
        <strong>${escapeHtml(doc.title)}</strong>
        <small>${escapeHtml(doc.summary || doc.source || "")}</small>
      `;
      section.append(link);
    }
    target.append(section);
  }
  updateDocsIndexActive();
}

function renderDocsRoute() {
  if (currentRoute() !== "/docs" || !state.docs.length) return;
  const slug = docSlugFromRoute() || state.docs[0].slug;
  if (location.pathname === "/docs" && slug) history.replaceState({}, "", `/docs/${slug}`);
  updateDocsIndexActive(slug);
  if (state.activeDoc?.slug === slug && state.activeDoc?.markdown !== undefined) {
    renderDocPayload(state.activeDoc);
    return;
  }
  renderDocLoading(slug);
  getJson(`/api/docs/${encodeURIComponent(slug)}`)
    .then((payload) => {
      if (!payload.ok) throw new Error(payload.error || "document_not_found");
      state.activeDoc = payload;
      renderDocPayload(payload);
      updateDocsIndexActive(payload.slug);
    })
    .catch((err) => {
      renderDocError(err.message);
    });
}

function docSlugFromRoute() {
  if (!location.pathname.startsWith("/docs/")) return "";
  return decodeURIComponent(location.pathname.replace(/^\/docs\/?/, "").split("/")[0] || "");
}

function renderDocLoading(slug) {
  const doc = state.docs.find((item) => item.slug === slug);
  text("docCategory", doc?.category || "Docs");
  text("docTitle", doc?.title || "문서 로딩 중");
  text("docSummary", doc?.summary || "Markdown 문서를 불러오고 있습니다.");
  const body = $("docMarkdown");
  if (body) body.innerHTML = `<div class="empty-state">문서를 불러오는 중입니다.</div>`;
}

function renderDocPayload(doc) {
  text("docCategory", doc.category || "Docs");
  text("docTitle", doc.title || doc.slug);
  text("docSummary", doc.summary || doc.source || "");
  const permalink = $("docPermalink");
  if (permalink) {
    permalink.href = doc.url || `/docs/${doc.slug}`;
    permalink.textContent = doc.source || "link";
  }
  const body = $("docMarkdown");
  if (body) body.innerHTML = markdownToHtml(doc.markdown || "");
}

function renderDocError(message) {
  text("docCategory", "Docs");
  text("docTitle", "문서를 열 수 없습니다");
  text("docSummary", message || "알 수 없는 오류");
  const body = $("docMarkdown");
  if (body) body.innerHTML = `<div class="empty-state">${escapeHtml(message || "문서 로딩 실패")}</div>`;
}

function updateDocsIndexActive(slug = docSlugFromRoute()) {
  document.querySelectorAll("[data-doc-link]").forEach((link) => {
    link.classList.toggle("active", link.dataset.docSlug === slug);
  });
}

function markdownToHtml(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const code = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      html.push(`<pre><code class="language-${escapeHtml(language)}">${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const label = heading[2].trim();
      const id = slugify(label);
      html.push(`<h${level} id="${id}">${formatInline(label)}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*\|.+\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
      const header = splitTableRow(line);
      const rows = [];
      i += 2;
      while (i < lines.length && /^\s*\|.+\|\s*$/.test(lines[i])) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      html.push(renderMarkdownTable(header, rows));
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      html.push(`<ul>${items.map((item) => `<li>${formatInline(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      html.push(`<ol>${items.map((item) => `<li>${formatInline(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      html.push(`<blockquote>${formatInline(quote.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*-{3,}\s*$/.test(line)) {
      html.push("<hr />");
      i += 1;
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !isMarkdownBlockStart(lines[i])) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    html.push(`<p>${formatInline(paragraph.join(" "))}</p>`);
  }
  return html.join("\n");
}

function isMarkdownBlockStart(line) {
  return line.startsWith("```")
    || /^(#{1,4})\s+/.test(line)
    || /^\s*[-*]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^\s*>\s?/.test(line)
    || /^\s*-{3,}\s*$/.test(line);
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderMarkdownTable(header, rows) {
  const head = header.map((cell) => `<th>${formatInline(cell)}</th>`).join("");
  const body = rows.map((row) => `<tr>${row.map((cell) => `<td>${formatInline(cell)}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function formatInline(value) {
  let textValue = escapeHtml(value);
  textValue = textValue.replace(/`([^`]+)`/g, "<code>$1</code>");
  textValue = textValue.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const safe = safeDocHref(href);
    const external = /^https?:\/\//.test(safe) ? ' target="_blank" rel="noreferrer"' : "";
    return `<a href="${escapeHtml(safe)}"${external}>${label}</a>`;
  });
  textValue = textValue.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  textValue = textValue.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return textValue;
}

function safeDocHref(href) {
  const raw = String(href || "").trim();
  if (/^https?:\/\//.test(raw) || raw.startsWith("#") || raw.startsWith("/")) return raw;
  const normalized = raw.replace(/^\.\//, "").replace(/^docs\//, "");
  const doc = state.docs.find((item) => item.source === raw || item.source === `docs/${normalized}` || item.source?.endsWith(`/${normalized}`));
  return doc?.url || "#";
}

function slugify(value) {
  return escapeHtml(String(value || "").trim().toLowerCase().replace(/[^a-z0-9가-힣]+/g, "-").replace(/^-|-$/g, ""));
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

function requestChartAnimation() {
  if (state.animationFrame) return;
  state.animationFrame = requestAnimationFrame(animateCharts);
}

function animateCharts(timestamp) {
  state.animationFrame = null;
  const historyActive = stepVisualHistory(timestamp);
  const lidarActive = stepLidarMapRange();
  drawCharts();
  if (historyActive || lidarActive) state.animationFrame = requestAnimationFrame(animateCharts);
}

function stepVisualHistory(timestamp) {
  if (state.chartShift <= 0) return false;
  const elapsed = Math.max(0, timestamp - state.chartShiftStartedAt);
  state.chartShift = Math.max(0, 1 - elapsed / 720);
  return state.chartShift > 0;
}

function stepLidarMapRange() {
  const target = clampLidarRange(state.lidarMap.range);
  if (!Number.isFinite(state.lidarMap.displayRange)) {
    state.lidarMap.displayRange = target;
    return false;
  }
  const diff = target - state.lidarMap.displayRange;
  if (Math.abs(diff) < 0.025) {
    state.lidarMap.displayRange = target;
    return false;
  }
  state.lidarMap.displayRange += diff * 0.22;
  return true;
}

function drawCharts() {
  const colors = chartColors();
  const powerMax = Math.max(10, ...state.history.power.filter((v) => v !== null));
  const groups = {
    "/": [
      ["chartOverviewSystem", [
        { values: state.visualHistory.cpu, color: colors.good, max: 100 },
        { values: state.visualHistory.memory, color: colors.cyan, max: 100 },
      ]],
      ["chartOverviewNetwork", [
        { values: normalizeBps(state.visualHistory.rx, "rx"), color: colors.blue, max: 100 },
        { values: normalizeBps(state.visualHistory.tx, "tx"), color: colors.rose, max: 100 },
      ]],
    ],
    "/connection": [],
    "/system": [
      ...(state.cpuMode === "combined" ? [["chartCpu", [{ values: state.visualHistory.cpu, color: colors.good, max: 100 }]]] : []),
      ["chartMemory", [{ values: state.visualHistory.memory, color: colors.cyan, max: 100 }]],
      ["chartSwap", [{ values: state.visualHistory.swap, color: colors.rose, max: 100 }]],
      ["chartSystemNetwork", [
        { values: normalizeBps(state.visualHistory.rx, "rx"), color: colors.blue, max: 100 },
        { values: normalizeBps(state.visualHistory.tx, "tx"), color: colors.rose, max: 100 },
      ]],
      ["chartPower", [{ values: state.visualHistory.power, color: colors.amber, max: powerMax }]],
    ],
    "/network": [
      ["chartNetwork", [
        { values: normalizeBps(state.visualHistory.rx, "rx"), color: colors.blue, max: 100 },
        { values: normalizeBps(state.visualHistory.tx, "tx"), color: colors.rose, max: 100 },
      ]],
    ],
    "/docker": [
      ["chartDockerCpu", [{ values: state.visualHistory.dockerCpu, color: colors.good, max: 100 }]],
      ["chartDockerMem", [{ values: state.visualHistory.dockerMemory, color: colors.cyan, max: 100 }]],
    ],
    "/fan": [
      ["chartFanTemp", [{ values: state.visualHistory.fanTemp, color: colors.amber, max: Math.max(90, ...state.history.fanTemp.filter((v) => v !== null)) }]],
    ],
    "/research": [],
  };
  for (const [id, series] of groups[currentRoute()] || []) drawMultiLine(id, series);
  if (currentRoute() === "/system" && state.cpuMode === "separate") drawCoreCharts();
  if (currentRoute() === "/connection") renderActiveLidarMap();
}

function renderActiveLidarMap() {
  drawLidarMap(state.lidarScan);
}

function normalizeBps(values, key) {
  const max = Math.max(1, ...state.history[key].filter((v) => v !== null));
  return values.map((value) => (value === null ? null : (value / max) * 100));
}

function normalizeSeries(values, rawValues) {
  const max = Math.max(1, ...rawValues.filter((v) => v !== null));
  return values.map((value) => (value === null ? null : (value / max) * 100));
}

function chartColors() {
  return {
    blue: cssVar("--chart-blue", "#2f76ff"),
    cyan: cssVar("--chart-cyan", "#38d8ff"),
    rose: cssVar("--chart-rose", "#f21b58"),
    amber: cssVar("--chart-amber", "#f4bd4f"),
    good: cssVar("--chart-good", "#55e2b2"),
  };
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function drawCoreCharts() {
  const target = $("cpuSeparateView");
  if (!target) return;
  [...target.children].forEach((row, index) => {
    const canvas = row.querySelector("canvas");
    const key = row.dataset.key;
    if (!canvas || !key) return;
    drawMultiLine(canvas, [{ values: state.visualCoreHistory[key] || [], color: CORE_COLORS[index % CORE_COLORS.length], max: 100 }]);
  });
}

function drawMultiLine(target, series) {
  const canvas = typeof target === "string" ? $(target) : target;
  if (!canvas) return;
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { ctx, w, h } = prepared;
  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createLinearGradient(0, 0, 0, h);
  gradient.addColorStop(0, cssVar("--chart-bg-top", "rgba(255,255,255,0.05)"));
  gradient.addColorStop(1, cssVar("--chart-bg-bottom", "rgba(255,255,255,0.01)"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  drawGrid(ctx, w, h);
  for (const item of series) drawLine(ctx, item.values, item.color, item.max || 100, w, h);
}

function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, DPR_LIMIT);
  const width = Math.round(rect.width * dpr);
  const height = Math.round(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d", { alpha: true });
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: rect.width, h: rect.height };
}

function drawGrid(ctx, w, h) {
  ctx.save();
  ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,0.1)");
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
    const offset = Math.max(0, MAX_POINTS - values.length);
    const x = values.length <= 1 ? w : ((offset + index + state.chartShift) / (MAX_POINTS - 1)) * w;
    const safe = Math.max(0, Math.min(max, value));
    const y = h - (safe / max) * (h - 18) - 9;
    return { x, y };
  });
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.8;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.shadowColor = color;
  ctx.shadowBlur = 8;
  ctx.beginPath();
  let segment = [];
  for (const point of points) {
    if (point) {
      segment.push(point);
      continue;
    }
    strokeSegment(ctx, segment);
    segment = [];
  }
  strokeSegment(ctx, segment);
  ctx.stroke();
  ctx.restore();
}

function strokeSegment(ctx, points) {
  if (!points.length) return;
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length === 1) {
    ctx.lineTo(points[0].x + 0.01, points[0].y);
    return;
  }
  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const current = points[i];
    const midX = (prev.x + current.x) / 2;
    const midY = (prev.y + current.y) / 2;
    ctx.quadraticCurveTo(prev.x, prev.y, midX, midY);
  }
  const last = points[points.length - 1];
  ctx.lineTo(last.x, last.y);
}

function drawLidarMap(result) {
  const canvas = $("lidarScanCanvas");
  if (!canvas) return;
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { ctx, w, h } = prepared;
  const colors = chartColors();
  const points = result?.ok ? result.points || [] : [];
  const radius = Math.max(80, Math.min(w, h) * 0.39);
  const cx = w / 2;
  const cy = h / 2;
  const range = lidarDrawRange();
  const gridStep = lidarGridStep(range);

  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createRadialGradient(cx, cy, radius * 0.12, cx, cy, radius);
  gradient.addColorStop(0, cssVar("--chart-bg-top", "rgba(255,255,255,0.05)"));
  gradient.addColorStop(1, cssVar("--chart-bg-bottom", "rgba(255,255,255,0.01)"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  drawLidarGrid(ctx, cx, cy, radius, range, gridStep);

  if (!points.length) {
    drawLidarEmpty(ctx, w, h, result?.message || "스캔 대기");
    return;
  }

  ctx.save();
  ctx.fillStyle = colors.cyan;
  for (const point of points) {
    const x = cx + ((Number(point.x_m) || 0) / range) * radius;
    const y = cy - ((Number(point.y_m) || 0) / range) * radius;
    const quality = Math.max(0, Math.min(63, Number(point.quality) || 0));
    ctx.globalAlpha = Math.max(0.75, Math.min(0.95, 0.75 + quality / 315));
    ctx.beginPath();
    ctx.arc(x, y, Math.max(1.6, Math.min(2.2, 1.6 + quality / 105)), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  ctx.save();
  ctx.fillStyle = cssVar("--text", "#f7f9ff");
  ctx.font = "800 12px Inter, Arial, sans-serif";
  ctx.fillText(`${points.length} points`, 14, 22);
  ctx.fillText(`${fmtNumber(result.metrics?.coverage_deg ?? angleCoverage(points), 0)}° coverage`, 14, 40);
  ctx.textAlign = "right";
  ctx.fillText(`Grid: ${fmtNumber(gridStep, 0)}m · Range: ${fmtNumber(range, 1)}m`, w - 14, h - 16);
  ctx.restore();
}

function lidarRangeFor(result) {
  return clampLidarRange(state.lidarMap.range);
}

function lidarDrawRange() {
  return clampLidarRange(state.lidarMap.displayRange || state.lidarMap.range);
}

function lidarGridStep(range) {
  if (range <= 6) return 1;
  if (range <= 12) return 2;
  return 5;
}

function drawLidarGrid(ctx, cx, cy, radius, range, gridStep) {
  ctx.save();
  ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,0.1)");
  ctx.lineWidth = 1;
  for (let meters = gridStep; meters <= range + 0.001; meters += gridStep) {
    const r = (meters / range) * radius;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = cssVar("--dim", "#7480a3");
    ctx.font = "800 10px Inter, Arial, sans-serif";
    ctx.fillText(`${fmtNumber(meters, 0)}m`, cx + 7, cy - r + 13);
  }
  ctx.beginPath();
  ctx.moveTo(cx - radius, cy);
  ctx.lineTo(cx + radius, cy);
  ctx.moveTo(cx, cy - radius);
  ctx.lineTo(cx, cy + radius);
  ctx.stroke();
  ctx.fillStyle = cssVar("--muted", "#a9b4d2");
  ctx.font = "900 11px Inter, Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const labelOffset = 20;
  ctx.fillText("0°", cx, cy - radius - labelOffset);
  ctx.fillText("90°", cx + radius + labelOffset, cy);
  ctx.fillText("180°", cx, cy + radius + labelOffset);
  ctx.fillText("270°", cx - radius - labelOffset, cy);
  ctx.fillStyle = cssVar("--text", "#f7f9ff");
  ctx.beginPath();
  ctx.arc(cx, cy, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawLidarEmpty(ctx, w, h, message) {
  ctx.save();
  ctx.fillStyle = cssVar("--muted", "#a9b4d2");
  ctx.font = "900 14px Inter, Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(message, w / 2, h / 2);
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
window.addEventListener("resize", requestChartAnimation);
window.addEventListener("pagehide", () => {
  if (state.lidarLive.socket || state.lidarLive.enabled) {
    closeLidarLive("페이지 종료로 LiDAR WebSocket을 닫습니다.", { userStopped: false });
  }
});
document.addEventListener("click", (event) => {
  const docLink = event.target.closest("a[data-doc-link]");
  if (docLink) {
    event.preventDefault();
    history.pushState({}, "", docLink.getAttribute("href"));
    setMobileNav(false);
    renderRoute();
    return;
  }
  const link = event.target.closest("a[data-route]");
  if (!link) return;
  event.preventDefault();
  history.pushState({}, "", link.getAttribute("href"));
  setMobileNav(false);
  renderRoute();
});

initMobileNav();
initTheme();
initCpuMode();
initLidarTest();
initFanControls();
initDockerControls();
renderRoute();
loadPlan().catch(console.error);
loadDocs().catch(console.error);
tick();
setInterval(tick, POLL_INTERVAL_MS);
