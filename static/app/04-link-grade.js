// ---- Link grade (NET-10) ------------------------------------------------------
// One place that judges the link to the dashboard and, in automatic transfer
// quality, chooses the plan of every channel (camera, LiDAR, IMU, GPS, status
// polls, request limiter) through effectiveLinkProfile().
//
// Measure: a tiny GET /api/ping (204, no body) every second, and while driving
// also the round trip of every control request. The server time (Server-Timing)
// is taken off, so a sample is the network round trip including queueing.
// Judge: the smallest of the last 4 samples (queueing only adds time, so the
// smallest is close to the bare round trip):
//   good < 25 ms, normal < 90 ms, slow < 220 ms, otherwise very slow.
// A worse grade is taken at once (4 s of samples), a better one only after it
// has held for 10 s with 30 % margin, so a link that swings does not swing the
// plan. Two pings in a row without an answer (3 s each) is "offline".
// The thresholds are assumptions; the field link log (below) is meant to replace them.
//
// Field log (optional, NET-10 (5)): with ?link_log=1 or localStorage
// "ubts-link-log" = "1" the page sends its samples every 10 s to the dashboard,
// which keeps them in runtime/link_log/ (one JSONL file a day).
const LINK_GRADE = {
  pingIntervalMs: 1000,
  pingTimeoutMs: 3000,
  window: 4,
  thresholdsMs: { good: 25, normal: 90, slow: 220 },
  upgradeHoldMs: 10000,
  upgradeMargin: 0.7,
  offlineAfterFailures: 2,
  order: ["good", "normal", "slow", "very_slow"],
  profile: { good: "detail", normal: "balanced", slow: "slow", very_slow: "tiny", offline: null, unknown: "slow" },
  label: { good: "연결 좋음", normal: "연결 보통", slow: "연결 느림", very_slow: "연결 아주 느림", offline: "연결 끊김", unknown: "연결 확인 중" },
  level: { good: "ok", normal: "ok", slow: "warn", very_slow: "warn", offline: "bad", unknown: "" },
};
const linkGrade = {
  grade: "unknown",
  profile: LINK_GRADE.profile.unknown,
  samples: [],
  failures: 0,
  betterSince: 0,
  betterGrade: null,
  changes: [],
  timer: null,
  inFlight: false,
  log: [],
  logTimer: null,
};

function linkGradeOf(rttMs, margin = 1) {
  const t = LINK_GRADE.thresholdsMs;
  if (rttMs < t.good * margin) return "good";
  if (rttMs < t.normal * margin) return "normal";
  if (rttMs < t.slow * margin) return "slow";
  return "very_slow";
}

// The decision, separate from timers and the network so it can be tested.
// `now` is a millisecond clock. Returns the new grade (may be unchanged).
function nextLinkGrade(model, now) {
  if (model.failures >= LINK_GRADE.offlineAfterFailures) return "offline";
  const recent = model.samples.slice(-LINK_GRADE.window);
  if (!recent.length) return model.grade === "offline" ? "offline" : model.grade;
  // The first answer often comes while the page is still starting (seen: 300-500 ms
  // on localhost); wait for a second one, and take the first seconds without the
  // upgrade hold so the page does not sit in the very-slow plan for 10 s.
  if (model.grade === "unknown" && recent.length < 2) return "unknown";
  const warmingUp = model.samples.length < LINK_GRADE.window;
  const base = Math.min(...recent);
  const current = model.grade;
  const worse = linkGradeOf(base);
  const rank = (grade) => LINK_GRADE.order.indexOf(grade);
  if (warmingUp || current === "unknown" || current === "offline" || rank(worse) > rank(current)) {
    model.betterGrade = null;
    return worse;
  }
  const better = linkGradeOf(base, LINK_GRADE.upgradeMargin);
  if (rank(better) < rank(current)) {
    if (model.betterGrade !== better) { model.betterGrade = better; model.betterSince = now; }
    if (now - model.betterSince >= LINK_GRADE.upgradeHoldMs) { model.betterGrade = null; return better; }
  } else {
    model.betterGrade = null;
  }
  return current;
}

function noteLinkSample(rttMs, ok = true, kind = "ping") {
  const now = performance.now();
  if (ok && Number.isFinite(rttMs) && rttMs >= 0) {
    linkGrade.samples.push(rttMs);
    if (linkGrade.samples.length > 16) linkGrade.samples.splice(0, linkGrade.samples.length - 16);
    if (kind === "ping") linkGrade.failures = 0;
  } else if (kind === "ping") {
    linkGrade.failures += 1;
  }
  if (linkGrade.logOn) {
    linkGrade.log.push({ t: Date.now(), kind, ok: Boolean(ok), rtt_ms: Number.isFinite(rttMs) ? Math.round(rttMs) : null, grade: linkGrade.grade });
    if (linkGrade.log.length > 2000) linkGrade.log.splice(0, linkGrade.log.length - 2000);
  }
  const next = nextLinkGrade(linkGrade, now);
  if (next !== linkGrade.grade) setLinkGrade(next, now);
  else renderLinkBadge();
}

function serverTimeMs(response) {
  const header = response?.headers?.get?.("Server-Timing") || "";
  const match = /dur=([0-9.]+)/.exec(header);
  return match ? Number(match[1]) : 0;
}

