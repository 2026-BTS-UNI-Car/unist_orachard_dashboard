const ROUTES = ["/", "/connection", "/system", "/network", "/docker", "/fan", "/research", "/rover-control", "/docs"];
const HISTORY_KEYS = ["cpu", "memory", "swap", "power", "temp", "rx", "tx", "dockerCpu", "dockerMemory", "fanTemp", "fanPwm"];
const CORE_COLORS = ["#55e2b2", "#38d8ff", "#2f76ff", "#8238c9", "#f21b58", "#f4bd4f", "#7dd3fc", "#fb7185"];
const MAX_POINTS = 90;
const CHART_BUFFER_POINTS = MAX_POINTS + 1;
const POLL_INTERVAL_MS = 1000;
const DPR_LIMIT = 1.5;
const CPU_MODE_KEY = "ubts-cpu-chart-mode";
const LIDAR_RANGE_KEY = "ubts-lidar-map-range-v2";
const LIDAR_RANGE_AUTO_KEY = "ubts-lidar-map-range-auto-v2";
const LIDAR_POINTS_KEY = "ubts-lidar-points-v1";
const LIDAR_OVERLAY_MODE_KEY = "ubts-lidar-overlay-mode-v1";
const ARCHIVE_OVERLAY_MODE_KEY = "ubts-archive-overlay-mode-v1";
const ARCHIVE_OVERLAY_VISIBLE_KEY = "ubts-archive-overlay-visible-v1";
const ARCHIVE_RANGE_KEY = "ubts-archive-lidar-range-v1";
const ARCHIVE_SEGMENT_SECONDS = 12;
const PERCEPTION_OVERLAY_KEY = "ubts-perception-overlay-mode-v1";
const LIDAR_SPEC_MAX_POINTS = 3200;
const LIDAR_COORDINATE_FRAME = "lidar_bridge_xy_v1";
const LIDAR_RECONNECT_DEBOUNCE_MS = 650;
const CAMERA_RESOLUTION_KEY = "ubts-camera-resolution-v1";
const CAMERA_FPS_KEY = "ubts-camera-fps-v1";
const CAMERA_OVERLAY_MODE_KEY = "ubts-camera-overlay-mode-v1";
const CAMERA_RECONNECT_DEBOUNCE_MS = 500;
const ROVER_SPEED_KEY = "ubts-rover-speed-v1";
const ROVER_FADE_START_KEY = "ubts-rover-fade-start-v1";
const ROVER_FADE_STOP_KEY = "ubts-rover-fade-stop-v1";
const ROVER_RECORD_KIND_KEY = "ubts-rover-record-kind-v1";
const ROVER_PANEL_COLLAPSED_KEY = "ubts-rover-panel-collapsed-v1";
const ROVER_CONTROL_INTERVAL_MS = 140;
const ROVER_MAX_LINEAR_MPS = 0.45;
const ROVER_MAX_ANGULAR_RPS = 1.4;
const DEFAULT_CAMERA_MODES = [
  { resolution: "1920x1080", width: 1920, height: 1080, fps: [30], label: "1080p 16:9" },
  { resolution: "1280x720", width: 1280, height: 720, fps: [60], label: "720p 16:9" },
  { resolution: "1024x768", width: 1024, height: 768, fps: [30], label: "768p 4:3" },
  { resolution: "800x600", width: 800, height: 600, fps: [60], label: "600p 4:3" },
  { resolution: "640x480", width: 640, height: 480, fps: [30], label: "480p 4:3" },
  { resolution: "320x240", width: 320, height: 240, fps: [30], label: "240p 4:3" },
  { resolution: "1280x1024", width: 1280, height: 1024, fps: [30], label: "1024p 5:4" },
];
const ORCHARD_MAP_LAYERS = [
  { id: "lidar", canvasId: "roverOrchardMapLidarCanvas", statsId: "roverOrchardMapLidarStats", label: "LiDAR-only", color: "#38d8ff" },
  { id: "camera", canvasId: "roverOrchardMapCameraCanvas", statsId: "roverOrchardMapCameraStats", label: "Camera-only", color: "#55e2b2" },
  { id: "fusion", canvasId: "roverOrchardMapFusionCanvas", statsId: "roverOrchardMapFusionStats", label: "Weighted Fusion", color: "#2f76ff" },
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
	  lidarOverlayMode: "both",
	  cameraLive: {
    enabled: false,
    socket: null,
    connected: false,
    frameTopicAlive: false,
    lastFrameAgeSec: null,
    frameCount: 0,
    displayFps: null,
    sourceFps: null,
    fpsWindowStartedAt: null,
    fpsWindowFrames: 0,
    sourceWidth: null,
    sourceHeight: null,
    frameId: "camera_link_optical",
    error: "",
    userStopped: false,
    reconnectTimer: null,
    pendingRestart: false,
    resolution: "1280x720",
    fps: 60,
    settingsInFlight: false,
    settingsError: "",
    lastFrameAt: null,
    lastOverlay: null,
    lastFrameSize: null,
  },
  cameraModes: DEFAULT_CAMERA_MODES,
  cameraModesSignature: JSON.stringify(DEFAULT_CAMERA_MODES),
  cameraModesSource: "fallback",
  cameraOverlayMode: "both",
	  roverControl: {
	    speedPct: 25,
	    linear: 0,
	    angular: 0,
	    targetLinear: 0,
	    targetAngular: 0,
	    currentLinear: 0,
	    currentAngular: 0,
	    fadeStart: true,
	    fadeStop: false,
	    activeKeys: new Set(),
	    activeButton: "",
	    stoppingWithFade: false,
	    commandTimer: null,
	    inFlight: false,
	    lastCommandAt: null,
	    lastError: "",
	    panelCollapsed: false,
	  },
	  recordings: {
	    status: null,
	    items: [],
	    selectedId: null,
	    selected: null,
	    timeline: null,
	    orchardMap: null,
	    loading: false,
	    actionInFlight: false,
	    kind: "both",
	    lastStatusAt: 0,
	  },
	  archivePlayback: {
	    playing: false,
	    timeS: 0,
	    durationS: 0,
	    startedAt: 0,
	    startTimeS: 0,
	    raf: null,
	    overlayMode: "both",
	    overlayVisible: true,
	    sync: true,
	    range: 25,
	    fallbackPreview: null,
	    cache: { lidarFrames: [], cameraFrames: [], objectUrls: new Map(), imageElements: new Map() },
	    segmentDurationS: ARCHIVE_SEGMENT_SECONDS,
	    segmentCache: new Map(),
	    loadingSegments: new Map(),
	    lastLidarRenderKey: "",
	    lastCameraRenderKey: "",
	    lastCameraOverlayRenderKey: "",
	  },
  perceptionOverlayMode: "fusion",
  orchardMapView: { scale: 1, offsetX: 0, offsetY: 0, dragging: false, dragMoved: false, lastX: 0, lastY: 0 },
  selectedContainerId: null,
  containerDetail: null,
  containerLogs: null,
  containerRequestInFlight: false,
  containerLogInFlight: false,
  containerLogsUpdatedAt: null,
  chartShift: 0,
  chartShiftStartedAt: 0,
  chartShiftDurationMs: POLL_INTERVAL_MS,
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

function fmtClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
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

async function postJson(path, payload = {}) {
  const res = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function currentRoute() {
  if (location.pathname === "/docs" || location.pathname.startsWith("/docs/")) return "/docs";
  return ROUTES.includes(location.pathname) ? location.pathname : "/";
}

function routeSupportsLidarLive(route = currentRoute()) {
  return route === "/connection" || route === "/rover-control";
}

function routeSupportsCameraLive(route = currentRoute()) {
  return route === "/connection" || route === "/rover-control";
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
    renderLiveCameraOverlays();
    if (!state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
    if (!state.cameraLive.socket && !state.cameraLive.userStopped) startCameraLive();
	  } else if (route === "/rover-control") {
	    renderActiveLidarMap();
	    renderLiveCameraOverlays();
	    renderRoverControl(state.telemetry || {});
	    renderRecordingStatus();
	    maybeRefreshRecordingStatus();
	    if (!state.recordings.items.length && !state.recordings.loading) loadRecordings().catch(console.error);
	  } else if (state.lidarLive.socket || state.lidarLive.enabled) {
	    closeLidarLive("Live 화면 밖에서는 LiDAR WebSocket을 닫습니다.", { userStopped: false });
	  }
	  if (route !== "/rover-control" && state.archivePlayback.playing) {
	    stopArchivePlayback(false);
	  }
  if (!routeSupportsCameraLive(route) && (state.cameraLive.socket || state.cameraLive.enabled)) {
    closeCameraLive("Live 화면 밖에서는 Camera WebSocket을 닫습니다.", { userStopped: false });
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
  document.querySelectorAll("[data-lidar-range-slider]").forEach((slider) => slider.addEventListener("input", (event) => {
    setLidarRange(Number(event.target.value), { auto: false, persist: true });
    requestChartAnimation();
  }));
  document.querySelectorAll("[data-lidar-points]").forEach((button) => {
    button.addEventListener("click", () => changeLidarPointLimit(button.dataset.lidarPoints));
  });
  document.querySelectorAll("[data-lidar-point-slider]").forEach((slider) => slider.addEventListener("input", (event) => {
    setLidarPointLimit(Number(event.target.value), { persist: true, reconnect: true });
  }));
  document.querySelectorAll("[data-lidar-overlay-mode]").forEach((button) => {
    button.addEventListener("click", () => setLidarOverlayMode(button.dataset.lidarOverlayMode, true));
  });
  initLidarRange();
  initLidarPointLimit();
  initLidarOverlayMode();
  setLidarDownloadFormat(state.lidarExport.format);
  renderLidarLiveState();
}

function initCameraLive() {
  try {
    state.cameraLive.resolution = localStorage.getItem(CAMERA_RESOLUTION_KEY) || state.cameraLive.resolution;
    state.cameraLive.fps = Number(localStorage.getItem(CAMERA_FPS_KEY) || state.cameraLive.fps);
    const storedOverlayMode = localStorage.getItem(CAMERA_OVERLAY_MODE_KEY);
    if (["raw", "both", "overlay"].includes(storedOverlayMode)) state.cameraOverlayMode = storedOverlayMode;
  } catch (_) {
    // Camera controls remain usable without localStorage.
  }
  enforceCameraSelection();
  document.querySelectorAll("[data-camera-resolution-select]").forEach((resolution) => {
    resolution.value = state.cameraLive.resolution;
    resolution.addEventListener("change", () => setCameraSettings({ resolution: resolution.value }));
  });
  document.querySelectorAll("[data-camera-fps-select]").forEach((fps) => {
    fps.value = String(state.cameraLive.fps);
    fps.addEventListener("change", () => setCameraSettings({ fps: Number(fps.value) }));
  });
  document.querySelectorAll("[data-camera-overlay-mode]").forEach((button) => {
    button.addEventListener("click", () => setCameraOverlayMode(button.dataset.cameraOverlayMode, true));
  });
  $("cameraLiveButton")?.addEventListener("click", toggleCameraLive);
  updateCameraControls();
  setCameraOverlayMode(state.cameraOverlayMode, false);
  renderCameraLiveState();
}

function normalizeCameraModes(rawModes) {
  const source = Array.isArray(rawModes) && rawModes.length ? rawModes : DEFAULT_CAMERA_MODES;
  const rows = [];
  const seen = new Set();
  source.forEach((item) => {
    if (!item) return;
    let resolution = String(item.resolution || "");
    if (!resolution.includes("x") && item.width && item.height) resolution = `${item.width}x${item.height}`;
    const [widthText, heightText] = resolution.toLowerCase().split("x");
    const width = Number(widthText);
    const height = Number(heightText);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 || seen.has(resolution)) return;
    const fps = [...new Set((Array.isArray(item.fps) ? item.fps : [])
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value >= 1)
      .map((value) => Math.abs(value - Math.round(value)) < 0.05 ? Math.round(value) : Number(value.toFixed(2))))].sort((a, b) => a - b);
    rows.push({ resolution, width, height, fps: fps.length ? fps : [30], label: item.label || "" });
    seen.add(resolution);
  });
  return rows.length ? rows : DEFAULT_CAMERA_MODES;
}

function applyCameraModeCatalog(rawModes, source = "fallback") {
  const modes = normalizeCameraModes(rawModes);
  const signature = JSON.stringify(modes);
  if (signature === state.cameraModesSignature && source === state.cameraModesSource) return;
  const before = `${state.cameraLive.resolution}@${state.cameraLive.fps}`;
  state.cameraModes = modes;
  state.cameraModesSignature = signature;
  state.cameraModesSource = source || "fallback";
  enforceCameraSelection();
  updateCameraControls();
  const after = `${state.cameraLive.resolution}@${state.cameraLive.fps}`;
  if (before !== after && (state.cameraLive.socket || state.cameraLive.enabled)) {
    scheduleCameraReconnectForSettings();
  }
}

function applyCameraStatus(camera = {}, cameraBridge = {}) {
  if (Number.isFinite(Number(cameraBridge.source_fps))) {
    state.cameraLive.sourceFps = Number(cameraBridge.source_fps);
  }
  if (cameraBridge.source_width && cameraBridge.source_height) {
    state.cameraLive.sourceWidth = cameraBridge.source_width;
    state.cameraLive.sourceHeight = cameraBridge.source_height;
  }
  if (state.cameraLive.settingsInFlight) return;
  const requested = camera.settings || camera.driver_requested || {};
  const resolution = requested.resolution || (requested.width && requested.height ? `${requested.width}x${requested.height}` : "");
  const fps = Number(requested.fps);
  const resolutions = cameraResolutionOptions();
  if (!resolutions.includes(resolution) || !Number.isFinite(fps)) return;
  const allowed = cameraFpsOptions(resolution);
  const nextFps = chooseCameraFps(allowed, fps);
  const changed = state.cameraLive.resolution !== resolution || Number(state.cameraLive.fps) !== Number(nextFps);
  state.cameraLive.resolution = resolution;
  state.cameraLive.fps = nextFps;
  if (changed) updateCameraControls();
}

function cameraResolutionOptions() {
  return state.cameraModes.map((mode) => mode.resolution);
}

function cameraResolutionOptionLabels() {
  return state.cameraModes.map((mode) => ({
    value: mode.resolution,
    label: mode.label ? `${mode.resolution} · ${mode.label}` : mode.resolution,
  }));
}

function cameraFpsOptions(resolution = state.cameraLive.resolution) {
  const mode = state.cameraModes.find((item) => item.resolution === resolution);
  return mode?.fps?.length ? mode.fps : [30];
}

function chooseCameraFps(allowed, preferred) {
  const current = Number(preferred);
  if (allowed.includes(current)) return current;
  const lower = allowed.filter((value) => value <= current).pop();
  return lower || allowed[allowed.length - 1] || 30;
}

function enforceCameraSelection() {
  const resolutions = cameraResolutionOptions();
  if (!resolutions.includes(state.cameraLive.resolution)) {
    state.cameraLive.resolution = resolutions.includes("1280x720") ? "1280x720" : resolutions[0] || "1280x720";
  }
  const fps = cameraFpsOptions(state.cameraLive.resolution);
  state.cameraLive.fps = chooseCameraFps(fps, state.cameraLive.fps);
}

async function setCameraSettings(next) {
  if (next.resolution) {
    const resolutions = cameraResolutionOptions();
    state.cameraLive.resolution = resolutions.includes(next.resolution) ? next.resolution : (resolutions[0] || "1280x720");
  }
  if (next.fps) state.cameraLive.fps = Number(next.fps);
  enforceCameraSelection();
  try {
    localStorage.setItem(CAMERA_RESOLUTION_KEY, state.cameraLive.resolution);
    localStorage.setItem(CAMERA_FPS_KEY, String(state.cameraLive.fps));
  } catch (_) {
    // Ignore storage failures.
  }
  updateCameraControls();
  state.cameraLive.settingsError = "";
  state.cameraLive.settingsInFlight = true;
  setCameraResult("testing", `카메라 캡처 설정 적용 중 · ${state.cameraLive.resolution} · ${state.cameraLive.fps} fps`);
  try {
    const [width, height] = state.cameraLive.resolution.split("x").map((value) => Number(value));
    const res = await fetch("/api/camera/settings", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resolution: state.cameraLive.resolution,
        width,
        height,
        fps: state.cameraLive.fps,
      }),
    });
    const result = await res.json();
    if (!res.ok || !result.ok) {
      throw new Error(result.error || `HTTP ${res.status}`);
    }
    if (Array.isArray(result.supported_view_modes)) {
      applyCameraModeCatalog(result.supported_view_modes, "settings");
    }
    setCameraResult("testing", `캡처 설정 저장됨 · ${state.cameraLive.resolution} · ${state.cameraLive.fps} fps`);
    scheduleCameraReconnectForSettings(900);
  } catch (err) {
    state.cameraLive.settingsError = err.message || String(err);
    setCameraResult("bad", `카메라 설정 적용 실패: ${state.cameraLive.settingsError}`);
  } finally {
    state.cameraLive.settingsInFlight = false;
    updateCameraControls();
  }
}

