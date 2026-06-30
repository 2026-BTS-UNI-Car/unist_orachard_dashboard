const ROUTES = ["/", "/connection", "/system", "/network", "/docker", "/research", "/docs"];
const HISTORY_KEYS = ["cpu", "memory", "gpu", "temp", "rx", "tx", "dockerCpu", "dockerMemory"];
const CORE_COLORS = ["#55e2b2", "#38d8ff", "#2f76ff", "#8238c9", "#f21b58", "#f4bd4f", "#7dd3fc", "#fb7185"];
const MAX_POINTS = 90;
const POLL_INTERVAL_MS = 1000;
const DPR_LIMIT = 1.5;
const CPU_MODE_KEY = "ubts-cpu-chart-mode";
const LIDAR_MAP_MODE_KEY = "ubts-lidar-map-mode";
const SCAN_PROGRESS_STAGES = [
  { at: 0, label: "포트 점유 확인" },
  { at: 0.2, label: "모터 라인 준비" },
  { at: 0.44, label: "360도 점군 수집" },
  { at: 0.78, label: "STOP 전송 및 포트 정리" },
];

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
  lidarMapMode: "2d",
  lidarScan: null,
  scanProgressFrame: null,
  scanProgressStartedAt: 0,
  scanProgressDurationMs: 3800,
  chartShift: 0,
  chartShiftStartedAt: 0,
  animationFrame: null,
};

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

async function getJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
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
  if (route === "/connection") renderActiveLidarMap();
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
  window.ubtsLidar3d?.setTheme?.();
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
  $("lidarScanButton")?.addEventListener("click", runLidarScan);
  initLidarMapMode();
}

function initLidarMapMode() {
  try {
    state.lidarMapMode = localStorage.getItem(LIDAR_MAP_MODE_KEY) || "2d";
  } catch (_) {
    state.lidarMapMode = "2d";
  }
  document.querySelectorAll("[data-lidar-map-mode]").forEach((button) => {
    button.addEventListener("click", () => setLidarMapMode(button.dataset.lidarMapMode));
  });
  setLidarMapMode(state.lidarMapMode);
}

