// Front-steer screens (UI-07, docs/STEERING_SERVER_KO.md). The server marks the page with
// data-vehicle-kind="front_steer" (DASHBOARD_VEHICLE, D-12); the style sheet then hides the differential pad and shows
// the steering panel. The modules are fetched only on a front-steer dashboard and only when their route opens, so the
// first load of the differential dashboard does not grow. renderRoute() announces every route with "ubts:route".
const STEER_VEHICLE = document.documentElement.dataset.vehicleKind === "front_steer";
const steerScreens = { drive: null };

function steerPageApi() {
  return { getJson, postJson, currentRoute, renderReadout, chartColors };
}

function loadSteerScreens(route = currentRoute()) {
  if (STEER_VEHICLE && route === "/rover-control" && !steerScreens.drive) {
    steerScreens.drive = import("/static/steer/steer-drive.mjs")
      .then((module) => module.initSteerDrive(steerPageApi()))
      .catch((err) => { steerScreens.drive = null; console.warn("steering panel", err); });
  }
  steerScreens.drive?.then((screen) => screen?.routeChanged());
}

window.addEventListener("ubts:route", (event) => loadSteerScreens(event.detail?.route));