function updateCameraControls() {
  text("cameraResolutionValue", state.cameraLive.resolution);
  text("cameraFpsValue", `${state.cameraLive.fps} fps`);
  document.querySelectorAll("[data-camera-resolution-value]").forEach((el) => {
    el.textContent = state.cameraLive.resolution;
  });
  document.querySelectorAll("[data-camera-fps-value]").forEach((el) => {
    el.textContent = `${state.cameraLive.fps} fps`;
  });
  document.querySelectorAll("[data-camera-resolution-select]").forEach((resolution) => {
    syncSelectOptions(resolution, cameraResolutionOptionLabels());
    if (document.activeElement !== resolution) resolution.value = state.cameraLive.resolution;
  });
  document.querySelectorAll("[data-camera-fps-select]").forEach((fps) => {
    syncSelectOptions(fps, cameraFpsOptions().map((value) => ({ value: String(value), label: `${value} fps` })));
    if (document.activeElement !== fps) fps.value = String(state.cameraLive.fps);
  });
}

function setCameraOverlayMode(mode, persist = false) {
  state.cameraOverlayMode = ["raw", "both", "overlay"].includes(mode) ? mode : "both";
  if (persist) {
    try {
      localStorage.setItem(CAMERA_OVERLAY_MODE_KEY, state.cameraOverlayMode);
    } catch (_) {
      // Ignore storage failures.
    }
  }
  document.querySelectorAll("[data-camera-overlay-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.cameraOverlayMode === state.cameraOverlayMode);
  });
  state.archivePlayback.lastCameraOverlayRenderKey = "";
  renderLiveCameraOverlays();
  renderArchiveFrame();
}

function syncSelectOptions(select, options) {
  const signature = JSON.stringify(options);
  if (select.dataset.optionsSignature === signature) return;
  select.dataset.optionsSignature = signature;
  select.replaceChildren(...options.map((option) => {
    const node = document.createElement("option");
    node.value = option.value;
    node.textContent = option.label;
    return node;
  }));
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
  document.querySelectorAll("[data-lidar-range-value]").forEach((el) => {
    el.textContent = state.lidarMap.auto ? `${rangeLabel} auto` : rangeLabel;
  });
  document.querySelectorAll("[data-lidar-range-slider]").forEach((slider) => {
    if (document.activeElement !== slider) slider.value = String(state.lidarMap.range);
  });
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
  document.querySelectorAll("[data-lidar-point-value]").forEach((el) => {
    el.textContent = `${state.lidarPoints.value} pts`;
  });
  document.querySelectorAll("[data-lidar-point-slider]").forEach((slider) => {
    if (document.activeElement !== slider) slider.value = String(state.lidarPoints.value);
  });
  document.querySelectorAll("[data-lidar-points]").forEach((button) => {
    const action = button.dataset.lidarPoints;
    button.disabled = (action === "down" && state.lidarPoints.value <= state.lidarPoints.min)
      || (action === "up" && state.lidarPoints.value >= state.lidarPoints.max);
  });
}

function initLidarOverlayMode() {
  try {
    state.lidarOverlayMode = localStorage.getItem(LIDAR_OVERLAY_MODE_KEY) || state.lidarOverlayMode;
  } catch (_) {
    // Overlay mode remains session-local without storage.
  }
  setLidarOverlayMode(state.lidarOverlayMode, false);
}

function setLidarOverlayMode(mode, persist = false) {
  state.lidarOverlayMode = ["raw", "both", "overlay"].includes(mode) ? mode : "both";
  if (persist) {
    try {
      localStorage.setItem(LIDAR_OVERLAY_MODE_KEY, state.lidarOverlayMode);
    } catch (_) {
      // Ignore storage failures.
    }
  }
  document.querySelectorAll("[data-lidar-overlay-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.lidarOverlayMode === state.lidarOverlayMode);
  });
  renderActiveLidarMap();
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
  state.chartShiftDurationMs = Math.max(500, POLL_INTERVAL_MS * 1.08);
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
  if (target.length >= CHART_BUFFER_POINTS) {
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
  renderRoverControl(data);
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
  if (!routeSupportsLidarLive()) {
    debugLog("ws", "skip start outside live routes");
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
      if (socket !== state.lidarLive.socket || !routeSupportsLidarLive()) return;
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
      const shouldReconnect = state.lidarLive.enabled && !state.lidarLive.userStopped && routeSupportsLidarLive();
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
          if (routeSupportsLidarLive() && !state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
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
  if (!routeSupportsLidarLive()) return;
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
  if (!routeSupportsLidarLive()) return;
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
      if (routeSupportsLidarLive() && !state.lidarLive.socket && !state.lidarLive.userStopped) startLidarLive();
    }, 160);
  }
}

function toggleCameraLive() {
  if (state.cameraLive.connected || state.cameraLive.socket) {
    stopCameraLive("사용자가 카메라 실시간 연결을 중지했습니다.");
    return;
  }
  startCameraLive();
}