async function pingLink() {
  if (linkGrade.inFlight || document.hidden) return;
  linkGrade.inFlight = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), LINK_GRADE.pingTimeoutMs);
  const started = performance.now();
  try {
    const response = await pageFetch("/api/ping", { cache: "no-store", priority: "high", signal: controller.signal });
    // Finish the request (no body) so the browser does not keep it open.
    await response.arrayBuffer().catch(() => null);
    const elapsed = performance.now() - started;
    noteLinkSample(Math.max(0, elapsed - serverTimeMs(response)), response.ok || response.status === 204, "ping");
  } catch (_) {
    noteLinkSample(null, false, "ping");
  } finally {
    clearTimeout(timer);
    linkGrade.inFlight = false;
  }
}

function setLinkGrade(grade, now = performance.now()) {
  const previousProfile = linkGrade.profile;
  linkGrade.grade = grade;
  linkGrade.changes.push(now);
  if (linkGrade.changes.length > 32) linkGrade.changes.shift();
  // Offline keeps the last plan: nothing is reopened until the link answers again.
  if (LINK_GRADE.profile[grade]) linkGrade.profile = LINK_GRADE.profile[grade];
  renderLinkBadge();
  if (linkGrade.profile !== previousProfile && liveNetworkProfile() === "auto") applyLinkProfile();
}

// Reopen the streams with the plan of the new grade (automatic mode only).
function applyLinkProfile() {
  window.RoverGps?.profileChanged?.();
  if (state.imuLive.socket) { closeImuLive("전송 등급 변경", { userStopped: false }); setTimeout(startImuLive, 250); }
  if (state.cameraLive.socket) { closeCameraLive("전송 등급 변경", { userStopped: false }); setTimeout(startCameraLive, 250); }
  if (state.lidarLive.socket) { closeLidarLive("전송 등급 변경", { userStopped: false }); setTimeout(startLidarLive, 250); }
  state.lastTelemetryPoll = Math.min(state.lastTelemetryPoll || 0, Date.now() - 1000);
}

function linkGradeReason() {
  const base = linkGrade.samples.length ? Math.round(Math.min(...linkGrade.samples.slice(-LINK_GRADE.window))) : null;
  const rtt = base === null ? "" : ` (왕복 ${base} ms)`;
  if (linkGrade.grade === "offline") return "연결 끊김 · 조종 중이었다면 차는 입력 만료로 스스로 정지합니다.";
  if (liveNetworkProfile() !== "auto") return `전송 품질을 직접 골랐습니다${rtt}.`;
  if (linkGrade.grade === "slow" || linkGrade.grade === "very_slow") return `연결이 느려 화면 자료를 줄였습니다${rtt}.`;
  if (linkGrade.grade === "unknown") return "연결을 재는 중입니다.";
  return `연결 상태에 맞춰 화면 자료를 보냅니다${rtt}.`;
}

// The status bar's connection badge shows the grade (one badge, no new element).
function renderLinkBadge() {
  const el = $("connectionState");
  if (!el) return;
  const telemetryDown = (state.telemetryFailures || 0) >= 3;
  const grade = telemetryDown ? "offline" : linkGrade.grade;
  const level = LINK_GRADE.level[grade] ?? "";
  const label = LINK_GRADE.label[grade] || "연결됨";
  const className = level ? `status-chip ${level}` : "status-chip";
  if (el.className !== className) el.className = className;
  if (el.textContent !== label) el.textContent = label;
  const reason = telemetryDown ? "상태 응답이 없습니다 · 조종 중이었다면 차는 입력 만료로 스스로 정지합니다." : linkGradeReason();
  if (el.title !== reason) el.title = reason;
}

function linkLogEnabled() {
  try {
    if (new URLSearchParams(location.search).get("link_log") === "1") return true;
    return localStorage.getItem("ubts-link-log") === "1";
  } catch (_) {
    return false;
  }
}

async function flushLinkLog() {
  if (!linkGrade.log.length) return;
  const samples = linkGrade.log.splice(0, 500);
  const recording = state.recordings?.status?.active ? state.recordings.status.recording_id || null : null;
  try {
    await pageFetch("/api/link-log", {
      method: "POST", cache: "no-store", priority: "low",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ samples, recording_id: recording, profile: liveNetworkProfile() }),
    });
  } catch (_) {
    // A lost batch is not retried; the log is a field aid, not a record.
  }
}

function initLinkGrade() {
  if (linkGrade.timer) return;
  linkGrade.logOn = linkLogEnabled();
  pingLink();
  linkGrade.timer = setInterval(pingLink, LINK_GRADE.pingIntervalMs);
  if (linkGrade.logOn) linkGrade.logTimer = setInterval(flushLinkLog, 10000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pingLink(); });
  renderLinkBadge();
}

window.LinkGrade = {
  grade: () => linkGrade.grade,
  profile: () => linkGrade.profile,
  constrained: () => linkGrade.profile === "slow" || linkGrade.profile === "tiny",
  samples: () => linkGrade.samples.slice(),
  note: noteLinkSample,
  changesPerMinute: () => linkGrade.changes.filter((t) => performance.now() - t < 60000).length,
};
