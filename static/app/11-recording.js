
async function refreshRecordingStatus() {
  if (state.recordings.statusInFlight || state.recordings.actionInFlight) return;
  const ticket = state.recordings.statusRequestId = (state.recordings.statusRequestId || 0) + 1;
  state.recordings.statusInFlight = ticket;
  const requestedAt = performance.now();
  state.recordings.lastStatusPollSteadyAt = requestedAt;
  const controller = state.recordings.statusController = new AbortController();
  const timeout = setTimeout(() => {
    if (ticket !== state.recordings.statusRequestId) return;
    invalidateRecordingStatusRead();
    state.recordings.status = {ok: false, state: "unavailable", error: "status_timeout"};
    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
  }, 4000);
  try {
    const status = await getJson("/api/recordings/status", {signal: controller.signal});
    if (ticket !== state.recordings.statusRequestId) return;
    state.recordings.status = status;
    state.recordings.lastStatusAt = Date.now();
    state.recordings.lastStatusSteadyAt = requestedAt;
    renderRecordingStatus();
  } catch (err) {
    if (ticket !== state.recordings.statusRequestId) return;
    state.recordings.status = { ok: false, state: "unavailable", error: err.message };
    state.recordings.lastStatusAt = Date.now();
    renderRecordingStatus();
  } finally {
    clearTimeout(timeout);
    if (state.recordings.statusInFlight === ticket) {
      state.recordings.statusInFlight = null;
      state.recordings.statusController = null;
    }
  }
}

function invalidateRecordingStatusRead() {
  state.recordings.statusRequestId = (state.recordings.statusRequestId || 0) + 1;
  state.recordings.statusController?.abort();
  state.recordings.statusController = null;
  state.recordings.statusInFlight = null;
}

function maybeRefreshRecordingStatus() {
  if (currentRoute() !== "/rover-control") return;
  renderRecordingStatus();
  const now = performance.now();
  const last = state.recordings.lastStatusPollSteadyAt;
  if (!Number.isFinite(now)) return;
  if (Number.isFinite(last) && now >= last && now - last < 2200) return;
  refreshRecordingStatus().catch((err) => debugLog("recording", "status refresh failed", err));
}

// The sensors of a recording request (2026-11-19: one check box per sensor). A state without the list (an older
// caller) asks for what its kind and switches said.
const RECORDING_SENSOR_LABEL = { lidar: "LiDAR", camera: "카메라", imu: "IMU", gps: "GPS", drive: "구동", steering: "조향",
  executor: "실행기", tf: "TF" };
const RECORDING_ALL_SENSORS = Object.keys(RECORDING_SENSOR_LABEL);
const RECORDING_ERROR_TEXT = {
  sensors_need_lidar_or_camera: "LiDAR 또는 카메라 하나는 골라야 한다",
  recording_busy: "기록 요청이 이미 진행 중",
};

function recordingSensorsOf(recordings) {
  if (Array.isArray(recordings?.sensors)) return recordings.sensors.filter((name) => RECORDING_ALL_SENSORS.includes(name));
  const kind = recordings?.kind || "both";
  return RECORDING_ALL_SENSORS.filter((name) => (name === "lidar" && kind !== "camera") || (name === "camera" && kind !== "lidar")
    || (name === "imu" && recordings?.includeImu !== false) || (name === "gps" && recordings?.includeGps !== false));
}

// The request body: the new fields (sensors, tag) and the old ones (kind, include_imu, include_gps) for a recorder that
// does not know the list yet. A recorder that knows "sensors" uses it.
function recordingStartBody(sensors, tag = "manual") {
  const lidar = sensors.includes("lidar"), camera = sensors.includes("camera");
  return { sensors: [...sensors], tag: tag === "autonomy" ? "autonomy" : "manual",
    kind: lidar && camera ? "both" : camera ? "camera" : "lidar",
    include_imu: sensors.includes("imu"), include_gps: sensors.includes("gps") };
}

function recordingSensorsLabel(sensors) {
  const list = (sensors || []).filter((name) => RECORDING_SENSOR_LABEL[name]);
  if (list.length === RECORDING_ALL_SENSORS.length) return "모든 센서";
  return list.map((name) => RECORDING_SENSOR_LABEL[name]).join("+") || "센서 없음";
}