function cameraWebSocketUrl() {
  const params = new URLSearchParams({
    resolution: state.cameraLive.resolution,
    fps: String(state.cameraLive.fps),
  });
  if (window.UBTS_CAMERA_WS_URL) {
    const separator = String(window.UBTS_CAMERA_WS_URL).includes("?") ? "&" : "?";
    return `${window.UBTS_CAMERA_WS_URL}${separator}${params.toString()}`;
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws/camera?${params.toString()}`;
}

function startCameraLive() {
  if (!routeSupportsCameraLive()) {
    debugLog("camera", "skip start outside live routes");
    return;
  }
  if (state.cameraLive.socket) return;
  clearCameraReconnectTimer();
  const url = cameraWebSocketUrl();
  state.cameraLive.enabled = true;
  state.cameraLive.userStopped = false;
  state.cameraLive.connected = false;
  state.cameraLive.frameTopicAlive = false;
  state.cameraLive.error = "";
  renderCameraLiveState("연결 중");
  setCameraResult("testing", "Camera WebSocket 연결 중");
  debugLog("camera", `connecting ${url}`);
  try {
    const socket = new WebSocket(url);
    state.cameraLive.socket = socket;
    socket.addEventListener("open", () => {
      if (socket !== state.cameraLive.socket) return;
      state.cameraLive.connected = true;
      state.cameraLive.error = "";
      debugLog("camera", "open");
      renderCameraLiveState();
      if (state.cameraLive.pendingRestart) {
        state.cameraLive.pendingRestart = false;
        scheduleCameraReconnectForSettings(80);
      }
    });
    socket.addEventListener("message", (event) => {
      if (socket !== state.cameraLive.socket || !routeSupportsCameraLive()) return;
      handleCameraLiveMessage(event.data);
    });
    socket.addEventListener("error", (event) => {
      if (socket !== state.cameraLive.socket) return;
      state.cameraLive.error = "Camera WebSocket 연결 오류";
      debugLog("camera", "error", event);
      renderCameraLiveState();
    });
    socket.addEventListener("close", (event) => {
      if (socket !== state.cameraLive.socket) return;
      const shouldReconnect = state.cameraLive.enabled && !state.cameraLive.userStopped && routeSupportsCameraLive();
      state.cameraLive.socket = null;
      state.cameraLive.connected = false;
      state.cameraLive.frameTopicAlive = false;
      debugLog("camera", `close code=${event.code} reason=${event.reason || "-"}`);
      renderCameraLiveState(state.cameraLive.error || "disconnected");
      if (state.cameraLive.pendingRestart && shouldReconnect) {
        state.cameraLive.pendingRestart = false;
        scheduleCameraReconnectForSettings(120);
      } else if (shouldReconnect) {
        state.cameraLive.reconnectTimer = window.setTimeout(() => {
          state.cameraLive.reconnectTimer = null;
          if (routeSupportsCameraLive() && !state.cameraLive.socket && !state.cameraLive.userStopped) startCameraLive();
        }, 1500);
      }
    });
  } catch (err) {
    state.cameraLive.socket = null;
    state.cameraLive.connected = false;
    state.cameraLive.frameTopicAlive = false;
    state.cameraLive.error = err.message;
    debugLog("camera", "create failed", err);
    renderCameraLiveState();
  }
}

function stopCameraLive(message = "disconnected") {
  closeCameraLive(message, { userStopped: true });
}

function closeCameraLive(message = "disconnected", options = {}) {
  const userStopped = options.userStopped !== undefined ? Boolean(options.userStopped) : true;
  if (options.clearReconnect !== false) clearCameraReconnectTimer();
  const socket = state.cameraLive.socket;
  state.cameraLive.enabled = false;
  state.cameraLive.userStopped = userStopped;
  state.cameraLive.socket = null;
  state.cameraLive.connected = false;
  state.cameraLive.frameTopicAlive = false;
  state.cameraLive.error = "";
  state.cameraLive.pendingRestart = false;
  if (socket) closeSocketAfterConnect(socket, message);
  debugLog("camera", `stopped: ${message}`);
  renderCameraLiveState(message);
}

function clearCameraReconnectTimer() {
  if (!state.cameraLive.reconnectTimer) return;
  window.clearTimeout(state.cameraLive.reconnectTimer);
  state.cameraLive.reconnectTimer = null;
}

function scheduleCameraReconnectForSettings(delayMs = CAMERA_RECONNECT_DEBOUNCE_MS) {
  if (!routeSupportsCameraLive()) return;
  if (!state.cameraLive.socket && !state.cameraLive.enabled) return;
  clearCameraReconnectTimer();
  state.cameraLive.pendingRestart = true;
  state.cameraLive.reconnectTimer = window.setTimeout(() => {
    state.cameraLive.reconnectTimer = null;
    restartCameraLiveForSettings();
  }, Math.max(0, delayMs));
  debugLog("camera", `settings restart scheduled ${state.cameraLive.resolution}@${state.cameraLive.fps}`);
}

function restartCameraLiveForSettings() {
  if (!routeSupportsCameraLive()) return;
  const wasEnabled = state.cameraLive.enabled && !state.cameraLive.userStopped;
  const socket = state.cameraLive.socket;
  if (socket?.readyState === WebSocket.CONNECTING) {
    state.cameraLive.pendingRestart = true;
    return;
  }
  clearCameraReconnectTimer();
  state.cameraLive.pendingRestart = false;
  if (socket) {
    state.cameraLive.socket = null;
    state.cameraLive.connected = false;
    closeSocketAfterConnect(socket, "camera settings changed");
  }
  if (wasEnabled) {
    window.setTimeout(() => {
      if (routeSupportsCameraLive() && !state.cameraLive.socket && !state.cameraLive.userStopped) startCameraLive();
    }, 160);
  }
}

function noteCameraFrame(payload) {
  const now = Date.now();
  if (!state.cameraLive.fpsWindowStartedAt) {
    state.cameraLive.fpsWindowStartedAt = now;
    state.cameraLive.fpsWindowFrames = 0;
  }
  state.cameraLive.fpsWindowFrames += 1;
  const elapsed = (now - state.cameraLive.fpsWindowStartedAt) / 1000;
  if (elapsed >= 0.8) {
    state.cameraLive.displayFps = state.cameraLive.fpsWindowFrames / elapsed;
    state.cameraLive.fpsWindowStartedAt = now;
    state.cameraLive.fpsWindowFrames = 0;
  }
  const sourceFps = Number(payload.source_fps);
  if (Number.isFinite(sourceFps) && sourceFps > 0) {
    state.cameraLive.sourceFps = sourceFps;
  }
}

function cameraFpsLabel(value) {
  const fps = Number(value);
  if (!Number.isFinite(fps) || fps <= 0) return "--";
  return `${fmtNumber(fps, fps >= 10 ? 0 : 1)} fps`;
}

function handleCameraLiveMessage(raw) {
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    state.cameraLive.error = "잘못된 Camera WebSocket 메시지";
    debugLog("camera", "invalid message", raw);
    renderCameraLiveState();
    return;
  }
  if (payload.type === "status") {
    state.cameraLive.frameTopicAlive = Boolean(payload.frame_topic_alive);
    state.cameraLive.lastFrameAgeSec = payload.last_frame_age_sec;
    state.cameraLive.frameCount = payload.frame_count || state.cameraLive.frameCount || 0;
    if (Number.isFinite(Number(payload.source_fps))) state.cameraLive.sourceFps = Number(payload.source_fps);
    state.cameraLive.sourceWidth = payload.source_width;
    state.cameraLive.sourceHeight = payload.source_height;
    state.cameraLive.frameId = payload.frame_id || state.cameraLive.frameId;
    state.cameraLive.error = payload.error || (payload.frame_topic_alive === false ? "ROS2 /camera/image_raw 대기" : "");
    if (payload.error) debugLog("camera", "status", payload);
    if (!payload.frame_topic_alive) setCameraResult("testing", payload.error || "ROS2 카메라 프레임 수신 대기");
    renderCameraLiveState();
    renderLiveSensorLinks();
    return;
  }
  if (payload.type === "frame") {
    state.cameraLive.frameTopicAlive = true;
    state.cameraLive.lastFrameAgeSec = 0;
    state.cameraLive.frameCount = payload.frame_count || state.cameraLive.frameCount + 1;
    noteCameraFrame(payload);
    state.cameraLive.sourceWidth = payload.source_width;
    state.cameraLive.sourceHeight = payload.source_height;
    state.cameraLive.frameId = payload.frame_id || state.cameraLive.frameId;
    state.cameraLive.lastFrameAt = Date.now();
    state.cameraLive.lastOverlay = payload.overlay || null;
    state.cameraLive.lastFrameSize = {
      width: Number(payload.width || payload.source_width || 0),
      height: Number(payload.height || payload.source_height || 0),
    };
    const overlayError = cameraOverlayErrorText(payload.overlay);
    state.cameraLive.error = overlayError;
    const src = payload.data ? `data:${payload.mime || "image/jpeg"};base64,${payload.data}` : "";
    const image = $("cameraLiveImage");
    const roverImage = $("roverCameraImage");
    if (image && src) image.src = src;
    if (roverImage && src) roverImage.src = src;
    const source = payload.source_width && payload.source_height ? `${payload.source_width}x${payload.source_height}` : "--";
    const output = payload.width && payload.height ? `${payload.width}x${payload.height}` : state.cameraLive.resolution;
    const rate = state.cameraLive.sourceFps || state.cameraLive.displayFps || state.cameraLive.fps;
    const modeText = source === output ? source : `source ${source} · output ${output}`;
    if (overlayError) {
      setCameraResult("error", `AI 비전 오류 · ${overlayError}`);
    } else {
      setCameraResult("ok", `실시간 수신 중 · ${modeText} · ${cameraFpsLabel(rate)}`);
    }
    renderCameraLiveState();
    renderLiveCameraOverlays();
    renderLiveSensorLinks();
    return;
  }
  if (payload.type === "socket") {
    state.cameraLive.error = payload.ok ? "" : payload.message || "socket status error";
    debugLog("camera", "socket", payload);
    renderCameraLiveState();
  }
}

function renderCameraLiveState(overrideLabel) {
  const button = $("cameraLiveButton");
  if (button) {
    button.textContent = state.cameraLive.socket && !state.cameraLive.connected
      ? "연결 중"
      : state.cameraLive.connected ? "실시간 중지" : "실시간 연결";
    button.classList.toggle("active", state.cameraLive.connected);
  }
  const source = state.cameraLive.sourceWidth && state.cameraLive.sourceHeight
    ? `${state.cameraLive.sourceWidth}x${state.cameraLive.sourceHeight}`
    : "--";
  const socketText = overrideLabel || (state.cameraLive.connected ? "connected" : state.cameraLive.error || "disconnected");
  text("cameraFrameCaption", state.cameraLive.frameTopicAlive ? `${state.cameraLive.frameId} · source ${source}` : socketText);
  const frameAge = state.cameraLive.lastFrameAgeSec === null || state.cameraLive.lastFrameAgeSec === undefined
    ? "--"
    : `${fmtNumber(state.cameraLive.lastFrameAgeSec, 1)}s ago`;
  const captureTarget = `${state.cameraLive.resolution} · ${state.cameraLive.fps} fps target`;
  const observedFps = state.cameraLive.sourceFps || state.cameraLive.displayFps;
  const observedDetail = `ROS ${cameraFpsLabel(state.cameraLive.sourceFps)} · WS ${cameraFpsLabel(state.cameraLive.displayFps)}`;
  const colors = chartColors();
  const overlayError = cameraOverlayErrorText(state.cameraLive.lastOverlay);
  const aiVisionValue = state.cameraLive.lastOverlay
    ? overlayError ? "ERROR" : "ON"
    : "WAIT";
  const aiVisionSub = overlayError || state.cameraLive.lastOverlay?.backend || state.cameraLive.lastOverlay?.method || "local AI worker required";
  const aiVisionColor = overlayError ? colors.rose : state.cameraLive.lastOverlay ? colors.good : colors.amber;
  renderReadout("cameraLiveStats", [
    { label: "Socket", value: state.cameraLive.connected ? "ON" : "OFF", sub: socketText, color: state.cameraLive.connected ? colors.good : colors.rose },
    { label: "Capture", value: state.cameraLive.resolution, sub: `${state.cameraLive.fps} fps target`, color: colors.amber },
    { label: "Source", value: source, sub: state.cameraLive.frameId || "camera_link_optical", color: colors.cyan },
    { label: "AI Vision", value: aiVisionValue, sub: aiVisionSub, color: aiVisionColor },
    { label: "Observed FPS", value: cameraFpsLabel(observedFps), sub: `${observedDetail} · frames ${state.cameraLive.frameCount || 0} · ${frameAge}`, color: colors.blue },
  ]);
  text("roverCameraCaption", state.cameraLive.frameTopicAlive ? `${state.cameraLive.frameId} · source ${source} · capture ${captureTarget}` : socketText);
  const roverButton = $("roverCameraToggle");
  if (roverButton) {
    roverButton.textContent = state.cameraLive.socket && !state.cameraLive.connected
      ? "연결 중"
      : state.cameraLive.connected ? "스트리밍 중지" : "스트리밍 시작";
    roverButton.classList.toggle("active", state.cameraLive.connected);
  }
  renderReadout("roverCameraStats", [
    { label: "Socket", value: state.cameraLive.connected ? "ON" : "OFF", sub: socketText, color: state.cameraLive.connected ? colors.good : colors.rose },
    { label: "Capture", value: state.cameraLive.resolution, sub: `${state.cameraLive.fps} fps target`, color: colors.amber },
    { label: "Source", value: source, sub: state.cameraLive.frameId || "camera_link_optical", color: colors.cyan },
    { label: "AI Vision", value: aiVisionValue, sub: aiVisionSub, color: aiVisionColor },
    { label: "Observed FPS", value: cameraFpsLabel(observedFps), sub: `${observedDetail} · frames ${state.cameraLive.frameCount || 0} · ${frameAge}`, color: colors.blue },
  ]);
}

function renderLiveCameraOverlays() {
  drawCameraOverlayOnCanvas($("cameraLiveOverlayCanvas"), state.cameraLive.lastOverlay, {
    frameSize: state.cameraLive.lastFrameSize,
  });
  drawCameraOverlayOnCanvas($("roverCameraOverlayCanvas"), state.cameraLive.lastOverlay, {
    frameSize: state.cameraLive.lastFrameSize,
  });
}

function renderArchiveCameraOverlay(cameraFrame) {
  drawCameraOverlayOnCanvas($("roverArchiveCameraOverlayCanvas"), cameraFrame?.overlay || null, {
    frameSize: { width: cameraFrame?.width || cameraFrame?.source_width, height: cameraFrame?.height || cameraFrame?.source_height },
    overlayVisible: state.archivePlayback.overlayVisible,
  });
}

function shouldShowCameraOverlay() {
  return ["camera", "fusion"].includes(state.perceptionOverlayMode) && state.cameraOverlayMode !== "raw";
}

function cameraOverlayErrorText(overlay) {
  if (!overlay || overlay.ok !== false) return "";
  return overlay.error || overlay.method || "local vision AI worker unavailable";
}

function drawCameraOverlayOnCanvas(canvas, overlay, options = {}) {
  if (!canvas) return;
  const frame = canvas.closest(".camera-frame, .rover-camera-frame, .archive-camera-frame");
  const mode = options.overlayVisible === false || !shouldShowCameraOverlay() ? "raw" : state.cameraOverlayMode;
  frame?.classList.toggle("overlay-only", mode === "overlay");
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { ctx, w, h } = prepared;
  ctx.clearRect(0, 0, w, h);
  if (mode === "raw") return;
  const overlayError = cameraOverlayErrorText(overlay);
  if (overlayError) {
    drawCameraOverlayError(ctx, w, h, overlayError, mode);
    return;
  }

  const detections = (overlay?.detections || [])
    .filter((item) => Number(item?.confidence) >= 0.12 && Array.isArray(item?.bbox))
    .slice(0, 16);
  const imageInfo = overlay?.image || options.frameSize || {};
  const imageW = Number(imageInfo.width || options.frameSize?.width || 0);
  const imageH = Number(imageInfo.height || options.frameSize?.height || 0);
  if (!imageW || !imageH) return;
  const scale = Math.min(w / imageW, h / imageH);
  const drawW = imageW * scale;
  const drawH = imageH * scale;
  const offsetX = (w - drawW) / 2;
  const offsetY = (h - drawH) / 2;
  const labelBoxes = [];

  ctx.save();
  ctx.beginPath();
  ctx.rect(offsetX, offsetY, drawW, drawH);
  ctx.clip();
  if (mode === "overlay") {
    ctx.fillStyle = "rgba(2, 6, 23, 0.7)";
    ctx.fillRect(offsetX, offsetY, drawW, drawH);
  }
  for (const detection of detections) {
    const [bx, by, bw, bh] = detection.bbox.map((value) => Number(value) || 0);
    const x = offsetX + bx * scale;
    const y = offsetY + by * scale;
    const boxW = bw * scale;
    const boxH = bh * scale;
    const confidence = Math.max(0, Math.min(1, Number(detection.confidence) || 0));
    const color = cameraConfidenceColor(confidence);
    ctx.globalAlpha = 0.96;
    ctx.strokeStyle = color.stroke;
    ctx.fillStyle = color.fill;
    ctx.lineWidth = Math.max(2, Math.min(4, 2 + confidence * 2));
    drawCameraDetectionShape(ctx, detection, { offsetX, offsetY, scale, color, x, y, boxW, boxH, confidence });
    drawCameraConfidenceLabel(ctx, `${Math.round(confidence * 100)}%`, x, y, boxW, color, labelBoxes);
  }
  ctx.restore();
}

function drawCameraOverlayError(ctx, w, h, message, mode) {
  ctx.save();
  if (mode === "overlay") {
    ctx.fillStyle = "rgba(2, 6, 23, 0.78)";
    ctx.fillRect(0, 0, w, h);
  }
  const maxW = Math.min(w - 32, 560);
  const x = Math.max(16, (w - maxW) / 2);
  const y = Math.max(18, h * 0.08);
  ctx.fillStyle = "rgba(127, 29, 29, 0.82)";
  ctx.strokeStyle = cssVar("--chart-rose", "#ff5d7e");
  ctx.lineWidth = 2;
  roundRectPath(ctx, x, y, maxW, 72, 8);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#fff7f8";
  ctx.font = "900 14px Inter, Arial, sans-serif";
  ctx.fillText("Camera AI unavailable", x + 16, y + 27);
  ctx.font = "700 12px Inter, Arial, sans-serif";
  wrapCanvasText(ctx, String(message), x + 16, y + 50, maxW - 32, 16, 2);
  ctx.restore();
}

function roundRectPath(ctx, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function wrapCanvasText(ctx, textValue, x, y, maxWidth, lineHeight, maxLines) {
  const words = String(textValue || "").split(/\s+/).filter(Boolean);
  let line = "";
  let lines = 0;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxWidth && line) {
      ctx.fillText(lines + 1 >= maxLines ? `${line.slice(0, Math.max(0, line.length - 3))}...` : line, x, y + lines * lineHeight);
      lines += 1;
      line = word;
      if (lines >= maxLines) return;
    } else {
      line = next;
    }
  }
  if (line && lines < maxLines) ctx.fillText(line, x, y + lines * lineHeight);
}

function drawCameraDetectionShape(ctx, detection, viewport) {
  if (Array.isArray(detection.contour) && detection.contour.length >= 3) {
    drawCameraContour(ctx, detection.contour, viewport);
    return;
  }
  if (Array.isArray(detection.line) && detection.line.length >= 4) {
    drawCameraLine(ctx, detection, viewport);
    return;
  }
  ctx.beginPath();
  ctx.rect(viewport.x, viewport.y, viewport.boxW, viewport.boxH);
  ctx.fill();
  ctx.stroke();
}

function cameraOverlayPoint(point, viewport) {
  return {
    x: viewport.offsetX + (Number(point?.[0]) || 0) * viewport.scale,
    y: viewport.offsetY + (Number(point?.[1]) || 0) * viewport.scale,
  };
}

function drawCameraContour(ctx, contour, viewport) {
  ctx.save();
  ctx.globalAlpha = 0.18 + viewport.confidence * 0.16;
  ctx.fillStyle = viewport.color.fill;
  ctx.beginPath();
  contour.forEach((point, index) => {
    const p = cameraOverlayPoint(point, viewport);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 0.95;
  ctx.strokeStyle = viewport.color.stroke;
  ctx.lineWidth = Math.max(2.4, Math.min(5, 2.4 + viewport.confidence * 2.3));
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.restore();
}

function drawCameraLine(ctx, detection, viewport) {
  const [x1, y1, x2, y2] = detection.line.map((value) => Number(value) || 0);
  const a = cameraOverlayPoint([x1, y1], viewport);
  const b = cameraOverlayPoint([x2, y2], viewport);
  const lineWidth = Math.max(4, Math.min(12, (Number(detection.line_width_px) || 7) * viewport.scale));
  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = viewport.color.fill;
  ctx.globalAlpha = 0.42;
  ctx.lineWidth = lineWidth + 5;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  ctx.strokeStyle = viewport.color.stroke;
  ctx.globalAlpha = 0.96;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  ctx.restore();
}

function cameraConfidenceColor(confidence) {
  if (confidence >= 0.78) return { stroke: cssVar("--chart-blue", "#2f76ff"), fill: "rgba(47,118,255,0.17)" };
  if (confidence >= 0.58) return { stroke: cssVar("--chart-good", "#55e2b2"), fill: "rgba(85,226,178,0.14)" };
  return { stroke: cssVar("--chart-amber", "#f4bd4f"), fill: "rgba(244,189,79,0.12)" };
}

function drawCameraConfidenceLabel(ctx, label, x, y, boxW, color, placed) {
  ctx.save();
  ctx.font = "900 11px Inter, Arial, sans-serif";
  const labelW = Math.ceil(ctx.measureText(label).width) + 10;
  const labelH = 18;
  let lx = Math.max(4, x + 4);
  let ly = Math.max(4, y + 4);
  if (lx + labelW > x + boxW - 2) lx = Math.max(4, x + boxW - labelW - 4);
  const rect = { x: lx, y: ly, w: labelW, h: labelH };
  const overlaps = placed.some((item) => rectsOverlap(rect, item));
  if (overlaps) {
    ctx.fillStyle = color.stroke;
    ctx.beginPath();
    ctx.arc(x + Math.min(Math.max(8, boxW - 8), 16), y + 10, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return;
  }
  placed.push(rect);
  ctx.fillStyle = "rgba(2, 6, 23, 0.82)";
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  ctx.strokeStyle = color.stroke;
  ctx.lineWidth = 1;
  ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  ctx.fillStyle = "#ffffff";
  ctx.textBaseline = "middle";
  ctx.fillText(label, rect.x + 5, rect.y + rect.h / 2 + 0.5);
  ctx.restore();
}

function rectsOverlap(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function setCameraResult(kind, message) {
  const target = $("cameraLiveResult");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
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
	    if (state.lidarLive.messageCount % 10 === 1) {
	      debugLog("ws", "scan", {
	        source: payload.source,
	        points: result.points.length,
	        walls: result.analysis?.walls?.length || 0,
	        objects: result.analysis?.objects?.length || 0,
	      });
	    }
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
	    analysis: normalizeLidarOverlay(payload.overlay || payload.analysis),
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
  const roverButton = $("roverLidarToggle");
  if (roverButton) {
    roverButton.textContent = state.lidarLive.socket && !state.lidarLive.connected
      ? "연결 중"
      : state.lidarLive.connected ? "LiDAR 중지" : "LiDAR 시작";
    roverButton.classList.toggle("active", state.lidarLive.connected);
  }
  text("lidarLiveDevice", state.telemetry?.connection?.lidar?.device || "--");
  text("lidarLiveTopic", state.lidarLive.scanTopicAlive ? `${lidarSourceLabel(state.lidarLive.source)} live` : lidarSourceLabel(state.lidarLive.source));
  const socketText = overrideLabel || (state.lidarLive.connected ? "connected" : state.lidarLive.error || "disconnected");
  text("lidarLiveSocket", socketText);
  text("lidarLiveAge", lidarAgeLabel());
  const pointLabel = state.lidarLive.pointCount
    ? `${state.lidarLive.pointCount}/${state.lidarLive.requestedPointCount || state.lidarPoints.value}`
    : "--";
  text("lidarLivePoints", pointLabel);
  const colors = chartColors();
  renderReadout("roverLidarStats", [
    { label: "Socket", value: state.lidarLive.connected ? "ON" : "OFF", sub: socketText, color: state.lidarLive.connected ? colors.good : colors.rose },
    { label: "Source", value: state.lidarLive.scanTopicAlive ? "live" : lidarSourceLabel(state.lidarLive.source), sub: state.telemetry?.connection?.lidar?.device || "/scan", color: colors.cyan },
    { label: "Points", value: pointLabel, sub: lidarAgeLabel(), color: colors.blue },
  ]);
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
  const rotatedAngle = Number.isFinite(angleDeg) ? (angleDeg + 180) % 360 : null;
  const displayAngle = rotatedAngle === null ? null : (360 - rotatedAngle) % 360;
  const displayX = xM;
  const displayY = -yM;
  return {
    angle_deg: displayAngle === null ? null : Number(displayAngle.toFixed(3)),
    distance_m: Number(distanceM.toFixed(3)),
    quality: Number.isFinite(Number(point.quality)) ? Number(point.quality) : 0,
    x_m: Number(displayX.toFixed(3)),
    y_m: Number(displayY.toFixed(3)),
  };
}

function normalizeLidarOverlay(analysis) {
  if (!analysis) return null;
  const overlayPoint = (point) => {
    if (!point) return {};
    const xM = Number(point.x_m);
    const yM = Number(point.y_m);
    return {
      ...point,
      x_m: Number.isFinite(xM) ? Number(xM.toFixed(3)) : point.x_m,
      y_m: Number.isFinite(yM) ? Number((-yM).toFixed(3)) : point.y_m,
    };
  };
  return {
    ...analysis,
    walls: (analysis.walls || []).map((wall) => ({
      ...wall,
      start: overlayPoint(wall.start || wall.a),
      end: overlayPoint(wall.end || wall.b),
    })),
    objects: (analysis.objects || []).map((item) => ({
      ...item,
      center: overlayPoint(item.center || item.centroid),
      track: (item.track || item.history || []).map(overlayPoint),
    })),
    orchard: analysis.orchard ? {
      ...analysis.orchard,
      trees: (analysis.orchard.trees || []).map((tree) => ({
        ...tree,
        center: overlayPoint(tree.center || tree),
      })),
    } : analysis.orchard,
    trees: (analysis.trees || []).map((tree) => ({
      ...tree,
      center: tree.center ? overlayPoint(tree.center) : undefined,
      x_m: Number.isFinite(Number(tree.x_m)) ? Number(Number(tree.x_m).toFixed(3)) : tree.x_m,
      y_m: Number.isFinite(Number(tree.y_m)) ? Number((-Number(tree.y_m)).toFixed(3)) : tree.y_m,
    })),
  };
}

function normalizeLidarResult(result, analysis = result?.analysis) {
  if (!result) return null;
  return {
    ...result,
    points: (result.points || []).map(normalizeLidarExportPoint).filter(Boolean),
    analysis: normalizeLidarOverlay(analysis),
  };
}

function normalizeArchiveLidarResult(result, analysis = result?.analysis, frame = result) {
  if (!result) return null;
  const coordinateFrame = frame?.coordinate_frame || result.coordinate_frame || analysis?.coordinate_frame || state.recordings.timeline?.coordinate_frame || "";
  const legacyRecorderFrame = coordinateFrame !== LIDAR_COORDINATE_FRAME;
  const points = (result.points || []).map((point) => legacyRecorderFrame ? archiveLegacyPointToBridgeFrame(point) : point);
  return {
    ...result,
    coordinate_frame: coordinateFrame || "legacy_archive_recorder_xy_v0",
    points: points.map(normalizeLidarExportPoint).filter(Boolean),
    analysis: normalizeLidarOverlay(legacyRecorderFrame ? transformArchiveLegacyOverlay(analysis) : analysis),
  };
}

function archiveLegacyPointToBridgeFrame(point) {
  if (!point) return point;
  const xM = Number(point.x_m);
  const yM = Number(point.y_m);
  if (!Number.isFinite(xM) || !Number.isFinite(yM)) return point;
  return {
    ...point,
    x_m: yM,
    y_m: xM,
  };
}

function transformArchiveLegacyOverlay(analysis) {
  if (!analysis) return analysis;
  const overlayPoint = (point) => archiveLegacyPointToBridgeFrame(point || {});
  return {
    ...analysis,
    coordinate_frame: LIDAR_COORDINATE_FRAME,
    walls: (analysis.walls || []).map((wall) => ({
      ...wall,
      start: overlayPoint(wall.start || wall.a),
      end: overlayPoint(wall.end || wall.b),
    })),
    objects: (analysis.objects || []).map((item) => ({
      ...item,
      center: overlayPoint(item.center || item.centroid),
      track: (item.track || item.history || []).map(overlayPoint),
    })),
    orchard: analysis.orchard ? {
      ...analysis.orchard,
      trees: (analysis.orchard.trees || []).map((tree) => ({
        ...tree,
        center: overlayPoint(tree.center || tree),
      })),
    } : analysis.orchard,
    trees: (analysis.trees || []).map((tree) => ({
      ...tree,
      center: tree.center ? overlayPoint(tree.center) : undefined,
      x_m: Number.isFinite(Number(tree.x_m)) ? overlayPoint(tree).x_m : tree.x_m,
      y_m: Number.isFinite(Number(tree.y_m)) ? overlayPoint(tree).y_m : tree.y_m,
    })),
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
  const camera = connection.camera || {};
  const bridge = connection.lidar_bridge || {};
  const cameraBridge = connection.camera_bridge || {};
  const ws = connection.lidar_websocket || {};
  const cameraWs = connection.camera_websocket || {};
  const last = lidar.last_test || {};
  const lastScan = lidar.last_scan || {};
  const scanLive = Boolean(state.lidarLive.scanTopicAlive || bridge.scan_topic_alive);
  const cameraLive = Boolean(state.cameraLive.frameTopicAlive || cameraBridge.frame_topic_alive);
  const wsText = `WS ${ws.active || 0}`;
  const cameraWsText = `WS ${cameraWs.active || 0}`;
  applyCameraModeCatalog(camera.supported_view_modes, camera.supported_view_modes_source || "fallback");
  applyCameraStatus(camera, cameraBridge);
  text("connectionLidarState", scanLive ? "수신 중" : (lidar.present ? lidarStateLabel(lidar.state) : "미인식"));
  text("connectionLidarDevice", lidar.device || "--");
  text("connectionLidarProtocol", "ROS2 /scan");
  text("connectionLidarSubtext", "subscribe-only");
  text("connectionLidarHealth", scanLive ? "수신 중" : (lidar.present ? "ROS2 대기" : "--"));
  text("connectionLidarHealthText", scanLive ? `ROS2 /scan live · ${wsText}` : `ROS2 /scan 대기 · ${wsText}`);
  text("connectionCameraState", cameraLive ? "수신 중" : cameraStateLabel(camera));
  text("connectionCameraDevice", cameraLive ? `${cameraResolutionLabel(cameraBridge)} · ${cameraWsText}` : cameraDeviceLabel(camera));
  text("lidarDeviceDetail", lidar.present ? shortPath(lidar.device) : "장치 미인식");
  text("lidarByIdDetail", lidar.by_id ? shortPath(lidar.by_id, 42) : "stable ID 대기");
  text("lidarDriverDetail", driverLabel(lidar.driver_hint));
  text("lidarLastTestDetail", lidarLastTestLabel(last));
  text("lidarPolicyDetail", policyLabel(lidar.conflict_policy));
  renderStatusRows("connectionServices", serviceRows(connection.services || {}));
  renderStatusRows("connectionSensors", sensorRows(connection.sensors || {}));
  renderLidarLiveState();
  renderCameraLiveState();
  if (last.ok || last.message) renderLidarTest(last);
  if (lastScan.ok || lastScan.message) {
    if (!state.lidarScan || (lastScan.tested_at || 0) >= (state.lidarScan.tested_at || 0)) {
      state.lidarScan = lastScan;
      renderLidarScan(lastScan, "ros2_scan");
    }
  }
}

function cameraResolutionLabel(cameraBridge) {
  const width = cameraBridge?.source_width || state.cameraLive.sourceWidth;
  const height = cameraBridge?.source_height || state.cameraLive.sourceHeight;
  return width && height ? `${width}x${height}` : "ROS2 /camera";
}

function cameraStateLabel(camera) {
  if (camera.present) return lidarStateLabel(camera.state);
  if (camera.usb_present || camera.state === "usb_detected_video_node_missing") return "USB 감지";
  return "미인식";
}

function cameraDeviceLabel(camera) {
  if (camera.device) return camera.device;
  if (camera.usb_present || camera.state === "usb_detected_video_node_missing") {
    const devices = Array.isArray(camera.usb_devices) ? camera.usb_devices : [];
    const first = devices[0] || {};
    const product = [first.manufacturer, first.product].filter(Boolean).join(" ");
    return product ? `${product} · 비디오 노드 대기` : "USB camera · 비디오 노드 대기";
  }
  return "--";
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
	  const analysis = result.analysis || {};
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
	    { label: "Overlay", value: `${analysis.walls?.length || 0}/${analysis.objects?.length || 0}`, sub: "walls / objects", color: colors.amber },
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
    waiting_for_frame: "프레임 대기",
    waiting_for_open: "장치 열기 대기",
    video_node_missing: "비디오 노드 대기",
    usb_detected_video_node_missing: "비디오 노드 대기",
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
      lidarStateLabel(item.state),
    ].filter(Boolean).join(" · "),
    ok: Boolean(item.ok || (key === "lidar" && lidarBridgeLive()) || (key === "camera" && cameraBridgeLive())),
    level: sensorLevel(key, item),
    value: sensorValue(key, item),
  }));
}

function sensorLevel(key, item) {
  if (key === "lidar" && lidarBridgeLive()) return "ok";
  if (key === "camera" && cameraBridgeLive()) return "ok";
  if (item.ok) return "ok";
  if (item.severity === "error" || item.state === "video_node_missing") return "bad";
  if (item.severity === "waiting" || item.state === "waiting_for_ros2") return "warn";
  if ((key === "lidar" || key === "camera") && item.dashboard_access === "subscribe_only") return "warn";
  return "bad";
}

function sensorValue(key, item) {
  if (key === "lidar" && lidarBridgeLive()) return `${lidarSourceLabel(item.source || state.lidarLive.source)} live`;
  if (key === "camera" && cameraBridgeLive()) return `${cameraResolutionLabel(item)} live`;
  if (item.rate_hz) return `${Number(item.rate_hz).toFixed(1)} Hz`;
  if (key === "lidar" && item.dashboard_access === "subscribe_only") return "ROS2 대기";
  if (key === "camera" && item.state === "video_node_missing") return "비디오 노드 대기";
  if (key === "camera" && item.dashboard_access === "subscribe_only") return "ROS2 대기";
  return "연결 전";
}

function lidarBridgeLive() {
  return Boolean(state.lidarLive.scanTopicAlive || state.telemetry?.connection?.lidar_bridge?.scan_topic_alive);
}

function cameraBridgeLive() {
  return Boolean(state.cameraLive.frameTopicAlive || state.telemetry?.connection?.camera_bridge?.frame_topic_alive);
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
  text("diskSummary", primary ? `${primary.mount} · ${fmtBytes(primary.used)} / ${fmtBytes(primary.total)} · ${fmtPercent(primary.percent)}` : "--");
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
  const source = [disk.source, disk.fstype].filter(Boolean).join(" · ");
  el.querySelector('[data-role="usage"]').textContent = `${source ? `${source} · ` : ""}${fmtBytes(disk.used)} used · ${fmtBytes(disk.free)} free · ${fmtBytes(disk.total)} total`;
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
  renderContainerActions(null);
  if (detail) detail.innerHTML = `<div class="empty-state">컨테이너 정보를 불러오는 중입니다.</div>`;
  if (logs) logs.textContent = "로그를 불러오는 중입니다.";
}

function renderContainerDetailError(message) {
  text("containerDetailTitle", "컨테이너 상세");
  const detail = $("containerDetail");
  const logs = $("containerLogs");
  renderContainerActions(null);
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
  renderContainerActions(detailPayload);
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

function renderContainerActions(detailPayload) {
  const target = $("containerActions");
  if (!target) return;
  if (!detailPayload?.ok) {
    target.innerHTML = "";
    return;
  }
  if (detailPayload.self) {
    target.innerHTML = `<span class="container-self-lock">Dashboard self</span>`;
    return;
  }
  const running = Boolean(detailPayload.state?.Running);
  const id = escapeHtml(detailPayload.id || detailPayload.full_id || "");
  target.innerHTML = `
    <button type="button" data-container-action="start" data-container-id="${id}" ${running ? "disabled" : ""}>시작</button>
    <button type="button" data-container-action="stop" data-container-id="${id}" ${running ? "" : "disabled"}>중지</button>
    <button type="button" data-container-action="restart" data-container-id="${id}" ${running ? "" : "disabled"}>재시작</button>
  `;
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
  $("containerActions")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-container-action]");
    if (!button || button.disabled) return;
    await postContainerAction(button.dataset.containerId, button.dataset.containerAction);
  });
}

async function postContainerAction(containerId, action) {
  if (!containerId || !action) return;
  const target = $("containerActions");
  if (target) target.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  debugLog("docker", `POST ${action}`, { containerId });
  try {
    const res = await fetch(`/api/docker/container/${encodeURIComponent(containerId)}/${encodeURIComponent(action)}`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    debugLog("docker", `${action} result`, payload);
    state.containerDetail = null;
    state.containerLogs = null;
    state.containerLogsUpdatedAt = null;
    await tick();
    await loadSelectedContainer(true);
  } catch (err) {
    debugLog("docker", `${action} failed`, err);
    renderContainerDetailError(`컨테이너 ${action} 실패: ${err.message}`);
  }
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
    if (line.trim() === "$$") {
      const formula = [];
      i += 1;
      while (i < lines.length && lines[i].trim() !== "$$") {
        formula.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      html.push(`<div class="math-block">${escapeHtml(formula.join("\n"))}</div>`);
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
    || line.trim() === "$$"
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
  return {
    dashboard: "Dashboard",
    cloudflared: "Cloudflare",
    ros2_bridge: "ROS2 Bridge",
    lidar_bridge: "LiDAR Bridge",
    camera_bridge: "Camera Bridge",
    rover_bridge: "Rover Bridge",
    data_recorder: "Data Recorder",
  }[key] || key;
}

function renderRoverControl(data) {
  const rover = data.rover || data.connection?.rover || {};
  const services = data.services || data.connection?.services || {};
  const roverBridge = services.rover_bridge || {};
  if (services.data_recorder?.state || services.data_recorder?.active) {
    state.recordings.status = { ...(state.recordings.status || {}), ...services.data_recorder, ok: services.data_recorder.ok ?? state.recordings.status?.ok };
  }
  const recorder = state.recordings.status || services.data_recorder || {};
  text("roverControlState", roverBridge.serial_open ? "open" : roverBridge.state || "대기");
  text("roverControlBase", roverBridge.serial_port || "--");
  text("roverProxyState", rover.mode || roverBridge.mode || "serial_only");
  text("roverRosState", roverBridge.ok ? "ready" : roverBridge.state || "대기");
  text("roverRecorderState", recorder.active ? "recording" : recorder.ok ? "ready" : recorder.state || "대기");
  text("roverRecorderKind", recorder.active ? recordingKindLabel(recorder.kind) : recorder.archive_count !== undefined ? `${recorder.archive_count} archives` : "대기");
  renderStatusRows("roverControlDetails", [
    {
      title: "UART ROS2 Bridge",
      sub: "ROS2 /cmd_vel -> WAVE ROVER JSON",
      ok: Boolean(roverBridge.ok),
      level: roverBridge.ok ? "ok" : "warn",
      value: roverBridge.state || "대기",
    },
    {
      title: "Jetson UART",
      sub: roverBridge.serial_port || "/dev/ttyTHS1",
      ok: Boolean(roverBridge.serial_open),
      level: roverBridge.serial_open ? "ok" : "warn",
      value: roverBridge.serial_open ? "열림" : "포트 대기",
    },
    {
      title: "Network Control",
      sub: "WAVE ROVER AP/Web UI",
      ok: rover.mode === "serial_only" || rover.state === "network_disabled",
      level: "ok",
      value: "비활성화",
    },
    {
      title: "Command File",
      sub: roverBridge.command_file || "/app/runtime/rover_command.json",
      ok: true,
      level: "ok",
      value: roverBridge.file_command_active ? "active" : "TTL 대기",
    },
    {
      title: "Data Recorder",
      sub: recorder.recording_id || recorder.status_url || "ros2-data-recorder",
      ok: Boolean(recorder.ok),
      level: recorder.ok ? "ok" : "warn",
      value: recorder.active ? `${recordingKindLabel(recorder.kind)} 기록 중` : recorder.state || "대기",
    },
  ]);
  renderRoverMotionReadout();
  renderRecordingStatus();
}

function initRoverControl() {
  try {
    const rawSpeed = localStorage.getItem(ROVER_SPEED_KEY);
    const stored = rawSpeed === null ? null : Number(rawSpeed);
    if (Number.isFinite(stored)) state.roverControl.speedPct = Math.max(0, Math.min(100, stored));
    const fadeStart = localStorage.getItem(ROVER_FADE_START_KEY);
    const fadeStop = localStorage.getItem(ROVER_FADE_STOP_KEY);
    const recordKind = localStorage.getItem(ROVER_RECORD_KIND_KEY);
    const panelCollapsed = localStorage.getItem(ROVER_PANEL_COLLAPSED_KEY);
    const archiveRange = Number(localStorage.getItem(ARCHIVE_RANGE_KEY));
    const archiveOverlayVisible = localStorage.getItem(ARCHIVE_OVERLAY_VISIBLE_KEY);
    const perceptionOverlay = localStorage.getItem(PERCEPTION_OVERLAY_KEY);
    state.roverControl.fadeStart = fadeStart === null ? true : fadeStart === "1";
    state.roverControl.fadeStop = fadeStop === "1";
    state.roverControl.panelCollapsed = panelCollapsed === "1";
    if (Number.isFinite(archiveRange)) state.archivePlayback.range = clampLidarRange(archiveRange);
    state.archivePlayback.overlayVisible = archiveOverlayVisible === null ? true : archiveOverlayVisible === "1";
    if (["lidar", "camera", "fusion"].includes(perceptionOverlay)) state.perceptionOverlayMode = perceptionOverlay;
    if (["lidar", "camera", "both"].includes(recordKind)) state.recordings.kind = recordKind;
  } catch (_) {
    // Speed defaults remain usable without localStorage.
  }
  $("roverSpeedSlider")?.addEventListener("input", (event) => setRoverSpeed(Number(event.target.value), true));
  $("roverStopButton")?.addEventListener("click", () => stopRoverNow("operator_stop"));
  $("roverDrivePanelToggle")?.addEventListener("click", () => setRoverDrivePanelCollapsed(!state.roverControl.panelCollapsed, true));
  $("roverCameraToggle")?.addEventListener("click", toggleCameraLive);
  $("roverLidarToggle")?.addEventListener("click", toggleLidarLive);
  $("roverRecordStartButton")?.addEventListener("click", () => startRecording(state.recordings.kind));
  document.querySelectorAll("[data-record-kind]").forEach((button) => {
    button.addEventListener("click", () => setRecordingKind(button.dataset.recordKind, true));
  });
  $("roverRecordStopButton")?.addEventListener("click", stopRecording);
  $("roverArchiveRefreshButton")?.addEventListener("click", () => loadRecordings().catch(console.error));
  $("roverFadeStartToggle")?.addEventListener("change", (event) => setRoverFadeOption("start", event.target.checked, true));
  $("roverFadeStopToggle")?.addEventListener("change", (event) => setRoverFadeOption("stop", event.target.checked, true));
  $("roverArchivePlayButton")?.addEventListener("click", toggleArchivePlayback);
  $("roverArchiveScrubber")?.addEventListener("input", (event) => setArchiveTime(Number(event.target.value), { pause: true }));
  $("roverOrchardMapScrubber")?.addEventListener("input", (event) => setArchiveTime(Number(event.target.value), { pause: true }));
  $("roverArchiveSyncToggle")?.addEventListener("change", (event) => {
    state.archivePlayback.sync = Boolean(event.target.checked);
    state.archivePlayback.lastCameraRenderKey = "";
    renderArchiveFrame();
  });
  $("roverArchiveOverlayToggle")?.addEventListener("change", (event) => setArchiveOverlayVisible(event.target.checked, true));
  $("roverArchiveRangeSlider")?.addEventListener("input", (event) => setArchiveRange(Number(event.target.value), true));
  $("roverOrchardMapZoomIn")?.addEventListener("click", () => zoomOrchardMap(1.18));
  $("roverOrchardMapZoomOut")?.addEventListener("click", () => zoomOrchardMap(1 / 1.18));
  $("roverOrchardMapReset")?.addEventListener("click", () => {
    resetOrchardMapView();
    drawOrchardMap();
  });
  $("roverOrchardMapDownload")?.addEventListener("click", downloadOrchardMapPng);
  initOrchardMapCanvas();
  document.querySelectorAll("[data-archive-overlay-mode]").forEach((button) => {
    button.addEventListener("click", () => setArchiveOverlayMode(button.dataset.archiveOverlayMode, true));
  });
  document.querySelectorAll("[data-perception-overlay]").forEach((button) => {
    button.addEventListener("click", () => setPerceptionOverlayMode(button.dataset.perceptionOverlay, true));
  });
  document.querySelectorAll("[data-rover-drive]").forEach((button) => {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const action = button.dataset.roverDrive || "stop";
      if (action === "stop") stopRoverNow("pad_stop");
      else startRoverButtonCommand(action);
    });
    button.addEventListener("pointerup", () => releaseRoverInput("pad_release"));
    button.addEventListener("pointercancel", () => releaseRoverInput("pad_cancel"));
    button.addEventListener("pointerleave", () => {
      if (state.roverControl.activeButton) releaseRoverInput("pad_leave");
    });
  });
  document.addEventListener("keydown", handleRoverKeyDown);
  document.addEventListener("keyup", handleRoverKeyUp);
  setRoverSpeed(state.roverControl.speedPct, false);
  setRoverFadeOption("start", state.roverControl.fadeStart, false);
  setRoverFadeOption("stop", state.roverControl.fadeStop, false);
  setRoverDrivePanelCollapsed(state.roverControl.panelCollapsed, false);
  setRecordingKind(state.recordings.kind, false);
  setArchiveOverlayVisible(state.archivePlayback.overlayVisible, false);
  setArchiveRange(state.archivePlayback.range, false);
  initArchiveOverlayMode();
  setPerceptionOverlayMode(state.perceptionOverlayMode, false);
  renderRoverButtons();
  renderRecordingStatus();
  renderRecordings();
}

function setRoverSpeed(value, persist) {
  state.roverControl.speedPct = Math.max(0, Math.min(100, Number(value) || 0));
  text("roverSpeedValue", `${Math.round(state.roverControl.speedPct)}%`);
  const slider = $("roverSpeedSlider");
  if (slider && document.activeElement !== slider) slider.value = String(Math.round(state.roverControl.speedPct));
  if (persist) {
    try {
      localStorage.setItem(ROVER_SPEED_KEY, String(Math.round(state.roverControl.speedPct)));
    } catch (_) {
      // Keep speed session-local when storage is unavailable.
    }
  }
  if (hasActiveRoverInput()) updateRoverMotion();
}

function setRoverFadeOption(which, enabled, persist) {
  const value = Boolean(enabled);
  if (which === "stop") {
    state.roverControl.fadeStop = value;
    const toggle = $("roverFadeStopToggle");
    if (toggle) toggle.checked = value;
  } else {
    state.roverControl.fadeStart = value;
    const toggle = $("roverFadeStartToggle");
    if (toggle) toggle.checked = value;
  }
  if (persist) {
    try {
      localStorage.setItem(which === "stop" ? ROVER_FADE_STOP_KEY : ROVER_FADE_START_KEY, value ? "1" : "0");
    } catch (_) {
      // Option remains session-local without storage.
    }
  }
  renderRoverMotionReadout();
}

function setRoverDrivePanelCollapsed(collapsed, persist) {
  state.roverControl.panelCollapsed = Boolean(collapsed);
  document.querySelector(".rover-layout")?.classList.toggle("drive-collapsed", state.roverControl.panelCollapsed);
  const button = $("roverDrivePanelToggle");
  if (button) {
    button.textContent = state.roverControl.panelCollapsed ? "조종 패널 보이기" : "조종 패널 숨기기";
    button.classList.toggle("active", state.roverControl.panelCollapsed);
  }
  if (persist) {
    try {
      localStorage.setItem(ROVER_PANEL_COLLAPSED_KEY, state.roverControl.panelCollapsed ? "1" : "0");
    } catch (_) {
      // Keep panel visibility session-local without storage.
    }
  }
  const motionActive = hasActiveRoverInput()
    || state.roverControl.stoppingWithFade
    || Math.abs(state.roverControl.currentLinear) > 0.001
    || Math.abs(state.roverControl.currentAngular) > 0.001;
  if (persist && state.roverControl.panelCollapsed && motionActive) {
    stopRoverNow("drive_panel_hidden");
  }
}

function setRecordingKind(kind, persist) {
  state.recordings.kind = ["lidar", "camera", "both"].includes(kind) ? kind : "both";
  if (persist) {
    try {
      localStorage.setItem(ROVER_RECORD_KIND_KEY, state.recordings.kind);
    } catch (_) {
      // Kind selection remains session-local without storage.
    }
  }
  document.querySelectorAll("[data-record-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.recordKind === state.recordings.kind);
  });
  renderRecordingStatus();
}

function initArchiveOverlayMode() {
  try {
    state.archivePlayback.overlayMode = localStorage.getItem(ARCHIVE_OVERLAY_MODE_KEY) || state.archivePlayback.overlayMode;
  } catch (_) {
    // Archive overlay mode remains session-local without storage.
  }
  setArchiveOverlayMode(state.archivePlayback.overlayMode, false);
}

function setArchiveOverlayMode(mode, persist) {
  state.archivePlayback.overlayMode = ["raw", "both", "overlay"].includes(mode) ? mode : "both";
  if (persist) {
    try {
      localStorage.setItem(ARCHIVE_OVERLAY_MODE_KEY, state.archivePlayback.overlayMode);
    } catch (_) {
      // Ignore storage failures.
    }
  }
  document.querySelectorAll("[data-archive-overlay-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.archiveOverlayMode === state.archivePlayback.overlayMode);
  });
  state.archivePlayback.lastLidarRenderKey = "";
  renderArchiveFrame();
}

function setPerceptionOverlayMode(mode, persist) {
  state.perceptionOverlayMode = ["lidar", "camera", "fusion"].includes(mode) ? mode : "fusion";
  if (persist) {
    try {
      localStorage.setItem(PERCEPTION_OVERLAY_KEY, state.perceptionOverlayMode);
    } catch (_) {
      // Ignore storage failures.
    }
  }
  document.querySelectorAll("[data-perception-overlay]").forEach((button) => {
    button.classList.toggle("active", button.dataset.perceptionOverlay === state.perceptionOverlayMode);
  });
  state.archivePlayback.lastLidarRenderKey = "";
  state.archivePlayback.lastCameraOverlayRenderKey = "";
  renderActiveLidarMap();
  renderLiveCameraOverlays();
  renderArchiveFrame();
  drawOrchardMap();
}

function setArchiveOverlayVisible(visible, persist) {
  state.archivePlayback.overlayVisible = Boolean(visible);
  const toggle = $("roverArchiveOverlayToggle");
  if (toggle) toggle.checked = state.archivePlayback.overlayVisible;
  if (persist) {
    try {
      localStorage.setItem(ARCHIVE_OVERLAY_VISIBLE_KEY, state.archivePlayback.overlayVisible ? "1" : "0");
    } catch (_) {
      // Ignore storage failures.
    }
  }
  state.archivePlayback.lastLidarRenderKey = "";
  state.archivePlayback.lastCameraOverlayRenderKey = "";
  renderArchiveFrame();
}

function setArchiveRange(value, persist) {
  state.archivePlayback.range = clampLidarRange(value);
  const slider = $("roverArchiveRangeSlider");
  if (slider && document.activeElement !== slider) slider.value = String(state.archivePlayback.range);
  text("roverArchiveRangeValue", `${fmtNumber(state.archivePlayback.range, 1)}m`);
  if (persist) {
    try {
      localStorage.setItem(ARCHIVE_RANGE_KEY, String(state.archivePlayback.range));
    } catch (_) {
      // Ignore storage failures.
    }
  }
  state.archivePlayback.lastLidarRenderKey = "";
  renderArchiveFrame();
}

function archiveOverlayRenderMode() {
  return state.archivePlayback.overlayVisible ? state.archivePlayback.overlayMode : "raw";
}

function roverKeyFromEvent(event) {
  const key = String(event.key || "").toLowerCase();
  return ["w", "a", "s", "d"].includes(key) ? key : "";
}

function roverKeyTargetBlocked(event) {
  const tag = String(event.target?.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || event.target?.isContentEditable;
}

function handleRoverKeyDown(event) {
  if (currentRoute() !== "/rover-control" || roverKeyTargetBlocked(event)) return;
  if (state.roverControl.panelCollapsed) return;
  const key = roverKeyFromEvent(event);
  if (!key) return;
  event.preventDefault();
  state.roverControl.activeKeys.add(key);
  state.roverControl.activeButton = "";
  ensureRoverCommandTimer();
  updateRoverMotion();
}

function handleRoverKeyUp(event) {
  const key = roverKeyFromEvent(event);
  if (!key) return;
  state.roverControl.activeKeys.delete(key);
  if (currentRoute() === "/rover-control") {
    event.preventDefault();
	    if (hasActiveRoverInput()) updateRoverMotion();
	    else releaseRoverInput("key_release");
	  }
	}

function startRoverButtonCommand(action) {
  state.roverControl.activeButton = action;
  state.roverControl.activeKeys.clear();
  state.roverControl.stoppingWithFade = false;
  ensureRoverCommandTimer();
  updateRoverMotion();
}

function hasActiveRoverInput() {
  return Boolean(state.roverControl.activeButton || state.roverControl.activeKeys.size);
}

function roverMotionVector() {
  const keys = state.roverControl.activeKeys;
  let linear = 0;
  let angular = 0;
  if (keys.size) {
    linear += keys.has("w") ? 1 : 0;
    linear -= keys.has("s") ? 1 : 0;
    angular += keys.has("a") ? 1 : 0;
    angular -= keys.has("d") ? 1 : 0;
  } else {
    const action = state.roverControl.activeButton;
    linear = action === "forward" ? 1 : action === "backward" ? -1 : 0;
    angular = action === "left" ? 1 : action === "right" ? -1 : 0;
  }
  const speed = state.roverControl.speedPct / 100;
  return {
    linear: Number((linear * ROVER_MAX_LINEAR_MPS * speed).toFixed(3)),
    angular: Number((angular * ROVER_MAX_ANGULAR_RPS * speed).toFixed(3)),
  };
}

function ensureRoverCommandTimer() {
  if (state.roverControl.commandTimer) return;
  state.roverControl.commandTimer = window.setInterval(() => {
    if (currentRoute() !== "/rover-control") {
      clearRoverCommandTimer();
      return;
    }
    if (!hasActiveRoverInput() && !state.roverControl.stoppingWithFade) {
      clearRoverCommandTimer();
      return;
    }
    stepRoverMotion();
  }, ROVER_CONTROL_INTERVAL_MS);
}

function clearRoverCommandTimer() {
  if (!state.roverControl.commandTimer) return;
  window.clearInterval(state.roverControl.commandTimer);
  state.roverControl.commandTimer = null;
}

function updateRoverMotion() {
  const motion = roverMotionVector();
  state.roverControl.targetLinear = motion.linear;
  state.roverControl.targetAngular = motion.angular;
  state.roverControl.stoppingWithFade = false;
  renderRoverButtons();
  stepRoverMotion();
}

function stepRoverMotion() {
  const control = state.roverControl;
  if (hasActiveRoverInput()) {
    const motion = roverMotionVector();
    control.targetLinear = motion.linear;
    control.targetAngular = motion.angular;
  } else {
    control.targetLinear = 0;
    control.targetAngular = 0;
  }
  const targetLinear = control.targetLinear;
  const targetAngular = control.targetAngular;
  const accelerating = Math.abs(targetLinear) > Math.abs(control.currentLinear) || Math.abs(targetAngular) > Math.abs(control.currentAngular);
  const useFade = accelerating ? control.fadeStart : control.fadeStop;
  if (useFade) {
    control.currentLinear = fadeAxis(control.currentLinear, targetLinear, ROVER_MAX_LINEAR_MPS * 0.24);
    control.currentAngular = fadeAxis(control.currentAngular, targetAngular, ROVER_MAX_ANGULAR_RPS * 0.24);
  } else {
    control.currentLinear = targetLinear;
    control.currentAngular = targetAngular;
  }
  if (!hasActiveRoverInput() && Math.abs(control.currentLinear) < 0.005 && Math.abs(control.currentAngular) < 0.005) {
    control.currentLinear = 0;
    control.currentAngular = 0;
    control.stoppingWithFade = false;
    clearRoverCommandTimer();
  }
  control.linear = Number(control.currentLinear.toFixed(3));
  control.angular = Number(control.currentAngular.toFixed(3));
  renderRoverButtons();
  renderRoverMotionReadout();
  sendRoverCommand(control.linear, control.angular, hasActiveRoverInput() ? "operator_drive" : "operator_fade_stop").catch((err) => {
    state.roverControl.lastError = err.message;
    setRoverCommandStatus("bad", `명령 실패: ${err.message}`);
  });
}

function fadeAxis(current, target, step) {
  const diff = target - current;
  if (Math.abs(diff) <= step) return target;
  return current + Math.sign(diff) * step;
}

function releaseRoverInput(reason = "release") {
  state.roverControl.activeButton = "";
  state.roverControl.activeKeys.clear();
  if (state.roverControl.fadeStop) {
    state.roverControl.stoppingWithFade = true;
    ensureRoverCommandTimer();
    stepRoverMotion();
    setRoverCommandStatus("testing", "정지 페이드 적용 중");
    return;
  }
  stopRoverNow(reason);
}

async function sendRoverCommand(linear, angular, source) {
  if (state.roverControl.inFlight) return;
  state.roverControl.inFlight = true;
  try {
    const payload = await postJson("/api/rover/command", {
      linear,
      angular,
      ttl_s: 0.65,
      speed_pct: Math.round(state.roverControl.speedPct),
      source,
    });
    state.roverControl.lastCommandAt = Date.now();
    state.roverControl.lastError = payload.ok ? "" : payload.error || "command rejected";
    setRoverCommandStatus(payload.ok ? "ok" : "bad", roverCommandMessage(payload));
  } finally {
    state.roverControl.inFlight = false;
  }
}

async function stopRoverNow(reason = "operator_stop") {
  state.roverControl.activeButton = "";
  state.roverControl.activeKeys.clear();
  state.roverControl.linear = 0;
  state.roverControl.angular = 0;
  state.roverControl.targetLinear = 0;
  state.roverControl.targetAngular = 0;
  state.roverControl.currentLinear = 0;
  state.roverControl.currentAngular = 0;
  state.roverControl.stoppingWithFade = false;
  clearRoverCommandTimer();
  renderRoverButtons();
  renderRoverMotionReadout();
  setRoverCommandStatus("testing", "정지 명령 전송 중");
  try {
    const payload = await postJson("/api/rover/stop", { source: reason });
    setRoverCommandStatus(payload.ok ? "ok" : "bad", roverCommandMessage(payload));
  } catch (err) {
    setRoverCommandStatus("bad", `정지 실패: ${err.message}`);
  }
}

function roverCommandMessage(payload) {
  const command = payload.command || {};
  const linear = fmtNumber(command.linear || 0, 2);
  const angular = fmtNumber(command.angular || 0, 2);
  return payload.ok ? `serial bridge 대기 · linear ${linear} · angular ${angular}` : payload.error || payload.state || "명령 실패";
}

function setRoverCommandStatus(kind, message) {
  const target = $("roverCommandStatus");
  if (!target) return;
  target.className = `test-result ${kind}`;
  target.textContent = message;
}

function renderRoverButtons() {
  document.querySelectorAll("[data-rover-drive]").forEach((button) => {
    const action = button.dataset.roverDrive || "";
    const activeByKey = (action === "forward" && state.roverControl.activeKeys.has("w"))
      || (action === "backward" && state.roverControl.activeKeys.has("s"))
      || (action === "left" && state.roverControl.activeKeys.has("a"))
      || (action === "right" && state.roverControl.activeKeys.has("d"));
    button.classList.toggle("active", Boolean(action && (action === state.roverControl.activeButton || activeByKey)));
  });
}

function renderRoverMotionReadout() {
  const colors = chartColors();
  renderReadout("roverMotionReadout", [
    {
      label: "Current",
      value: `${fmtNumber(state.roverControl.linear, 2)} m/s`,
      sub: `${fmtNumber(state.roverControl.angular, 2)} rad/s`,
      color: colors.cyan,
    },
    {
      label: "Target",
      value: `${fmtNumber(state.roverControl.targetLinear, 2)} m/s`,
      sub: `${fmtNumber(state.roverControl.targetAngular, 2)} rad/s`,
      color: colors.blue,
    },
    {
      label: "Fade",
      value: `${state.roverControl.fadeStart ? "start" : "snap"} / ${state.roverControl.fadeStop ? "stop" : "zero"}`,
      sub: `${Math.round(state.roverControl.speedPct)}% speed cap`,
      color: colors.good,
    },
  ]);
}

async function refreshRecordingStatus() {
  try {
    const status = await getJson("/api/recordings/status");
    state.recordings.status = status;
    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
  } catch (err) {
    state.recordings.status = { ok: false, state: "unavailable", error: err.message };
    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
  }
}

function maybeRefreshRecordingStatus() {
  if (currentRoute() !== "/rover-control") return;
  const now = Date.now();
  if (now - (state.recordings.lastStatusAt || 0) < 2200) return;
  refreshRecordingStatus().catch((err) => debugLog("recording", "status refresh failed", err));
}

async function startRecording(kind) {
  if (state.recordings.actionInFlight) return;
  state.recordings.actionInFlight = true;
  renderRecordingStatus("기록 시작 중");
  try {
	    const payload = await postJson("/api/recordings/start", { kind: kind || state.recordings.kind });
	    state.recordings.status = payload.status || payload;
	    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
    await loadRecordings();
  } catch (err) {
    state.recordings.status = { ok: false, state: "start_failed", error: err.message };
    renderRecordingStatus();
  } finally {
    state.recordings.actionInFlight = false;
  }
}

async function stopRecording() {
  if (state.recordings.actionInFlight) return;
  state.recordings.actionInFlight = true;
  renderRecordingStatus("기록 중지 중");
  try {
    const payload = await postJson("/api/recordings/stop", {});
    state.recordings.status = payload.status || payload;
    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
    await loadRecordings();
  } catch (err) {
    state.recordings.status = { ok: false, state: "stop_failed", error: err.message };
    renderRecordingStatus();
  } finally {
    state.recordings.actionInFlight = false;
  }
}

function recordingKindLabel(kind) {
  return { lidar: "LiDAR", camera: "Camera", both: "LiDAR+Camera" }[kind] || kind || "대기";
}

function renderRecordingStatus(override) {
  const status = state.recordings.status || {};
  const active = Boolean(status.active);
  const kind = recordingKindLabel(active ? status.kind : state.recordings.kind);
  const duration = recordingDurationSeconds(status);
  const message = override || (active ? `${kind} 기록 중 · ${status.recording_id || ""}` : status.error || status.state || `${kind} 기록 대기`);
  const target = $("roverRecordingStatus");
  if (target) {
    target.className = `test-result ${active || status.ok ? "ok" : status.error ? "bad" : "testing"}`;
    target.textContent = message;
  }
  document.querySelectorAll("[data-record-kind]").forEach((button) => {
    button.disabled = state.recordings.actionInFlight || active;
    button.classList.toggle("active", (active ? status.kind : state.recordings.kind) === button.dataset.recordKind);
  });
  const start = $("roverRecordStartButton");
  if (start) {
    start.disabled = state.recordings.actionInFlight || active;
    start.textContent = active ? "기록 중" : `${recordingKindLabel(state.recordings.kind)} 기록 시작`;
  }
  const stop = $("roverRecordStopButton");
  if (stop) stop.disabled = state.recordings.actionInFlight || !active;
  text("roverRecordingTimer", active ? fmtClock(duration) : "00:00");
  text("roverRecorderState", active ? "recording" : status.ok ? "ready" : status.state || "--");
  text("roverRecorderKind", active ? kind : status.archive_count !== undefined ? `${status.archive_count} archives` : "대기");
}

function recordingDurationSeconds(status) {
  if (Number.isFinite(Number(status.duration_s))) return Number(status.duration_s);
  if (!status.started_at) return 0;
  const started = new Date(status.started_at).getTime();
  if (!Number.isFinite(started)) return 0;
  return Math.max(0, (Date.now() - started) / 1000);
}

async function loadRecordings() {
  state.recordings.loading = true;
  renderRecordings();
  await refreshRecordingStatus();
  try {
    const payload = await getJson("/api/recordings");
    state.recordings.items = payload.recordings || [];
    if (!state.recordings.selectedId && state.recordings.items.length) {
      state.recordings.selectedId = state.recordings.items[0].id;
    }
    renderRecordings();
    if (state.recordings.selectedId) await selectRecording(state.recordings.selectedId);
  } finally {
    state.recordings.loading = false;
    renderRecordings();
  }
}

async function selectRecording(id) {
  state.recordings.selectedId = id;
  stopArchivePlayback();
  state.recordings.timeline = null;
  state.recordings.orchardMap = null;
  clearArchiveFrameCache();
  drawOrchardMap();
  renderRecordings();
  try {
    const detail = await getJson(`/api/recordings/${encodeURIComponent(id)}`);
    state.recordings.selected = detail;
    state.recordings.timeline = timelineShellFromRecording(detail?.recording || detail);
    renderRecordingDetail(detail);
    if (state.recordings.timeline) {
      await ensureArchiveSegmentForTime(0, { force: true }).catch((err) => debugLog("archive", "first segment load failed", err));
      renderArchiveFrame();
      loadRecordingOrchardMap(id).catch((err) => debugLog("archive", "orchard map load failed", err));
    }
  } catch (err) {
    renderStatusRows("roverArchiveDetails", [
      { title: "Archive", sub: id, ok: false, level: "bad", value: err.message },
    ]);
  }
}

async function loadRecordingOrchardMap(id) {
  const payload = await getJson(`/api/recordings/${encodeURIComponent(id)}/orchard-map`);
  if (id !== state.recordings.selectedId) return;
  state.recordings.orchardMap = payload?.map || null;
  resetOrchardMapView();
  state.archivePlayback.lastLidarRenderKey = "";
  drawOrchardMap();
  renderArchiveFrame();
}

function timelineShellFromRecording(payload) {
  const summary = payload?.timeline_summary || {};
  const metadata = payload?.metadata || {};
  const durationS = Number(summary.duration_s || metadata.duration_s || payload?.duration_s || 0);
  const lidarFrames = Number(summary.lidar_frames || 0);
  const cameraFrames = Number(summary.camera_frames || 0);
  if (!summary.ok && !lidarFrames && !cameraFrames) return null;
  return {
    ok: true,
    id: payload?.id || metadata.id || state.recordings.selectedId,
    kind: payload?.kind || metadata.kind,
    algorithm: summary.algorithm,
    coordinate_frame: summary.coordinate_frame || payload?.preview_scan?.coordinate_frame || "",
    duration_s: durationS,
    lidar: { frames: [] },
    camera: { frames: [] },
    sync: {
      time_base: "seconds_since_recording_start",
      lidar_frames: lidarFrames,
      camera_frames: cameraFrames,
    },
    streaming: true,
  };
}

function clearArchiveFrameCache() {
  const cache = state.archivePlayback.cache;
  if (cache?.objectUrls) {
    for (const url of cache.objectUrls.values()) {
      if (url && url.startsWith("blob:")) URL.revokeObjectURL(url);
    }
  }
  state.archivePlayback.cache = {
    lidarFrames: [],
    cameraFrames: [],
    lidarKeys: new Set(),
    cameraKeys: new Set(),
    objectUrls: new Map(),
    imageElements: new Map(),
  };
  state.archivePlayback.segmentCache = new Map();
  state.archivePlayback.loadingSegments = new Map();
  state.archivePlayback.lastLidarRenderKey = "";
  state.archivePlayback.lastCameraRenderKey = "";
}

function prepareArchiveTimelineCache(timeline) {
  clearArchiveFrameCache();
  mergeArchiveTimelineSegment(timeline);
}

function mergeArchiveTimelineSegment(timeline) {
  if (!timeline) return;
  const cache = state.archivePlayback.cache;
  const lidarFrames = (timeline.lidar?.frames || []).map((frame, index) => ({
    ...frame,
    index,
    t_s: Number(frame.t_s) || 0,
    result: normalizeArchiveLidarResult({
      ok: true,
      state: "archive_timeline",
      frame_id: frame.frame_id || "laser",
      stamp: frame.stamp,
      points: frame.points || [],
      coordinate_frame: frame.coordinate_frame || timeline.coordinate_frame,
      metrics: frame.metrics || {},
      source: "archive_timeline",
    }, frame.overlay, frame),
  }));
  for (const frame of lidarFrames) {
    const key = archiveFrameKey(frame, "lidar");
    if (cache.lidarKeys.has(key)) continue;
    cache.lidarKeys.add(key);
    frame.cacheKey = key;
    cache.lidarFrames.push(frame);
  }
  cache.lidarFrames.sort((a, b) => a.t_s - b.t_s);
  cache.lidarFrames.forEach((frame, cacheIndex) => {
    frame.cacheIndex = cacheIndex;
  });
  const cameraFrames = (timeline.camera?.frames || []).map((frame, index) => ({
    ...frame,
    index,
    t_s: Number(frame.t_s) || 0,
  }));
  for (const frame of cameraFrames) {
    const key = archiveFrameKey(frame, "camera");
    if (cache.cameraKeys.has(key)) continue;
    cache.cameraKeys.add(key);
    frame.cacheKey = key;
    cache.cameraFrames.push(frame);
  }
  cache.cameraFrames.sort((a, b) => a.t_s - b.t_s);
  cache.cameraFrames.forEach((frame, cacheIndex) => {
    frame.cacheIndex = cacheIndex;
  });
  state.archivePlayback.lastLidarRenderKey = "";
  state.archivePlayback.lastCameraRenderKey = "";
}

function archiveFrameKey(frame, kind) {
  const stamp = frame?.stamp ? `${frame.stamp.sec || 0}.${frame.stamp.nanosec || 0}` : "";
  const size = kind === "camera" ? String(frame?.data || "").length : (frame?.points || []).length;
  return `${kind}:${Number(frame?.t_s || 0).toFixed(3)}:${stamp}:${size}`;
}

async function ensureArchiveSegmentForTime(timeS, options = {}) {
  const id = state.recordings.selectedId;
  const timeline = state.recordings.timeline;
  if (!id || !timeline) return;
  const segmentDuration = Math.max(1, Number(state.archivePlayback.segmentDurationS) || ARCHIVE_SEGMENT_SECONDS);
  const startS = Math.max(0, Math.floor((Number(timeS) || 0) / segmentDuration) * segmentDuration);
  await loadArchiveTimelineSegment(id, startS, segmentDuration, options);
  const nextStart = startS + segmentDuration;
  const nearSegmentEnd = Number(timeS) + 2 >= nextStart;
  if ((state.archivePlayback.playing || nearSegmentEnd) && nextStart < state.archivePlayback.durationS + 0.25) {
    loadArchiveTimelineSegment(id, nextStart, segmentDuration).catch((err) => debugLog("archive", "segment prefetch failed", err));
  }
  const secondNextStart = nextStart + segmentDuration;
  if (state.archivePlayback.playing && secondNextStart < state.archivePlayback.durationS + 0.25) {
    loadArchiveTimelineSegment(id, secondNextStart, segmentDuration).catch((err) => debugLog("archive", "segment prefetch failed", err));
  }
}

async function loadArchiveTimelineSegment(id, startS, durationS, options = {}) {
  const start = Math.max(0, Number(startS) || 0);
  const duration = Math.max(1, Number(durationS) || ARCHIVE_SEGMENT_SECONDS);
  const key = `${id}:${start.toFixed(3)}:${duration.toFixed(3)}`;
  if (!options.force && state.archivePlayback.segmentCache.has(key)) return state.archivePlayback.segmentCache.get(key);
  if (state.archivePlayback.loadingSegments.has(key)) return state.archivePlayback.loadingSegments.get(key);
  const params = new URLSearchParams({ start_s: start.toFixed(3), duration_s: duration.toFixed(3) });
  const promise = getJson(`/api/recordings/${encodeURIComponent(id)}/timeline?${params.toString()}`)
    .then((payload) => {
      if (id !== state.recordings.selectedId) return payload;
      if (payload?.ok && payload.timeline) {
        if (!state.recordings.timeline) state.recordings.timeline = payload.timeline;
        if (payload.timeline.coordinate_frame && !state.recordings.timeline.coordinate_frame) {
          state.recordings.timeline.coordinate_frame = payload.timeline.coordinate_frame;
        }
        mergeArchiveTimelineSegment(payload.timeline);
        state.archivePlayback.segmentCache.set(key, {
          start_s: start,
          duration_s: duration,
          lidar_frames: payload.timeline.lidar?.frames?.length || 0,
          camera_frames: payload.timeline.camera?.frames?.length || 0,
        });
        if (currentRoute() === "/rover-control") renderArchiveFrame();
      }
      return payload;
    })
    .finally(() => {
      state.archivePlayback.loadingSegments.delete(key);
    });
  state.archivePlayback.loadingSegments.set(key, promise);
  return promise;
}

function archiveCameraFrameSrc(frame) {
  if (!frame?.data) return "";
  const cache = state.archivePlayback.cache;
  const key = frame.cacheKey || `${frame.index}:${String(frame.data).length}`;
  if (cache.objectUrls.has(key)) return cache.objectUrls.get(key);
  const mime = frame.mime || "image/jpeg";
  try {
    const binary = atob(frame.data);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
    cache.objectUrls.set(key, url);
    const image = new Image();
    image.decoding = "async";
    image.src = url;
    cache.imageElements.set(key, image);
    return url;
  } catch (_) {
    const dataUrl = `data:${mime};base64,${frame.data}`;
    cache.objectUrls.set(key, dataUrl);
    return dataUrl;
  }
}

function warmArchiveCameraCache(frame) {
  if (!frame) return;
  const frames = state.archivePlayback.cache.cameraFrames;
  const center = Number(frame.cacheIndex);
  if (!Number.isFinite(center)) return;
  const start = Math.max(0, center - 3);
  const end = Math.min(frames.length - 1, center + 8);
  for (let index = start; index <= end; index += 1) archiveCameraFrameSrc(frames[index]);
}

function renderArchiveDownloadButton(payload, id) {
  const button = $("roverArchiveDownloadButton");
  const meta = $("roverArchiveDownloadMeta");
  if (!button) return;
  if (!id) {
    button.hidden = true;
    button.removeAttribute("href");
    button.removeAttribute("download");
    if (meta) meta.textContent = "tar.gz";
    return;
  }
  const href = payload?.download_url || `/api/recordings/${encodeURIComponent(id)}/download`;
  button.hidden = false;
  button.href = href;
  button.download = `${id}.tar.gz`;
  if (meta) meta.textContent = `${recordingKindLabel(payload?.kind || payload?.metadata?.kind)} · tar.gz`;
}

function renderRecordings() {
  const target = $("roverArchiveList");
  if (!target) return;
  target.innerHTML = "";
  if (state.recordings.loading && !state.recordings.items.length) {
    target.innerHTML = "<span>loading</span>";
    return;
  }
  if (!state.recordings.items.length) {
    target.innerHTML = "<span>archive empty</span>";
    renderRecordingDetail(null);
    return;
  }
  for (const item of state.recordings.items) {
    const button = document.createElement("button");
    button.type = "button";
    button.classList.toggle("active", item.id === state.recordings.selectedId);
    button.innerHTML = `
      <span>${escapeHtml(recordingKindLabel(item.kind))}</span>
      <strong>${escapeHtml(item.id)}</strong>
      <small>${escapeHtml(formatShortDate(item.started_at || item.created_at))} · ${escapeHtml(item.duration_s ? `${fmtNumber(item.duration_s, 1)}s` : item.state || "ready")} · L${escapeHtml(item.timeline?.lidar_frames ?? 0)} C${escapeHtml(item.timeline?.camera_frames ?? 0)}</small>
    `;
    button.addEventListener("click", () => selectRecording(item.id));
    target.append(button);
  }
}

function renderRecordingDetail(detail) {
  const payload = detail?.recording || detail;
  const metadata = payload?.metadata || {};
  const analysis = payload?.analysis || {};
  const preview = payload?.preview_scan;
  const hasLidar = Boolean(preview?.points?.length);
  const normalizedPreview = hasLidar ? normalizeArchiveLidarResult(preview, analysis) : null;
  drawLidarMapOnCanvas($("roverArchiveLidarCanvas"), normalizedPreview, normalizedPreview?.analysis, {
    mode: archiveOverlayRenderMode(),
    range: state.archivePlayback.range,
  });
  const id = payload ? payload.id || metadata.id || state.recordings.selectedId || "" : "";
  if (!id) state.recordings.timeline = null;
  setupArchivePlayback(payload, normalizedPreview);
  const rows = id ? [
    { title: "ID", sub: recordingKindLabel(metadata.kind || payload.kind), ok: true, level: "ok", value: id },
    { title: "Duration", sub: formatShortDate(metadata.started_at), ok: true, level: "ok", value: metadata.duration_s ? `${fmtNumber(metadata.duration_s, 1)}s` : metadata.state || "--" },
    { title: "Timeline", sub: `${payload?.timeline_summary?.lidar_frames || state.recordings.timeline?.lidar?.frames?.length || 0} lidar · ${payload?.timeline_summary?.camera_frames || state.recordings.timeline?.camera?.frames?.length || 0} camera`, ok: Boolean(state.recordings.timeline), level: state.recordings.timeline ? "ok" : "warn", value: state.recordings.timeline ? "playable" : "preview only" },
    { title: "LiDAR Analysis", sub: `${analysis.walls?.length || 0} walls · ${analysis.objects?.length || 0} objects`, ok: hasLidar, level: hasLidar ? "ok" : "warn", value: hasLidar ? `${preview.points.length} pts` : "no scan" },
  ] : [
    { title: "Archive", sub: "기록을 선택하세요", ok: false, level: "warn", value: "--" },
  ];
  renderStatusRows("roverArchiveDetails", rows);
  renderArchiveDownloadButton(payload, id);
}

function setupArchivePlayback(payload, fallbackPreview) {
  const timeline = state.recordings.timeline;
  prepareArchiveTimelineCache(timeline);
  const metadata = payload?.metadata || {};
  const duration = Number(timeline?.duration_s || metadata.duration_s || 0);
  const lidarFrames = state.archivePlayback.cache.lidarFrames || [];
  const cameraFrames = state.archivePlayback.cache.cameraFrames || [];
  const lastFrameTime = Math.max(0, ...lidarFrames.map((frame) => Number(frame.t_s) || 0), ...cameraFrames.map((frame) => Number(frame.t_s) || 0));
  state.archivePlayback.durationS = Math.max(duration, lastFrameTime);
  state.archivePlayback.timeS = Math.min(state.archivePlayback.timeS || 0, state.archivePlayback.durationS);
  state.archivePlayback.fallbackPreview = fallbackPreview;
  const scrubber = $("roverArchiveScrubber");
  if (scrubber) {
    scrubber.max = String(Math.max(0, state.archivePlayback.durationS));
    scrubber.value = String(Math.min(Number(scrubber.value) || 0, state.archivePlayback.durationS));
    scrubber.disabled = !timeline;
  }
  const mapScrubber = $("roverOrchardMapScrubber");
  if (mapScrubber) {
    mapScrubber.max = String(Math.max(0, state.archivePlayback.durationS));
    mapScrubber.value = String(Math.min(Number(mapScrubber.value) || 0, state.archivePlayback.durationS));
    mapScrubber.disabled = !timeline;
  }
  const play = $("roverArchivePlayButton");
  if (play) play.disabled = !timeline;
  renderArchiveFrame();
}

function toggleArchivePlayback() {
  if (state.archivePlayback.playing) {
    stopArchivePlayback(false);
    return;
  }
  if (!state.recordings.timeline) return;
  ensureArchiveSegmentForTime(state.archivePlayback.timeS).catch((err) => debugLog("archive", "initial segment load failed", err));
  state.archivePlayback.playing = true;
  state.archivePlayback.startedAt = performance.now();
  state.archivePlayback.startTimeS = state.archivePlayback.timeS >= state.archivePlayback.durationS ? 0 : state.archivePlayback.timeS;
  archivePlaybackTick();
}

function stopArchivePlayback(reset = false) {
  state.archivePlayback.playing = false;
  if (state.archivePlayback.raf) {
    cancelAnimationFrame(state.archivePlayback.raf);
    state.archivePlayback.raf = null;
  }
  if (reset) state.archivePlayback.timeS = 0;
  renderArchiveFrame();
}

function archivePlaybackTick(now = performance.now()) {
  if (!state.archivePlayback.playing) return;
  const elapsed = (now - state.archivePlayback.startedAt) / 1000;
  const next = state.archivePlayback.startTimeS + elapsed;
  if (next >= state.archivePlayback.durationS) {
    state.archivePlayback.timeS = state.archivePlayback.durationS;
    stopArchivePlayback(false);
    return;
  }
  state.archivePlayback.timeS = next;
  renderArchiveFrame();
  state.archivePlayback.raf = requestAnimationFrame(archivePlaybackTick);
}

function setArchiveTime(value, options = {}) {
  if (options.pause) stopArchivePlayback(false);
  state.archivePlayback.timeS = Math.max(0, Math.min(state.archivePlayback.durationS, Number(value) || 0));
  ensureArchiveSegmentForTime(state.archivePlayback.timeS).catch((err) => debugLog("archive", "seek segment load failed", err));
  renderArchiveFrame();
}

function renderArchiveFrame() {
  const timeline = state.recordings.timeline;
  const timeS = Math.max(0, Number(state.archivePlayback.timeS) || 0);
  ensureArchiveSegmentForTime(timeS).catch((err) => debugLog("archive", "render segment load failed", err));
  const cache = state.archivePlayback.cache || {};
  const lidarFrame = timeline ? nearestTimelineFrame(cache.lidarFrames || [], timeS) : null;
  const cameraFrame = timeline && state.archivePlayback.sync ? nearestTimelineFrame(cache.cameraFrames || [], timeS) : null;
  const lidarResult = lidarFrame?.result || state.archivePlayback.fallbackPreview;
  const overlayMode = archiveOverlayRenderMode();
  const fusedTrees = archiveFusedTreesForTime(timeS, state.archivePlayback.range, lidarResult);
  const lidarRenderKey = [
    lidarFrame ? `frame:${lidarFrame.cacheIndex}` : "fallback",
    overlayMode,
    state.perceptionOverlayMode,
    fusedTrees ? `fused:${fusedTrees.length}:${state.recordings.orchardMap?.algorithm || ""}` : "frame-fusion",
    `local:${lidarResult?.analysis?.orchard?.trees?.length || 0}`,
    state.archivePlayback.overlayVisible ? "overlay-on" : "overlay-off",
    fmtNumber(state.archivePlayback.range, 2),
  ].join(":");
  if (lidarRenderKey !== state.archivePlayback.lastLidarRenderKey) {
    drawLidarMapOnCanvas($("roverArchiveLidarCanvas"), lidarResult, lidarResult?.analysis, {
      mode: overlayMode,
      range: state.archivePlayback.range,
      fusedTrees,
    });
    state.archivePlayback.lastLidarRenderKey = lidarRenderKey;
  }

  const image = $("roverArchiveCameraImage");
  if (image && cameraFrame?.data) {
    warmArchiveCameraCache(cameraFrame);
    const src = archiveCameraFrameSrc(cameraFrame);
    const cameraRenderKey = `${cameraFrame.cacheIndex}:${src}`;
    if (src && state.archivePlayback.lastCameraRenderKey !== cameraRenderKey) {
      image.src = src;
      state.archivePlayback.lastCameraRenderKey = cameraRenderKey;
    }
  } else if (image && !cameraFrame) {
    image.removeAttribute("src");
    state.archivePlayback.lastCameraRenderKey = "";
  }
  const cameraOverlayKey = [
    cameraFrame ? `frame:${cameraFrame.cacheIndex}` : "none",
    state.cameraOverlayMode,
    state.perceptionOverlayMode,
    state.archivePlayback.overlayVisible ? "overlay-on" : "overlay-off",
    cameraFrame?.overlay?.algorithm || "no-overlay",
    cameraFrame?.overlay?.detections?.length || 0,
  ].join(":");
  if (cameraOverlayKey !== state.archivePlayback.lastCameraOverlayRenderKey) {
    renderArchiveCameraOverlay(cameraFrame);
    state.archivePlayback.lastCameraOverlayRenderKey = cameraOverlayKey;
  }
  const cameraCaption = cameraFrame
    ? `${cameraFrame.frame_id || "camera"} · ${fmtClock(cameraFrame.t_s)} · ${cameraFrame.source_width || cameraFrame.width}x${cameraFrame.source_height || cameraFrame.height}`
    : state.archivePlayback.sync ? "camera preview 없음" : "동시 재생 꺼짐";
  text("roverArchiveCameraCaption", cameraCaption);
  text("roverArchiveTime", `${fmtClock(timeS)} / ${fmtClock(state.archivePlayback.durationS)}`);
  text("roverOrchardMapTime", `${fmtClock(timeS)} / ${fmtClock(state.archivePlayback.durationS)}`);
  const scrubber = $("roverArchiveScrubber");
  if (scrubber && document.activeElement !== scrubber) scrubber.value = String(timeS);
  const mapScrubber = $("roverOrchardMapScrubber");
  if (mapScrubber && document.activeElement !== mapScrubber) mapScrubber.value = String(timeS);
  const play = $("roverArchivePlayButton");
  if (play) play.textContent = state.archivePlayback.playing ? "일시정지" : "재생";
  drawOrchardMap();
}

function archiveFusedTreesForTime(timeS, range, lidarResult = null) {
  const maxRange = Number(range) || state.archivePlayback.range || 25;
  const localTrees = archiveLocalOrchardTreesForFrame(lidarResult, maxRange);
  const projectedTrees = archiveProjectedMapTreesForTime(timeS, maxRange);
  if (localTrees.length) return archiveFrameAnchoredFusionTrees(localTrees, projectedTrees, maxRange);
  return projectedTrees;
}

function archiveLocalOrchardTreesForFrame(lidarResult, range) {
  const trees = lidarResult?.analysis?.orchard?.trees || lidarResult?.analysis?.trees || [];
  return (trees || [])
    .map((tree) => {
      const center = tree.center || tree;
      const x = Number(center.x_m);
      const y = Number(center.y_m);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      const distance = Math.hypot(x, y);
      return {
        ...tree,
        center: { x_m: x, y_m: y },
        distance_m: distance,
        source: "archive_frame_lidar_orchard",
      };
    })
    .filter((tree) => tree && tree.distance_m <= range + 1.5)
    .sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0));
}

function archiveProjectedMapTreesForTime(timeS, range) {
  const map = state.recordings.orchardMap;
  if (!map?.ok || !Array.isArray(map.trees) || !map.trees.length) return null;
  const pose = nearestMapPathPose(map.path || [], timeS);
  if (!pose) return null;
  const heading = Number.isFinite(Number(pose.theta_rad))
    ? Number(pose.theta_rad)
    : (Number(pose.heading_deg) || 0) * Math.PI / 180;
  const cosH = Math.cos(heading);
  const sinH = Math.sin(heading);
  const px = Number(pose.x_m) || 0;
  const py = Number(pose.y_m) || 0;
  return map.trees
    .map((tree) => {
      const dx = (Number(tree.x_m) || 0) - px;
      const dy = (Number(tree.y_m) || 0) - py;
      const localX = cosH * dx + sinH * dy;
      const localY = -sinH * dx + cosH * dy;
      const distance = Math.hypot(localX, localY);
      return {
        ...tree,
        center: { x_m: localX, y_m: -localY },
        distance_m: distance,
        source: "orchard_map_fused",
      };
    })
    .filter((tree) => tree.distance_m <= range + 1.5)
    .sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0))
    .slice(0, 48);
}

function archiveFrameAnchoredFusionTrees(localTrees, projectedTrees, range) {
  const projected = Array.isArray(projectedTrees) ? projectedTrees : [];
  const used = new Set();
  const rows = [];
  for (const local of localTrees) {
    const localCenter = local.center || {};
    let bestIndex = -1;
    let bestDistance = Infinity;
    projected.forEach((tree, index) => {
      if (used.has(index)) return;
      const center = tree.center || {};
      const distance = Math.hypot(Number(localCenter.x_m) - Number(center.x_m), Number(localCenter.y_m) - Number(center.y_m));
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    const gate = Math.max(0.55, Math.min(1.3, 0.32 + 0.045 * Math.max(0, Number(local.distance_m) || 0)));
    const matched = bestIndex >= 0 && bestDistance <= gate ? projected[bestIndex] : null;
    if (matched) used.add(bestIndex);
    const confidence = Math.max(
      Number(local.confidence) || 0,
      matched ? Number(matched.confidence) || 0 : 0,
    );
    if (!matched && confidence < 0.7 && local.kind !== "trunk_like") continue;
    rows.push({
      ...(matched || {}),
      ...local,
      center: local.center,
      radius_m: Number(local.radius_m || matched?.radius_m || 0.22),
      confidence: Math.min(0.98, confidence),
      map_confidence: matched?.confidence,
      frame_confidence: local.confidence,
      source: matched ? "archive_frame_anchored_fusion" : "archive_frame_lidar_orchard",
      match_error_m: matched ? Number(bestDistance.toFixed(3)) : null,
    });
  }
  if (!rows.length && projected.length) return projected.slice(0, 16);
  return rows
    .filter((tree) => Number(tree.distance_m || 0) <= range + 1.5)
    .sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0))
    .slice(0, 18);
}

function nearestMapPathPose(path, timeS) {
  if (!Array.isArray(path) || !path.length) return null;
  return nearestTimelineFrame(path, timeS);
}

function nearestTimelineFrame(frames, timeS) {
  if (!Array.isArray(frames) || !frames.length) return null;
  let low = 0;
  let high = frames.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const stamp = Number(frames[mid].t_s) || 0;
    if (stamp < timeS) low = mid + 1;
    else high = mid - 1;
  }
  if (low <= 0) return frames[0];
  if (low >= frames.length) return frames[frames.length - 1];
  const before = frames[low - 1];
  const after = frames[low];
  return Math.abs((Number(after.t_s) || 0) - timeS) < Math.abs(timeS - (Number(before.t_s) || 0)) ? after : before;
}

function initOrchardMapCanvas() {
  document.querySelectorAll("[data-orchard-map-layer]").forEach((canvas) => {
    canvas.addEventListener("pointerdown", (event) => {
      state.orchardMapView.dragging = true;
      state.orchardMapView.dragMoved = false;
      state.orchardMapView.lastX = event.clientX;
      state.orchardMapView.lastY = event.clientY;
      canvas.classList.add("dragging");
      canvas.setPointerCapture?.(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!state.orchardMapView.dragging) return;
      const dx = event.clientX - state.orchardMapView.lastX;
      const dy = event.clientY - state.orchardMapView.lastY;
      if (Math.hypot(dx, dy) > 2) state.orchardMapView.dragMoved = true;
      state.orchardMapView.offsetX += dx;
      state.orchardMapView.offsetY += dy;
      state.orchardMapView.lastX = event.clientX;
      state.orchardMapView.lastY = event.clientY;
      drawOrchardMap();
    });
    const endDrag = (event) => {
      const shouldSeek = state.orchardMapView.dragging && !state.orchardMapView.dragMoved && event?.type === "pointerup";
      state.orchardMapView.dragging = false;
      document.querySelectorAll("[data-orchard-map-layer]").forEach((item) => item.classList.remove("dragging"));
      if (event?.pointerId !== undefined) canvas.releasePointerCapture?.(event.pointerId);
      if (shouldSeek) seekArchiveFromOrchardMapPointer(event, canvas);
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoomOrchardMap(event.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });
  });
}

function seekArchiveFromOrchardMapPointer(event, canvas) {
  const map = state.recordings.orchardMap;
  const path = map?.path || [];
  if (!canvas || !map?.ok || path.length < 2) return;
  const projection = orchardMapProjection(canvas, map);
  if (!projection) return;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let best = null;
  for (const pose of path) {
    const p = projection.project(Number(pose.x_m) || 0, Number(pose.y_m) || 0);
    const distance = Math.hypot(p.x - x, p.y - y);
    if (!best || distance < best.distance) best = { pose, distance };
  }
  if (!best) return;
  setArchiveTime(Number(best.pose.t_s) || 0, { pause: true });
}

function resetOrchardMapView() {
  state.orchardMapView.scale = 1;
  state.orchardMapView.offsetX = 0;
  state.orchardMapView.offsetY = 0;
}

function zoomOrchardMap(factor) {
  state.orchardMapView.scale = Math.max(0.35, Math.min(8, state.orchardMapView.scale * factor));
  drawOrchardMap();
}

function drawOrchardMap() {
  const canvases = ORCHARD_MAP_LAYERS.map((layer) => ({ ...layer, canvas: $(layer.canvasId) })).filter((layer) => layer.canvas);
  if (!canvases.length) return;
  const map = state.recordings.orchardMap;
  if (!map?.ok) {
    for (const layer of canvases) {
      const prepared = prepareCanvas(layer.canvas);
      if (!prepared) continue;
      const { ctx, w, h } = prepared;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = cssVar("--chart-bg-bottom", "#0f172a");
      ctx.fillRect(0, 0, w, h);
      drawLidarEmpty(ctx, w, h, "과수 지도 생성 대기");
      text(layer.statsId, "--");
    }
    text("roverOrchardMapStats", "archive를 선택하면 3-layer 지도 생성");
    return;
  }
  for (const layer of canvases) {
    drawOrchardMapLayer(layer, map);
  }
  const layers = orchardMapLayers(map);
  const summary = ORCHARD_MAP_LAYERS.map((spec) => {
    const layer = layers[spec.id];
    const centerline = layer?.centerline || {};
    return `${spec.label} ${layer?.tree_count || 0}T/${centerline.ok ? `${centerline.length_m || 0}m` : "no path"}`;
  }).join(" · ");
  text("roverOrchardMapStats", `${summary} · t ${fmtClock(state.archivePlayback.timeS)} · zoom ${fmtNumber(state.orchardMapView.scale, 2)}x`);
}

function drawOrchardMapLayer(spec, rootMap) {
  const canvas = spec.canvas;
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { ctx, w, h } = prepared;
  const layers = orchardMapLayers(rootMap);
  const map = layers[spec.id] || rootMap;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = cssVar("--chart-bg-bottom", "#0f172a");
  ctx.fillRect(0, 0, w, h);
  const projection = orchardMapProjection(canvas, rootMap, w, h);
  if (!projection) return;
  const { bounds, project, scale } = projection;

  drawOrchardMapGrid(ctx, w, h, project, bounds);
  const path = map.path || [];
  if (path.length > 1) {
    ctx.strokeStyle = cssVar("--chart-good", "#55e2b2");
    ctx.lineWidth = 3;
    ctx.globalAlpha = 0.88;
    ctx.beginPath();
    path.forEach((pose, index) => {
      const p = project(Number(pose.x_m) || 0, Number(pose.y_m) || 0);
      if (index === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  }
  drawOrchardCenterline(ctx, project, map.centerline);
  drawMapMarker(ctx, project, map.start, cssVar("--chart-good", "#55e2b2"), "S");
  drawMapMarker(ctx, project, map.end, cssVar("--chart-rose", "#f21b58"), "E");
  for (const tree of map.trees || []) {
    const p = project(Number(tree.x_m) || 0, Number(tree.y_m) || 0);
    const r = Math.max(6, Math.min(24, (Number(tree.radius_m) || 0.3) * scale));
    const confidence = Math.max(0.25, Math.min(1, Number(tree.confidence) || 0.5));
    ctx.globalAlpha = 0.55 + confidence * 0.35;
    ctx.fillStyle = colorWithAlpha(spec.color, 0.2);
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  const currentPose = nearestMapPathPose(path, state.archivePlayback.timeS);
  drawRoverPoseMarker(ctx, project, currentPose);
  const centerline = map.centerline || {};
  text(spec.statsId, `${map.tree_count || 0} trees · center ${centerline.ok ? `${fmtNumber(centerline.length_m || 0, 1)}m` : centerline.state || "--"} · gap ${centerline.row_gap_m ? `${fmtNumber(centerline.row_gap_m, 1)}m` : "--"}`);
}

function orchardMapLayers(map) {
  const fallback = map?.ok ? {
    id: "fusion",
    label: "Weighted Fusion",
    method: map.fusion?.method || "legacy fusion map",
    tree_count: map.tree_count || 0,
    trees: map.trees || [],
    path: map.path || [],
    start: map.start,
    end: map.end,
    bounds: map.bounds,
    centerline: map.centerline || {},
  } : null;
  return {
    lidar: map?.maps?.lidar || fallback,
    camera: map?.maps?.camera || fallback,
    fusion: map?.maps?.fusion || fallback,
  };
}

function drawOrchardCenterline(ctx, project, centerline) {
  const points = centerline?.points || [];
  if (!points.length) return;
  ctx.save();
  ctx.strokeStyle = cssVar("--chart-yellow", "#f4bd4f");
  ctx.lineWidth = 3.5;
  ctx.globalAlpha = centerline.ok ? 0.95 : 0.55;
  ctx.beginPath();
  points.forEach((point, index) => {
    const p = project(Number(point.x_m) || 0, Number(point.y_m) || 0);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  for (const point of points) {
    const p = project(Number(point.x_m) || 0, Number(point.y_m) || 0);
    ctx.fillStyle = "rgba(244, 189, 79, 0.92)";
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function colorWithAlpha(hex, alpha) {
  const value = String(hex || "#2f76ff").replace("#", "");
  if (value.length !== 6) return `rgba(47, 118, 255, ${alpha})`;
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function orchardMapProjection(canvas, map, width, height) {
  const rect = canvas.getBoundingClientRect();
  const w = width || rect.width;
  const h = height || rect.height;
  if (!w || !h) return null;
  const bounds = map.bounds || { min_x: -5, max_x: 5, min_y: -5, max_y: 5 };
  const spanX = Math.max(1, Number(bounds.max_x) - Number(bounds.min_x));
  const spanY = Math.max(1, Number(bounds.max_y) - Number(bounds.min_y));
  const baseScale = Math.min((w - 52) / spanX, (h - 52) / spanY);
  const scale = baseScale * state.orchardMapView.scale;
  const cx = w / 2 + state.orchardMapView.offsetX;
  const cy = h / 2 + state.orchardMapView.offsetY;
  const midX = (Number(bounds.min_x) + Number(bounds.max_x)) / 2;
  const midY = (Number(bounds.min_y) + Number(bounds.max_y)) / 2;
  return {
    bounds,
    scale,
    project: (x, y) => ({ x: cx + (x - midX) * scale, y: cy - (y - midY) * scale }),
  };
}

function drawOrchardMapGrid(ctx, w, h, project, bounds) {
  ctx.save();
  ctx.strokeStyle = "rgba(148, 163, 184, 0.18)";
  ctx.lineWidth = 1;
  const minX = Math.floor(Number(bounds.min_x) / 2) * 2;
  const maxX = Math.ceil(Number(bounds.max_x) / 2) * 2;
  const minY = Math.floor(Number(bounds.min_y) / 2) * 2;
  const maxY = Math.ceil(Number(bounds.max_y) / 2) * 2;
  for (let x = minX; x <= maxX; x += 2) {
    const a = project(x, minY);
    const b = project(x, maxY);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  for (let y = minY; y <= maxY; y += 2) {
    const a = project(minX, y);
    const b = project(maxX, y);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.fillStyle = cssVar("--muted", "#a8b3cf");
  ctx.font = "800 12px Inter, Arial, sans-serif";
  ctx.fillText("2m grid", 14, h - 16);
  ctx.restore();
}

function drawMapMarker(ctx, project, pose, color, label) {
  if (!pose) return;
  const p = project(Number(pose.x_m) || 0, Number(pose.y_m) || 0);
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.font = "900 10px Inter, Arial, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, p.x, p.y + 0.5);
  ctx.restore();
}

function drawRoverPoseMarker(ctx, project, pose) {
  if (!pose) return;
  const p = project(Number(pose.x_m) || 0, Number(pose.y_m) || 0);
  const heading = Number.isFinite(Number(pose.theta_rad))
    ? Number(pose.theta_rad)
    : (Number(pose.heading_deg) || 0) * Math.PI / 180;
  const size = 15;
  const nose = { x: p.x + Math.sin(heading) * size, y: p.y - Math.cos(heading) * size };
  const left = { x: p.x + Math.sin(heading + 2.45) * size * 0.72, y: p.y - Math.cos(heading + 2.45) * size * 0.72 };
  const right = { x: p.x + Math.sin(heading - 2.45) * size * 0.72, y: p.y - Math.cos(heading - 2.45) * size * 0.72 };
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.fillStyle = cssVar("--chart-cyan", "#38d8ff");
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(nose.x, nose.y);
  ctx.lineTo(left.x, left.y);
  ctx.lineTo(right.x, right.y);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "rgba(56, 216, 255, 0.18)";
  ctx.beginPath();
  ctx.arc(p.x, p.y, size * 1.15, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function downloadOrchardMapPng() {
  const canvas = $("roverOrchardMapFusionCanvas") || $("roverOrchardMapLidarCanvas") || $("roverOrchardMapCameraCanvas");
  if (!canvas || !state.recordings.orchardMap?.ok) return;
  const link = document.createElement("a");
  link.download = `${state.recordings.selectedId || "orchard-map"}_fusion_centerline.png`;
  link.href = canvas.toDataURL("image/png");
  link.click();
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
  state.chartShift = Math.max(0, 1 - elapsed / Math.max(300, state.chartShiftDurationMs || POLL_INTERVAL_MS));
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
    "/rover-control": [],
  };
  for (const [id, series] of groups[currentRoute()] || []) drawMultiLine(id, series);
  if (currentRoute() === "/system" && state.cpuMode === "separate") drawCoreCharts();
  if (routeSupportsLidarLive()) renderActiveLidarMap();
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
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.clip();
  for (const item of series) drawLine(ctx, item.values, item.color, item.max || 100, w, h);
  ctx.restore();
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
  const buffered = values.slice(-CHART_BUFFER_POINTS);
  const scrollProgress = buffered.length > MAX_POINTS ? 1 - state.chartShift : 0;
  const points = buffered.map((value, index) => {
    if (value === null || Number.isNaN(value)) return null;
    const offset = Math.max(0, MAX_POINTS - buffered.length);
    const x = buffered.length <= 1 ? w : ((offset + index - scrollProgress) / (MAX_POINTS - 1)) * w;
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
  drawLidarMapOnCanvas($("lidarScanCanvas"), result, result?.analysis, { mode: state.lidarOverlayMode });
  drawLidarMapOnCanvas($("roverLidarCanvas"), result, result?.analysis, { mode: state.lidarOverlayMode });
}

function drawLidarMapOnCanvas(canvas, result, overlays = {}, options = {}) {
  if (!canvas) return;
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { ctx, w, h } = prepared;
  const colors = chartColors();
  const points = result?.ok ? result.points || [] : [];
  const mode = ["raw", "both", "overlay"].includes(options.mode) ? options.mode : "both";
  const showPoints = mode !== "overlay";
  const showOverlay = mode !== "raw";
  const radius = Math.max(80, Math.min(w, h) * 0.39);
  const cx = w / 2;
  const cy = h / 2;
  const optionRange = Number(options.range);
  const range = Number.isFinite(optionRange) ? clampLidarRange(optionRange) : lidarDrawRange();
  const gridStep = lidarGridStep(range);

  ctx.clearRect(0, 0, w, h);
  const gradient = ctx.createRadialGradient(cx, cy, radius * 0.12, cx, cy, radius);
  gradient.addColorStop(0, cssVar("--chart-bg-top", "rgba(255,255,255,0.05)"));
  gradient.addColorStop(1, cssVar("--chart-bg-bottom", "rgba(255,255,255,0.01)"));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, w, h);
  drawLidarGrid(ctx, cx, cy, radius, range, gridStep);

  if (!points.length && !showOverlay) {
    drawLidarEmpty(ctx, w, h, result?.message || "스캔 대기");
    return;
  }

  if (showPoints && points.length) {
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
  }

  if (showOverlay) drawLidarOverlays(ctx, overlays, { cx, cy, radius, range, colors, perceptionMode: state.perceptionOverlayMode, fusedTrees: options.fusedTrees });

  ctx.save();
  ctx.fillStyle = cssVar("--text", "#f7f9ff");
  ctx.font = "800 12px Inter, Arial, sans-serif";
  ctx.fillText(`${showPoints ? points.length : 0} points`, 14, 22);
  ctx.fillText(`${fmtNumber(result?.metrics?.coverage_deg ?? angleCoverage(points), 0)}° coverage`, 14, 40);
  ctx.textAlign = "right";
  ctx.fillText(`Grid: ${fmtNumber(gridStep, 0)}m · Range: ${fmtNumber(range, 1)}m`, w - 14, h - 16);
  ctx.restore();
}

function lidarCanvasPoint(point, viewport) {
  return {
    x: viewport.cx + ((Number(point?.x_m) || 0) / viewport.range) * viewport.radius,
    y: viewport.cy - ((Number(point?.y_m) || 0) / viewport.range) * viewport.radius,
  };
}

function drawLidarOverlays(ctx, overlays, viewport) {
  const walls = overlays?.walls || [];
  const objects = overlays?.objects || [];
  const trees = Array.isArray(viewport.fusedTrees) ? viewport.fusedTrees : (overlays?.orchard?.trees || overlays?.trees || []);
  const mode = viewport.perceptionMode || "fusion";
  if (mode === "camera") return;
  if (!walls.length && !objects.length && !trees.length) return;
  ctx.save();
  if (mode === "lidar") for (const wall of walls) {
    const a = lidarCanvasPoint(wall.start || wall.a || {}, viewport);
    const b = lidarCanvasPoint(wall.end || wall.b || {}, viewport);
    ctx.strokeStyle = cssVar("--chart-amber", "#f4bd4f");
    ctx.lineWidth = 3;
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  if (mode === "lidar") for (const item of objects) {
    const center = lidarCanvasPoint(item.center || item.centroid || {}, viewport);
    const radiusM = Math.max(0.08, Number(item.radius_m) || 0.18);
    const r = Math.max(5, (radiusM / viewport.range) * viewport.radius);
    drawObjectTrack(ctx, item, viewport);
    ctx.globalAlpha = 0.95;
    ctx.strokeStyle = cssVar("--chart-rose", "#f21b58");
    ctx.fillStyle = "rgba(242,27,88,0.13)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  if (mode === "fusion") drawOrchardTreeOverlay(ctx, trees, viewport);
  ctx.restore();
}

function drawObjectTrack(ctx, item, viewport) {
  const rawTrack = Array.isArray(item.track) ? item.track : [];
  if (rawTrack.length < 2 || item.track_drawable !== true) return;
  const track = rawTrack.filter((point) => Number.isFinite(Number(point.x_m)) && Number.isFinite(Number(point.y_m))).slice(-7);
  if (track.length < 2) return;
  const maxJumpM = Math.max(0.45, viewport.range * 0.055);
  for (let index = 1; index < track.length; index += 1) {
    const jump = Math.hypot(Number(track[index].x_m) - Number(track[index - 1].x_m), Number(track[index].y_m) - Number(track[index - 1].y_m));
    if (jump > maxJumpM) return;
  }
  ctx.save();
  ctx.strokeStyle = cssVar("--chart-good", "#55e2b2");
  ctx.lineWidth = 2.4;
  ctx.globalAlpha = Math.max(0.35, Math.min(0.82, Number(item.track_confidence) || 0.55));
  ctx.beginPath();
  track.forEach((point, index) => {
    const p = lidarCanvasPoint(point, viewport);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  const tail = lidarCanvasPoint(track[track.length - 1], viewport);
  ctx.fillStyle = cssVar("--chart-good", "#55e2b2");
  ctx.beginPath();
  ctx.arc(tail.x, tail.y, 3.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawOrchardTreeOverlay(ctx, trees, viewport) {
  const labelBoxes = [];
  for (const tree of trees || []) {
    const center = lidarCanvasPoint(tree.center || tree, viewport);
    const radiusM = Math.max(0.16, Number(tree.radius_m) || 0.28);
    const r = Math.max(7, Math.min(20, (radiusM / viewport.range) * viewport.radius));
    const confidence = Math.max(0.2, Math.min(1, Number(tree.confidence) || 0.5));
    const color = treeConfidenceColor(confidence);
    ctx.globalAlpha = 0.74 + confidence * 0.22;
    ctx.strokeStyle = color.stroke;
    ctx.fillStyle = color.fill;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(center.x, center.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    drawLidarConfidenceMark(ctx, center.x, center.y, r, confidence, color, labelBoxes);
  }
}

function treeConfidenceColor(confidence) {
  if (confidence >= 0.82) return { stroke: cssVar("--chart-blue", "#2f76ff"), fill: "rgba(47,118,255,0.2)" };
  if (confidence >= 0.66) return { stroke: cssVar("--chart-good", "#55e2b2"), fill: "rgba(85,226,178,0.16)" };
  return { stroke: cssVar("--chart-amber", "#f4bd4f"), fill: "rgba(244,189,79,0.13)" };
}

function drawLidarConfidenceMark(ctx, x, y, r, confidence, color, placed) {
  const label = `${Math.round(confidence * 100)}%`;
  ctx.save();
  ctx.font = "900 10px Inter, Arial, sans-serif";
  const w = Math.ceil(ctx.measureText(label).width) + 8;
  const h = 16;
  const rect = { x: x + r + 4, y: y - h / 2, w, h };
  const overlaps = placed.some((item) => rectsOverlap(rect, item));
  if (overlaps) {
    ctx.fillStyle = color.stroke;
    ctx.globalAlpha = 0.95;
    ctx.beginPath();
    ctx.arc(x, y - r - 4, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    return;
  }
  placed.push(rect);
  ctx.fillStyle = "rgba(2, 6, 23, 0.82)";
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
  ctx.strokeStyle = color.stroke;
  ctx.lineWidth = 1;
  ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.fillText(label, rect.x + 4, rect.y + rect.h / 2 + 0.5);
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
window.addEventListener("resize", () => {
  requestChartAnimation();
  renderLiveCameraOverlays();
  if (currentRoute() === "/rover-control") {
    state.archivePlayback.lastLidarRenderKey = "";
    state.archivePlayback.lastCameraOverlayRenderKey = "";
    renderArchiveFrame();
  }
});
window.addEventListener("pagehide", () => {
  if (hasActiveRoverInput() || state.roverControl.stoppingWithFade || Math.abs(state.roverControl.currentLinear) > 0.001 || Math.abs(state.roverControl.currentAngular) > 0.001) {
    try {
      const body = JSON.stringify({ source: "pagehide" });
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/rover/stop", new Blob([body], { type: "application/json" }));
      } else {
        fetch("/api/rover/stop", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body });
      }
    } catch (_) {
      // The bridge command TTL still stops stale UI commands.
    }
  }
  if (state.lidarLive.socket || state.lidarLive.enabled) {
    closeLidarLive("페이지 종료로 LiDAR WebSocket을 닫습니다.", { userStopped: false });
  }
  if (state.cameraLive.socket || state.cameraLive.enabled) {
    closeCameraLive("페이지 종료로 Camera WebSocket을 닫습니다.", { userStopped: false });
  }
  clearArchiveFrameCache();
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
initCameraLive();
initFanControls();
initDockerControls();
initRoverControl();
renderRoute();
loadPlan().catch(console.error);
loadDocs().catch(console.error);
tick();
setInterval(tick, POLL_INTERVAL_MS);