function setLidarMapMode(mode) {
  state.lidarMapMode = mode === "3d" ? "3d" : "2d";
  try {
    localStorage.setItem(LIDAR_MAP_MODE_KEY, state.lidarMapMode);
  } catch (_) {
    // Keep the current session usable when storage is blocked.
  }
  document.querySelectorAll("[data-lidar-map-mode]").forEach((button) => {
    const active = button.dataset.lidarMapMode === state.lidarMapMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const canvas2d = $("lidarScanCanvas");
  const canvas3d = $("lidarScanCanvas3d");
  if (canvas2d) canvas2d.hidden = state.lidarMapMode !== "2d";
  if (canvas3d) canvas3d.hidden = state.lidarMapMode !== "3d";
  text(
    "lidarMapHint",
    state.lidarMapMode === "3d"
      ? "3D 지도는 마우스 드래그 또는 터치로 회전하고, 휠/핀치로 확대합니다."
      : "2D 지도는 위에서 본 360도 점군입니다.",
  );
  requestAnimationFrame(renderActiveLidarMap);
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
  try {
    state.telemetry = await getJson("/api/telemetry");
    pushHistory(state.telemetry);
    renderTelemetry(state.telemetry);
    setConnection("ok", "online");
    requestChartAnimation();
  } catch (err) {
    setConnection("bad", "offline");
    console.error(err);
  }
}

function pushHistory(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
  const cores = sys.cpu?.cores || [];
  const dockerCpu = docker.summary?.cpu_percent;
  const dockerMemory = docker.summary?.memory_limit ? (docker.summary.memory_usage / docker.summary.memory_limit) * 100 : null;
  addPoint("cpu", sys.cpu?.total_percent);
  addPoint("memory", memoryPercent(sys.memory));
  addPoint("gpu", sys.gpu_percent);
  addPoint("temp", sys.temperature_c);
  addPoint("rx", net.total_rx_bps);
  addPoint("tx", net.total_tx_bps);
  addPoint("dockerCpu", dockerCpu);
  addPoint("dockerMemory", dockerMemory);
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
  text("hostExternalIp", net.external_ip || net.external_ip_error || "--");
  text("hostInternalIp", hostIpLabel(net));

  text("systemCpuValue", fmtPercent(sys.cpu?.total_percent));
  text("systemMemoryValue", fmtPercent(memoryPercent(sys.memory)));
  text("systemGpuValue", fmtPercent(sys.gpu_percent));
  text("systemTempValue", fmtTemp(sys.temperature_c));
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

  renderServices(services);
  renderConnection(connection);
  renderSensors(data.sensors || {});
  renderCores(sys.cpu?.cores || []);
  renderCoreCharts(sys.cpu?.cores || []);
  renderTemperatures(sys.temperatures || []);
  renderDisks(sys.disks || []);
  renderInterfaces(net.interfaces || [], net.primary_interface);
  renderContainers(docker);
  renderChartReadouts(data);
}

async function runLidarTest() {
  const button = $("lidarTestButton");
  if (button) button.disabled = true;
  setTestResult("testing", "장치 점유 여부를 확인한 뒤 Info/Health 요청만 짧게 보냅니다.");
  try {
    const res = await fetch("/api/lidar/test", { method: "POST", cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    renderLidarTest(payload);
    await tick();
  } catch (err) {
    setTestResult("bad", `정보 확인 실패: ${err.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runLidarScan() {
  const button = $("lidarScanButton");
  if (button) button.disabled = true;
  startLidarScanProgress();
  setScanResult("testing", "스캔 준비 중입니다. ROS2가 포트를 사용 중이면 바로 중단합니다.");
  try {
    const res = await fetch("/api/lidar/scan", { method: "POST", cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    state.lidarScan = payload;
    renderLidarScan(payload);
    finishLidarScanProgress(Boolean(payload.ok));
    await tick();
  } catch (err) {
    setScanResult("bad", `스캔 실패: ${err.message}`);
    finishLidarScanProgress(false);
    renderActiveLidarMap();
  } finally {
    if (button) button.disabled = false;
  }
}

function renderConnection(connection) {
  const lidar = connection.lidar || {};
  const last = lidar.last_test || {};
  const lastScan = lidar.last_scan || {};
  text("connectionLidarState", lidar.present ? lidarStateLabel(lidar.state) : "미인식");
  text("connectionLidarDevice", lidar.device || "--");
  text("connectionLidarProtocol", last.protocol || "--");
  text("connectionLidarBaud", last.baud ? `${last.baud} bps` : "테스트 전");
  text("connectionLidarHealth", last.health?.status || (lidar.present ? "대기" : "--"));
  text("connectionLidarHealthText", last.health ? `error ${last.health.error_code}` : "테스트 버튼으로 확인");
  text("connectionLidarGuard", (lidar.holders || []).length ? "점유 중" : "OK");
  text("connectionLidarGuardText", (lidar.holders || []).length ? holderLabel(lidar.holders) : "테스트 시에만 포트 사용");
  text("lidarDeviceDetail", lidar.present ? shortPath(lidar.device) : "장치 미인식");
  text("lidarByIdDetail", lidar.by_id ? shortPath(lidar.by_id, 42) : "stable ID 대기");
  text("lidarDriverDetail", driverLabel(lidar.driver_hint));
  text("lidarLastTestDetail", lidarLastTestLabel(last));
  text("lidarPolicyDetail", policyLabel(lidar.conflict_policy));
  renderStatusRows("connectionServices", serviceRows(connection.services || {}));
  renderStatusRows("connectionSensors", sensorRows(connection.sensors || {}));
  if (last.ok || last.message) renderLidarTest(last);
  if (lastScan.ok || lastScan.message) {
    if (!state.lidarScan || (lastScan.tested_at || 0) >= (state.lidarScan.tested_at || 0)) {
      state.lidarScan = lastScan;
      renderLidarScan(lastScan);
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

function renderLidarScan(result) {
  if (!result) return;
  const metrics = result.metrics || {};
  const points = result.points || [];
  const ok = Boolean(result.ok && points.length);
  const message = ok
    ? `스캔 완료: ${metrics.points || points.length}개 점 · ${fmtNumber(metrics.coverage_deg, 0)}도 커버리지 · ${fmtNumber(result.duration_s, 1)}초`
    : cleanLidarError(result, "스캔 데이터를 받지 못했습니다.");
  setScanResult(ok ? "ok" : "bad", message);
  const colors = chartColors();
  renderReadout("lidarScanStats", [
    { label: "Points", value: String(metrics.points || points.length || 0), sub: `render ${metrics.rendered_points || points.length || 0}`, color: colors.cyan },
    { label: "Coverage", value: `${fmtNumber(metrics.coverage_deg, 0)}°`, sub: "1° bins", color: colors.good },
    { label: "Range", value: `${fmtNumber(metrics.min_distance_m, 2)}-${fmtNumber(metrics.max_distance_m, 2)} m`, sub: "valid points", color: colors.blue },
    { label: "Baud", value: result.baud ? `${result.baud}` : "--", sub: result.protocol || "RPLIDAR", color: colors.rose },
  ]);
  renderActiveLidarMap();
}

function setScanResult(kind, message) {
  const target = $("lidarScanResult");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
}

function startLidarScanProgress() {
  stopLidarScanProgress();
  state.scanProgressStartedAt = performance.now();
  state.scanProgressDurationMs = 3800;
  const box = $("lidarScanProgress");
  if (box) {
    box.classList.add("active");
    box.setAttribute("aria-hidden", "false");
  }
  updateLidarScanProgress(state.scanProgressStartedAt);
}

function stopLidarScanProgress() {
  if (state.scanProgressFrame) {
    cancelAnimationFrame(state.scanProgressFrame);
    state.scanProgressFrame = null;
  }
}

function updateLidarScanProgress(timestamp) {
  const elapsed = Math.max(0, timestamp - state.scanProgressStartedAt);
  const raw = Math.min(0.92, elapsed / state.scanProgressDurationMs);
  const eased = 1 - Math.pow(1 - raw, 2.2);
  const stage = [...SCAN_PROGRESS_STAGES].reverse().find((item) => raw >= item.at) || SCAN_PROGRESS_STAGES[0];
  setProgressFill($("lidarScanProgressBar"), eased * 100);
  text("lidarScanProgressText", stage.label);
  text("lidarScanProgressPercent", `${Math.round(eased * 100)}%`);
  if (raw < 0.92) {
    state.scanProgressFrame = requestAnimationFrame(updateLidarScanProgress);
  }
}

function finishLidarScanProgress(ok) {
  stopLidarScanProgress();
  const pct = ok ? 100 : 100;
  setProgressFill($("lidarScanProgressBar"), pct);
  text("lidarScanProgressText", ok ? "스캔 완료" : "스캔 중단");
  text("lidarScanProgressPercent", ok ? "100%" : "확인 필요");
}

function lidarStateLabel(state) {
  return { detected: "인식됨", in_use: "점유 중", not_found: "미인식" }[state] || state || "--";
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
  const info = result.info || {};
  const health = result.health || {};
  const parts = [
    "정상",
    result.baud ? `${Number(result.baud).toLocaleString()} bps` : "",
    info.firmware ? `FW ${info.firmware}` : "",
    info.hardware ? `HW ${info.hardware}` : "",
    health.status ? `health ${health.status}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function policyLabel(policy) {
  if (!policy) return "버튼 실행 중에만 포트를 열고 바로 닫습니다.";
  return "ROS2가 포트를 사용 중이면 테스트하지 않고, 수동 테스트 후에는 STOP과 포트 해제를 수행합니다.";
}

function lidarTestMessage(result) {
  if (!result) return "정보 없음";
  if (!result.ok) return cleanLidarError(result, "RPLIDAR 응답을 받지 못했습니다.");
  const info = result.info || {};
  const health = result.health || {};
  const parts = [
    "정상 응답",
    result.baud ? `${Number(result.baud).toLocaleString()} bps` : "",
    info.firmware ? `펌웨어 ${info.firmware}` : "",
    info.hardware ? `하드웨어 ${info.hardware}` : "",
    health.status ? `상태 ${health.status}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function cleanLidarError(result, fallback) {
  const stateName = result?.state || result?.status?.state;
  const messages = {
    not_found: "LiDAR 장치를 찾지 못했습니다. USB 연결과 전원을 확인하세요.",
    in_use: "다른 프로세스가 LiDAR 포트를 사용 중이라 테스트를 건너뛰었습니다.",
    busy: "이미 다른 LiDAR 테스트가 실행 중입니다.",
    no_protocol_response: "직렬 장치는 보이지만 RPLIDAR Info/Health 응답이 없습니다.",
    no_scan_data: "스캔 데이터가 들어오지 않았습니다. 모터 전원과 baudrate를 확인하세요.",
    no_scan_points: "스캔 명령 후 유효한 거리 점을 받지 못했습니다.",
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
    value: item.ok ? "OK" : "대기",
  }));
}

function renderSensors(sensors) {
  renderStatusRows("sensorList", sensorRows(sensors));
}

function sensorRows(sensors) {
  return Object.entries(sensors).map(([key, item]) => ({
    title: sensorLabel(key),
    sub: [item.topic, item.state].filter(Boolean).join(" · "),
    ok: Boolean(item.ok),
    value: item.rate_hz ? `${Number(item.rate_hz).toFixed(1)} Hz` : "연결 전",
  }));
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
    return;
  }
  if (target.querySelector(".empty-state")) target.innerHTML = "";
  syncItems("containerList", docker.containers || [], (container) => container.id || container.name, createContainerRow, updateContainerRow);
}

function createContainerRow() {
  const row = document.createElement("div");
  row.className = "container-row";
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
  row.querySelector('[data-role="name"]').textContent = container.name || container.id;
  row.querySelector('[data-role="image"]').textContent = container.image || "";
  const stateEl = row.querySelector('[data-role="state"]');
  stateEl.className = `tag ${container.state === "running" ? "ok" : ""}`;
  stateEl.textContent = container.state || "--";
  row.querySelector('[data-role="cpu"]').textContent = fmtPercent(container.cpu_percent);
  row.querySelector('[data-role="memory"]').textContent = fmtBytes(container.memory_usage);
  row.querySelector('[data-role="status"]').textContent = container.status || "";
}

function renderChartReadouts(data) {
  const sys = data.system || {};
  const net = data.network || {};
  const docker = data.docker || {};
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
  renderReadout("gpuReadout", [
    { label: "GPU", value: fmtPercent(sys.gpu_percent), sub: "로드", color: colors.rose },
  ]);
  renderReadout("tempReadout", [
    { label: "Max", value: fmtTemp(sys.temperature_c), sub: "thermal zone", color: colors.amber },
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
}

function renderReadout(targetId, rows) {
  const target = $(targetId);
  if (!target) return;
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
  const active = stepVisualHistory(timestamp);
  drawCharts();
  if (active) state.animationFrame = requestAnimationFrame(animateCharts);
}

function stepVisualHistory(timestamp) {
  if (state.chartShift <= 0) return false;
  const elapsed = Math.max(0, timestamp - state.chartShiftStartedAt);
  state.chartShift = Math.max(0, 1 - elapsed / 720);
  return state.chartShift > 0;
}

function drawCharts() {
  const colors = chartColors();
  const tempMax = Math.max(90, ...state.history.temp.filter((v) => v !== null));
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
      ["chartGpu", [{ values: state.visualHistory.gpu, color: colors.rose, max: 100 }]],
      ["chartTemp", [{ values: state.visualHistory.temp, color: colors.amber, max: tempMax }]],
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
    "/research": [],
  };
  for (const [id, series] of groups[currentRoute()] || []) drawMultiLine(id, series);
  if (currentRoute() === "/system" && state.cpuMode === "separate") drawCoreCharts();
  if (currentRoute() === "/connection") renderActiveLidarMap();
}

function renderActiveLidarMap() {
  if (state.lidarMapMode === "3d") {
    renderLidarMap3d(state.lidarScan);
    return;
  }
  drawLidarMap(state.lidarScan);
}

function renderLidarMap3d(result) {
  window.ubtsLidar3d?.setScan?.(result);
}

function normalizeBps(values, key) {
  const max = Math.max(1, ...state.history[key].filter((v) => v !== null));
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
  const metrics = result?.metrics || {};
  const radius = Math.max(80, Math.min(w, h) * 0.42);
  const cx = w / 2;
  const cy = h / 2;
  const maxPointRange = points.reduce((max, point) => Math.max(max, Number(point.distance_m) || 0), 0);
  const range = Math.min(30, Math.max(1, (Number(metrics.max_distance_m) || maxPointRange || 1) * 1.15));

  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createRadialGradient(cx, cy, radius * 0.12, cx, cy, radius);
  gradient.addColorStop(0, cssVar("--chart-bg-top", "rgba(255,255,255,0.05)"));
  gradient.addColorStop(1, cssVar("--chart-bg-bottom", "rgba(255,255,255,0.01)"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  drawLidarGrid(ctx, cx, cy, radius, range);

  if (!points.length) {
    drawLidarEmpty(ctx, w, h, result?.message || "스캔 대기");
    return;
  }

  ctx.save();
  ctx.fillStyle = colors.cyan;
  ctx.shadowColor = colors.cyan;
  ctx.shadowBlur = 7;
  for (const point of points) {
    const x = cx + ((Number(point.x_m) || 0) / range) * radius;
    const y = cy - ((Number(point.y_m) || 0) / range) * radius;
    const quality = Math.max(0, Math.min(63, Number(point.quality) || 0));
    ctx.globalAlpha = Math.max(0.35, Math.min(0.92, 0.34 + quality / 90));
    ctx.beginPath();
    ctx.arc(x, y, 1.8, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  ctx.save();
  ctx.fillStyle = cssVar("--text", "#f7f9ff");
  ctx.font = "800 12px Inter, Arial, sans-serif";
  ctx.fillText(`${points.length} rendered / ${metrics.points || points.length} points`, 14, 22);
  ctx.fillText(`${fmtNumber(metrics.coverage_deg, 0)}° coverage`, 14, 40);
  ctx.restore();
}

function drawLidarGrid(ctx, cx, cy, radius, range) {
  ctx.save();
  ctx.strokeStyle = cssVar("--chart-grid", "rgba(255,255,255,0.1)");
  ctx.lineWidth = 1;
  for (let i = 1; i <= 4; i += 1) {
    const r = (radius / 4) * i;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = cssVar("--dim", "#7480a3");
    ctx.font = "800 10px Inter, Arial, sans-serif";
    ctx.fillText(`${fmtNumber((range / 4) * i, 1)}m`, cx + r + 4, cy - 4);
  }
  ctx.beginPath();
  ctx.moveTo(cx - radius, cy);
  ctx.lineTo(cx + radius, cy);
  ctx.moveTo(cx, cy - radius);
  ctx.lineTo(cx, cy + radius);
  ctx.stroke();
  ctx.fillStyle = cssVar("--muted", "#a9b4d2");
  ctx.font = "900 11px Inter, Arial, sans-serif";
  ctx.fillText("0°", cx + 8, cy - radius + 14);
  ctx.fillText("90°", cx + radius - 34, cy - 8);
  ctx.fillText("180°", cx + 8, cy + radius - 8);
  ctx.fillText("270°", cx - radius + 8, cy - 8);
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
window.addEventListener("ubts-lidar3d-ready", renderActiveLidarMap);
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
renderRoute();
loadPlan().catch(console.error);
loadDocs().catch(console.error);
tick();
setInterval(tick, POLL_INTERVAL_MS);