// The recorder state as the panels read it: known only within the 5 s freshness gate.
function recordingStatusView() {
  const status = state.recordings.status || {};
  const ageMs = performance.now() - state.recordings.lastStatusSteadyAt;
  const known = status.ok === true && status.status_fresh !== false
    && typeof status.active === "boolean"
    && Number.isFinite(ageMs) && ageMs >= 0 && ageMs <= 5000;
  return { known, active: known && status.active, status, busy: Boolean(state.recordings.actionInFlight),
           id: known && status.active ? status.recording_id || null : null, tag: status.tag || null,
           sensors: Array.isArray(status.sensors) ? status.sensors : null };
}

// options: {sensors, tag} (the autonomy panel: every sensor, tag "autonomy"); without them the check boxes.
async function startRecording(options = {}) {
  if (state.recordings.actionInFlight) return { ok: false, error: "recording_busy" };
  const sensors = Array.isArray(options?.sensors) ? options.sensors : recordingSensorsOf(state.recordings);
  if (!sensors.includes("lidar") && !sensors.includes("camera")) {
    state.recordings.lastError = RECORDING_ERROR_TEXT.sensors_need_lidar_or_camera;
    renderRecordingStatus();
    return { ok: false, error: "sensors_need_lidar_or_camera" };
  }
  state.recordings.actionInFlight = true;
  state.recordings.lastError = "";
  invalidateRecordingStatusRead();
  const requestedAt = performance.now();
  renderRecordingStatus("기록 시작 중");
  let payload = null;
  try {
	    payload = await postJson("/api/recordings/start", recordingStartBody(sensors, options?.tag));
	    state.recordings.status = payload.status || payload;
	    state.recordings.lastStatusAt = Date.now();
    state.recordings.lastStatusSteadyAt = requestedAt;
    if (payload && payload.ok === false) {
      state.recordings.lastError = `기록 시작 거절: ${RECORDING_ERROR_TEXT[payload.error] || payload.error || payload.state || "이유 모름"}`;
    }
    renderRecordingStatus();
    await loadRecordings();
  } catch (err) {
    state.recordings.status = { ok: false, state: "start_failed", error: err.message };
    payload = { ok: false, error: err.message };
    renderRecordingStatus();
  } finally {
    state.recordings.actionInFlight = false;
    renderRecordingStatus();
  }
  return payload || { ok: false, error: "no_reply" };
}

async function stopRecording() {
  if (state.recordings.actionInFlight) return { ok: false, error: "recording_busy" };
  let result = null;
  state.recordings.actionInFlight = true;
  state.recordings.lastError = "";
  invalidateRecordingStatusRead();
  const requestedAt = performance.now();
  renderRecordingStatus("기록 중지 중");
  try {
    const payload = await postJson("/api/recordings/stop", {});
    result = payload;
    state.recordings.status = payload.status || payload;
    state.recordings.lastStatusAt = Date.now();
    state.recordings.lastStatusSteadyAt = requestedAt;
    renderRecordingStatus();
    await loadRecordings();
  } catch (err) {
    state.recordings.status = { ok: false, state: "stop_failed", error: err.message };
    result = { ok: false, error: err.message };
    renderRecordingStatus();
  } finally {
    state.recordings.actionInFlight = false;
    renderRecordingStatus();
  }
  return result || { ok: false, error: "no_reply" };
}

function recordingKindLabel(kind) {
  return { lidar: "LiDAR", camera: "Camera", both: "LiDAR+Camera" }[kind] || kind || "대기";
}

function recordingImuFrameCount(payload) {
  const value = payload?.timeline?.imu_frames
    ?? payload?.timeline_summary?.imu_frames
    ?? payload?.metadata?.timeline?.imu_frames
    ?? 0;
  const count = Number(value);
  return Number.isFinite(count) ? Math.max(0, count) : 0;
}

function recordingSensorLabel(payload) {
  const listed = payload?.sensors || payload?.metadata?.sensors;
  if (Array.isArray(listed) && listed.length) return recordingSensorsLabel(listed);
  const kind = payload?.kind || payload?.metadata?.kind;
  const label = recordingKindLabel(kind);
  const sensors = recordingImuFrameCount(payload) > 0 ? `${label}+IMU` : label;
  const gpsCount = payload?.timeline?.gps_frames ?? payload?.timeline_summary?.gps_frames ?? payload?.metadata?.timeline?.gps_frames ?? 0;
  return Number(gpsCount) > 0 ? `${sensors}+GPS` : sensors;
}

