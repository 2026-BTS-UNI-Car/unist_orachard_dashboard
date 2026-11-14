// Front-steer and executor parts of Rover Control (UI-07, UI-08, UI-09 local grid, UI-12, UI-16, docs/SCREENS_KO.md,
// docs/STEERING_SERVER_KO.md). The server marks the page with data-vehicle-kind="front_steer" (DASHBOARD_VEHICLE, D-12);
// the style sheet then hides the differential pad and shows the steering panel, the potentiometer and the autonomy
// section. Their modules are fetched only on a front-steer dashboard and only when Rover Control opens, so the first
// load of the differential dashboard does not grow. The field tuning values and the executor's local grid are parts of
// Rover Control on every dashboard. renderRoute() announces every route with "ubts:route".
const STEER_VEHICLE = document.documentElement.dataset.vehicleKind === "front_steer";
const steerScreens = { drive: null, auto: null, tuning: null, grid: null, pot: null, driveScreen: null };

function steerPageApi() {
  // controlRequest: the one transport for driving requests (NET-05): high priority, never queued, with a time limit.
  // driveStatus: the steering panel's latest /api/rover/status reply, so the potentiometer rows on the same page do
  // not ask for it again.
  return { getJson, postJson, controlRequest, currentRoute, renderReadout, chartColors, driveStatus: steerDriveStatus };
}

function steerDriveStatus() {
  const ctl = steerScreens.driveScreen?.controller;
  return ctl && ctl.lastStatusAt !== null ? { reply: ctl.lastStatus, at: ctl.lastStatusAt } : null;
}

function loadSteerScreen(name, url, init) {
  if (!steerScreens[name]) {
    steerScreens[name] = import(url)
      .then((module) => { const screen = module[init](steerPageApi()); if (name === "drive") steerScreens.driveScreen = screen; return screen; })
      .catch((err) => { steerScreens[name] = null; console.warn(`front-steer ${name}`, err); });
  }
  steerScreens[name]?.then((screen) => screen?.routeChanged());
}

function loadSteerScreens(route = currentRoute()) {
  if (STEER_VEHICLE && (route === "/rover-control" || steerScreens.drive)) {
    loadSteerScreen("drive", "/static/steer/steer-drive.mjs", "initSteerDrive");
    loadSteerScreen("auto", "/static/steer/auto-panel.mjs", "initAutoPanel");
    loadSteerScreen("pot", "/static/steer/steer-sensor.mjs", "initSteerSensor");
  }
  // The field tuning values (UI-12) are a part of Rover Control on every dashboard; without an executor they say so.
  if (route === "/rover-control" || steerScreens.tuning) loadSteerScreen("tuning", "/static/steer/tuning-page.mjs", "initTuningPage");
  // The executor's local grid on the live occupancy grid map (UI-09): shown only when the executor sends one.
  if (route === "/rover-control" || steerScreens.grid) loadSteerScreen("grid", "/static/steer/local-grid.mjs", "initLocalGrid");
}

window.addEventListener("ubts:route", (event) => loadSteerScreens(event.detail?.route));
