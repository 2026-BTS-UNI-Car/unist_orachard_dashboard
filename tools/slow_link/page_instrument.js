// Page instrumentation of the slow-link bench. Injected before the dashboard scripts
// (Page.addScriptToEvaluateOnNewDocument). Observers and thin wrappers only: it sends
// no request of its own (it re-imports one module the page has already loaded),
// changes no application state and alters no message.
(() => {
  if (window.__slowlink) return;
  const RING = 20000;
  const U = {
    ws: {},            // per pathname: counters
    arrivals: {},      // per stream: [[clientMs, stampMs, bytes], ...]
    renders: {},       // per stream: [[clientMs, stampMs, extra], ...]
    canvas: {},        // canvas id -> {frames, lastFrame}
    longtasks: [], loaf: {count: 0, total_ms: 0, blocking_ms: 0, max_ms: 0, scripts: {}},
    resources: {},     // path -> {n, bytes, durs:[], sizes:[[clientMs, transferBytes], ...]}
    control: [],       // one row per /api/rover/* request the page made
    marks: [],         // [clientMs, name] set by the runner or by page events
    milestones: {},    // name -> clientMs, first time only
    errors: [],
    lag: [],           // event-loop lag samples (ms)
    frames: 0, frameTimes: [],
    vis: [[Date.now(), document.visibilityState]],
    maps: {}, mapEvents: [],
    handler: {},       // per pathname: application message-handler durations (ms)
    status: {},        // per pathname: last status payload of the stream
  };
  window.__slowlink = U;
  const push = (store, key, row) => { const a = store[key] ||= []; a.push(row); if (a.length > RING) a.splice(0, a.length - RING / 2); };
  const milestone = name => { if (!(name in U.milestones)) U.milestones[name] = Date.now(); };
  U.mark = name => { U.marks.push([Date.now(), String(name)]); };
  document.addEventListener('visibilitychange', () => U.vis.push([Date.now(), document.visibilityState]));
  document.addEventListener('DOMContentLoaded', () => milestone('dom_content_loaded'));
  window.addEventListener('load', () => milestone('load_event'));
  window.addEventListener('error', e => U.errors.length < 200 && U.errors.push([Date.now(), 'error', String(e.message).slice(0, 200), (e.filename || '').split('/').pop() + ':' + e.lineno]));
  window.addEventListener('unhandledrejection', e => U.errors.length < 200 && U.errors.push([Date.now(), 'rejection', String(e.reason?.message || e.reason).slice(0, 200)]));

  // ---- control requests: when the page asked, when the answer came
  const nativeFetch = window.fetch;
  window.fetch = function (input, init) {
    let path = '';
    try { path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname; } catch {}
    if (!path.startsWith('/api/rover/') || path === '/api/rover/status') return nativeFetch.apply(this, arguments);
    const row = {op: path.slice(11), t_call: Date.now(), p_call: performance.now(), seq: null, left: null, right: null, reason: null,
      p_headers: null, p_done: null, status: null, ok: null, error: null, answer_error: null};
    try { const body = JSON.parse(init?.body || '{}'); row.seq = body.seq ?? null; row.left = body.left ?? null; row.right = body.right ?? null; row.reason = body.reason ?? null; } catch {}
    if (U.control.length < 50000) U.control.push(row);
    const promise = nativeFetch.apply(this, arguments);
    promise.then(response => {
      row.p_headers = performance.now(); row.status = response.status;
      response.clone().json().then(answer => { row.p_done = performance.now(); row.ok = answer?.ok ?? null; row.answer_error = answer?.error ?? null; })
        .catch(() => { row.p_done = performance.now(); });
    }, error => { row.p_done = performance.now(); row.error = String(error?.message || error).slice(0, 120); });
    return promise;
  };
  const nativeBeacon = navigator.sendBeacon?.bind(navigator);
  if (nativeBeacon) navigator.sendBeacon = (url, data) => { if (String(url).includes('/api/rover/')) U.mark('beacon:' + String(url).split('/api/rover/')[1]); return nativeBeacon(url, data); };

  // ---- WebSocket arrivals (listener registered first, so it runs before the application handler)
  const NativeWS = window.WebSocket;
  const stampOf = {
    '/ws/lidar': d => { if (typeof d !== 'string' || !d.startsWith('{"type":"scan"')) return null; const m = /"stamp":([0-9.]+)/.exec(d); return m ? Number(m[1]) * 1000 : null; },
    '/ws/imu': d => { if (typeof d !== 'string' || d.indexOf('"type":"imu"') < 0) return null; const m = /"stamp":\{"sec":(\d+),"nanosec":(\d+)\}/.exec(d); return m ? Number(m[1]) * 1000 + Number(m[2]) / 1e6 : null; },
    '/ws/gps': d => { if (typeof d !== 'string') return null; const m = /"received_at":([0-9.]+)/.exec(d); return m ? Number(m[1]) * 1000 : null; },
    '/ws/camera': d => {
      if (!(d instanceof ArrayBuffer) || d.byteLength < 10) return null;
      try { const n = new DataView(d).getUint32(5, false); const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(d, 9, n))); return meta.type === 'frame' ? Number(meta.stamp) * 1000 : null; } catch { return null; }
    },
  };
  class WrappedWS extends NativeWS {
    constructor(url, protocols) {
      super(url, protocols);
      let path = '?'; try { path = new URL(url, location.href).pathname; } catch {}
      const s = U.ws[path] ||= {opened: 0, closed: 0, msgs: 0, bytes: 0, sent: 0, sent_bytes: 0, close_codes: [], open_times: [], max_msg: 0};
      s.opened++; if (s.open_times.length < 200) s.open_times.push(Date.now());
      this.__slowlinkPath = path;
      const arrival = ev => {
        const now = Date.now(); const d = ev.data;
        const size = typeof d === 'string' ? d.length : (d.byteLength ?? d.size ?? 0);
        s.msgs++; s.bytes += size; if (size > s.max_msg) s.max_msg = size;
        if (typeof d === 'string' && d.startsWith('{"type":"status"')) { try { U.status[path] = JSON.parse(d); } catch {} }
        const f = stampOf[path]; const stamp = f ? f(d) : null;
        if (stamp !== null) push(U.arrivals, path, [now, stamp, size]);
      };
      arrival.__slowlinkInner = true;
      super.addEventListener('message', arrival);
      this.addEventListener('close', ev => { s.closed++; if (s.close_codes.length < 200) s.close_codes.push([Date.now(), ev.code, String(ev.reason).slice(0, 80)]); });
    }
    addEventListener(type, listener, options) {
      if (type !== 'message' || typeof listener !== 'function' || listener.__slowlinkInner) return super.addEventListener(type, listener, options);
      const path = this.__slowlinkPath;
      const timed = function (ev) {
        const t0 = performance.now();
        try { return listener.call(this, ev); } finally { push(U.handler, path, Math.round((performance.now() - t0) * 100) / 100); }
      };
      timed.__slowlinkInner = true;
      return super.addEventListener(type, timed, options);
    }
    set onmessage(fn) {
      const path = this.__slowlinkPath;
      super.onmessage = typeof fn !== 'function' ? fn : function (ev) { const t0 = performance.now(); try { return fn.call(this, ev); } finally { push(U.handler, path, Math.round((performance.now() - t0) * 100) / 100); } };
    }
    get onmessage() { return super.onmessage; }
    send(data) { const s = U.ws[this.__slowlinkPath]; if (s) { s.sent++; s.sent_bytes += typeof data === 'string' ? data.length : (data.byteLength || 0); } return super.send(data); }
  }
  window.WebSocket = WrappedWS;

  // ---- frame clock, canvas update rate, event-loop lag
  const CTX = CanvasRenderingContext2D.prototype;
  for (const name of ['clearRect', 'fillRect', 'drawImage', 'putImageData']) {
    const original = CTX[name];
    CTX[name] = function (...args) {
      const c = this.canvas; const id = c && (c.id || (c.isConnected ? c.className || 'anon' : null));
      if (id) { const r = U.canvas[id] ||= {frames: 0, lastFrame: -1}; if (r.lastFrame !== U.frames) { r.lastFrame = U.frames; r.frames++; } }
      return original.apply(this, args);
    };
  }
  const raf = t => { U.frames++; U.frameTimes.push(t); if (U.frameTimes.length > 4000) U.frameTimes.splice(0, 2000); requestAnimationFrame(raf); };
  requestAnimationFrame(raf);
  let expected = performance.now() + 100;
  setInterval(() => { const now = performance.now(); U.lag.push(Math.max(0, now - expected)); if (U.lag.length > 6000) U.lag.splice(0, 3000); expected = now + 100; }, 100);

  // ---- long tasks / long animation frames / resources
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) { U.longtasks.push([Math.round(performance.timeOrigin + e.startTime), Math.round(e.duration)]); if (U.longtasks.length > 5000) U.longtasks.splice(0, 2500); } }).observe({type: 'longtask', buffered: true}); } catch {}
  try {
    new PerformanceObserver(l => {
      for (const e of l.getEntries()) {
        U.loaf.count++; U.loaf.total_ms += e.duration; U.loaf.blocking_ms += e.blockingDuration || 0; U.loaf.max_ms = Math.max(U.loaf.max_ms, e.duration);
        for (const s of e.scripts || []) {
          const key = `${(s.sourceURL || '').split('/').pop()}:${s.sourceFunctionName || s.invoker || '?'}`.slice(0, 120);
          const r = U.loaf.scripts[key] ||= {n: 0, ms: 0, max: 0}; r.n++; r.ms += s.duration; r.max = Math.max(r.max, s.duration);
        }
      }
    }).observe({type: 'long-animation-frame', buffered: true});
  } catch {}
  try { performance.setResourceTimingBufferSize(200000); } catch {}
  const controlTimings = [];
  const round1 = v => Math.round(v * 10) / 10;
  try {
    new PerformanceObserver(l => {
      for (const e of l.getEntries()) {
        let p = e.name; try { const u = new URL(e.name); p = u.pathname.replace(/\/recordings\/\d{8}T\d{6}Z[^/]*/, '/recordings/:id').replace(/\/segment-\d+\.m4s$/, '/segment-N.m4s'); } catch {}
        const bytes = e.transferSize || 0;
        const r = U.resources[p] ||= {n: 0, bytes: 0, durs: [], sizes: []}; r.n++; r.bytes += bytes; r.durs.push(Math.round(e.duration));
        if (r.durs.length > 3000) r.durs.splice(0, 1500);
        if (p.startsWith('/api/live/map')) { r.sizes.push([Math.round(performance.timeOrigin + e.responseEnd), bytes, e.decodedBodySize || 0]); if (r.sizes.length > 3000) r.sizes.splice(0, 1500); }
        if (p.startsWith('/api/rover/') && p !== '/api/rover/status' && controlTimings.length < 50000)
          controlTimings.push([p.slice(11), round1(e.startTime), round1(e.connectStart - e.startTime), round1(e.connectEnd - e.connectStart), round1(e.requestStart - e.startTime),
            round1(e.responseStart - e.startTime), round1(e.responseEnd - e.startTime), bytes]);
      }
      performance.clearResourceTimings();
    }).observe({type: 'resource', buffered: true});
  } catch {}

  // ---- render hooks (the application's global functions; wrapped once they exist)
  const wrapGlobal = (name, make) => {
    const original = window[name];
    if (typeof original !== 'function' || original.__slowlink) return false;
    const wrapped = make(original); wrapped.__slowlink = true; window[name] = wrapped; return true;
  };
  let pendingCamera = new Map();
  function installAppHooks() {
    wrapGlobal('drawLidarMapOnCanvas', orig => function (canvas, result, ...rest) {
      const out = orig.call(this, canvas, result, ...rest);
      if (canvas?.id === 'roverLidarCanvas' && result?.stamp) { milestone('first_lidar_drawn'); push(U.renders, 'lidar', [Date.now(), Number(result.stamp) * 1000, (result.points || []).length]); }
      return out;
    });
    // Every argument is passed on unchanged: the windowed decode queue hands its continuation in as a later argument.
    wrapGlobal('displayBinaryCameraFrame', orig => function (blob, payload, ...rest) {
      pendingCamera.set(Number(payload.frame_count), [Number(payload.stamp) * 1000, blob.size, Number(payload.width) || 0]);
      if (pendingCamera.size > 64) pendingCamera.delete(pendingCamera.keys().next().value);
      return orig.call(this, blob, payload, ...rest);
    });
    wrapGlobal('acknowledgeCameraFrame', orig => function (socket, frameCount, status, ...rest) {
      const p = pendingCamera.get(Number(frameCount)); pendingCamera.delete(Number(frameCount));
      if (status === 'decoded') milestone('first_camera_decoded');
      // Frames answered without a decode (superseded in the page's queue, timeout, error, not visible) are counted apart.
      push(U.renders, status === 'decoded' ? 'camera' : 'camera_' + status, [Date.now(), p ? p[0] : null, p ? p[1] : 0, p ? p[2] : 0]);
      return orig.call(this, socket, frameCount, status, ...rest);
    });
    wrapGlobal('drawImuAttitude', orig => function (canvas, ...rest) {
      const out = orig.call(this, canvas, ...rest);
      if (canvas?.id === 'roverImuCanvas') {
        try { const s = state.imuLive.raw?.stamp; if (s) push(U.renders, 'imu', [Date.now(), s.sec * 1000 + s.nanosec / 1e6, 0]); } catch {}
      }
      return out;
    });
    const gpsText = document.getElementById('roverGpsPosition');
    if (gpsText && !gpsText.__slowlink) {
      gpsText.__slowlink = true;
      new MutationObserver(() => { const a = U.arrivals['/ws/gps']; const last = a && a[a.length - 1]; if (last) push(U.renders, 'gps_text', [Date.now(), last[1], 0]); })
        .observe(gpsText, {childList: true, characterData: true, subtree: true});
    }
    for (const [id, name] of [['roverArmButton', 'click:arm'], ['roverStopButton', 'click:stop'], ['frameStopButton', 'click:frame_stop']]) {
      const el = document.getElementById(id);
      if (el && !el.__slowlink) { el.__slowlink = true; el.addEventListener('click', () => U.mark(name), true); }
    }
  }
  document.addEventListener('DOMContentLoaded', installAppHooks);
  window.addEventListener('load', installAppHooks);
  document.addEventListener('keydown', e => { if (!e.repeat) U.mark('keydown:' + e.code); }, true);
  document.addEventListener('keyup', e => U.mark('keyup:' + e.code), true);
  // "Usable for driving": the page scripts ran to the end (the telemetry poll of the last script has started).
  const readyTimer = setInterval(() => {
    try {
      if (typeof state === 'object' && state.lastTelemetryPoll > 0 && document.getElementById('roverArmButton')) milestone('drive_ready');
      if (typeof state === 'object' && state.telemetry) milestone('first_telemetry');
      if (U.milestones.drive_ready && U.milestones.first_telemetry) clearInterval(readyTimer);
    } catch {}
  }, 25);

  // ---- occupancy map hooks through the module instance the page already loaded. Hooked after the load event,
  // so the instrumentation does not fetch the module ahead of the page (first-load order and weight stay the page's own).
  // The server may name the module with a content-hash version (?v=...); a module is one instance per address, so
  // the hook imports exactly the address the page used (taken from the resource timing list).
  const metricMapAddress = () => {
    for (const entry of performance.getEntriesByType('resource')) {
      try {
        const url = new URL(entry.name);
        if (/^\/static\/metric-map\.mjs$/.test(url.pathname) && /^(\?v=[0-9a-f]+)?$/.test(url.search)) return url.pathname + url.search;
      } catch {}
    }
    return '/static/metric-map.mjs';
  };
  const hookMaps = () => import(metricMapAddress()).then(m => {
    const P = m.MetricMap.prototype;
    const draw = P.draw, commit = P.commit;
    P.draw = function (...args) {
      const out = draw.apply(this, args);
      const id = this.canvas?.id; if (!id) return out;
      U.maps[id] = this;
      const s = this.snapshot || {}; const stamp = Number(s.pose?.stamp ?? s.stamp);
      push(U.renders, 'map:' + id, [Date.now(), Number.isFinite(stamp) && stamp > 1e9 ? stamp * 1000 : null, Number(s.version) || 0,
        this.view ? Math.round(this.view.scale * 1000) / 1000 : null, this.auto ? 1 : 0, this.tileBitmaps?.size ?? 0,
        Number.isFinite(Number(s._slow_link_published_at)) ? Number(s._slow_link_published_at) * 1000 : null, s.reason ? String(s.reason).slice(0, 40) : null]);
      return out;
    };
    P.commit = function (snapshot, ...args) {
      const before = {view: this.view ? {...this.view} : null, auto: this.auto, epoch: this.snapshot?.map_epoch, version: this.snapshot?.version, tiles: this.tileBitmaps?.size ?? 0};
      const out = commit.call(this, snapshot, ...args);
      const id = this.canvas?.id;
      const reset = before.view && !before.auto && (this.auto || !this.view || Math.abs(this.view.scale - before.view.scale) > 1e-9);
      if (id && (reset || (before.epoch && snapshot?.map_epoch && before.epoch !== snapshot.map_epoch) || (this.tileBitmaps?.size ?? 0) + 4 < before.tiles))
        U.mapEvents.push([Date.now(), id, reset ? 'manual_view_reset' : 'map_replaced', before.version, snapshot?.version, before.tiles, this.tileBitmaps?.size ?? 0]);
      return out;
    };
  }).catch(e => U.errors.push([Date.now(), 'metric-map-hook', String(e)]));
  if (document.readyState === 'complete') hookMaps(); else window.addEventListener('load', hookMaps);

  // ---- export
  // Milestones and counters of the page load (called once the load part is over; the request list comes from the runner).
  U.takeLoad = () => ({t: Date.now(), time_origin: performance.timeOrigin, milestones: {...U.milestones},
    ws: JSON.parse(JSON.stringify(U.ws)), ready_state: document.readyState});
  // Per-sample rings (called every few seconds during the live phase).
  U.drain = (final = false) => {
    // Control rows leave in request order; one still waiting for its answer keeps the later ones back until the last drain.
    let done = final ? -1 : U.control.findIndex(r => r.p_done === null);
    if (done < 0) done = U.control.length;
    const out = {t: Date.now(), vis: document.visibilityState, ws: JSON.parse(JSON.stringify(U.ws)), arrivals: U.arrivals, renders: U.renders,
      canvas: Object.fromEntries(Object.entries(U.canvas).map(([k, v]) => [k, v.frames])), frames: U.frames,
      longtasks: U.longtasks, loaf: JSON.parse(JSON.stringify(U.loaf)), resources: U.resources, errors: U.errors.splice(0), lag: U.lag,
      mapEvents: U.mapEvents.splice(0), visEvents: U.vis.splice(0), handler: U.handler, status: JSON.parse(JSON.stringify(U.status)),
      heap: performance.memory ? performance.memory.usedJSHeapSize : null, dom: document.getElementsByTagName('*').length,
      frameTimes: U.frameTimes, marks: U.marks.splice(0), time_origin: performance.timeOrigin,
      control: U.control.splice(0, done), control_timings: controlTimings.splice(0)};
    U.arrivals = {}; U.renders = {}; U.longtasks = []; U.resources = {}; U.lag = []; U.frameTimes = []; U.handler = {};
    return out;
  };
})();