function renderRecordingStatus(override) {
  const { status, known, active } = recordingStatusView();
  // This secondary row shares the exact primary freshness gate. It must age
  // locally even when general telemetry and recorder requests stop arriving.
  const detail = $("roverRecorderDetail");
  if (detail) {
    detail.querySelector(".status-dot").className = `status-dot ${known ? "ok" : "warn"}`;
    detail.querySelector("small").textContent = known
      ? status.recording_id || status.status_url || "ros2-data-recorder"
      : "현재 상태를 확인할 수 없습니다";
    detail.querySelector("b").textContent = status.state === "disabled" ? "disabled"
      : !known ? "상태 미확인"
      : active ? `${recordingKindLabel(status.kind)} 기록 중` : status.state || "ready";
  }
  const chosen = recordingSensorsOf(state.recordings);
  const shown = active ? (Array.isArray(status.sensors) ? status.sensors : recordingSensorsOf({
    kind: status.kind, includeImu: status.include_imu !== false, includeGps: status.include_gps !== false })) : chosen;
  const sensorLabel = recordingSensorsLabel(shown);
  const noImage = !chosen.includes("lidar") && !chosen.includes("camera");
  const duration = recordingDurationSeconds(status);
  const message = override || (!known && status.state !== "disabled"
    ? "기록 상태 확인 중 · 연결을 확인하세요"
    : active
    ? `${sensorLabel} 기록 중${status.tag === "autonomy" ? " (자율주행)" : ""} · ${status.recording_id || ""}`
    : state.recordings.lastError || status.error || (noImage ? RECORDING_ERROR_TEXT.sensors_need_lidar_or_camera : "")
      || status.state || `${sensorLabel} 기록 대기`);
  const target = $("roverRecordingStatus");
  if (target) {
    target.className = `test-result ${known && !state.recordings.lastError ? "ok" : status.error || state.recordings.lastError ? "bad" : "testing"}`;
    target.textContent = message;
  }
  document.querySelectorAll("[data-record-sensor]").forEach((box) => {
    box.checked = shown.includes(box.dataset.recordSensor);
    box.disabled = state.recordings.actionInFlight || active || !known;
  });
  const start = $("roverRecordStartButton");
  if (start) {
    start.disabled = state.recordings.actionInFlight || active || !known || status.state === "disabled" || noImage;
    start.title = noImage ? RECORDING_ERROR_TEXT.sensors_need_lidar_or_camera : "";
    start.textContent = status.state === "disabled" ? "녹화는 후속 단계에서 활성화" : active ? "기록 중"
      : `기록 시작 (${chosen.length > 3 && chosen.length < RECORDING_ALL_SENSORS.length ? `센서 ${chosen.length}개` : recordingSensorsLabel(chosen)})`;
  }
  const stop = $("roverRecordStopButton");
  if (stop) stop.disabled = state.recordings.actionInFlight || (known && !active) || status.state === "disabled";
  text("roverRecordingTimer", !known ? "--:--" : active ? fmtClock(duration) : "00:00");
  text("roverRecorderState", !known ? "상태 미확인" : active ? "recording" : "ready");
  text("roverRecorderKind", active ? sensorLabel : status.archive_count !== undefined ? `${status.archive_count} archives` : "대기");
  renderFrameRecordState(known, active);
}

// Recording indicator of the status bar. On the drive page it shows what the
// recorder panel shows (ordered status reads with the 5 s freshness gate).
// The other routes do not poll the recorder: there the newest telemetry row
// is used, and nothing is claimed once telemetry has stopped.
function renderFrameRecordState(known, active) {
  const el = $("frameRecordState");
  if (!el) return;
  if (currentRoute() !== "/rover-control") {
    const service = state.telemetry?.services?.data_recorder;
    known = Boolean(service?.ok) && !(state.telemetryFailures >= 3);
    active = known && service.active === true;
  }
  const className = active ? "status-chip recording" : "status-chip";
  const label = !known ? "기록 미확인" : active ? "기록 중" : "기록 대기";
  if (el.className !== className) el.className = className;
  if (el.textContent !== label) el.textContent = label;
}

function recordingDurationSeconds(status) {
  if (Number.isFinite(Number(status.duration_s))) return Number(status.duration_s);
  if (!status.started_at) return 0;
  const started = new Date(status.started_at).getTime();
  if (!Number.isFinite(started)) return 0;
  return Math.max(0, (Date.now() - started) / 1000);
}
