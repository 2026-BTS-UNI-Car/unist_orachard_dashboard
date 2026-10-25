// Runs the operator page's inline script (operator_http.PAGE) against a minimal fake DOM, fake timers and a
// scripted fetch; prints one JSON object with the observations of each scenario. Used by test_page_js.py.
// No browser, no network. Usage: node page_js_harness.js <page_script.js>
'use strict';
const fs = require('fs');
const vm = require('vm');

const CODE = fs.readFileSync(process.argv[2], 'utf8');

function statusBody(mode, extra) {
  return Object.assign({
    mode, shadow_auto: false, profile: 'field', state: 'PAUSED', ctl: [null, null], reason: 'await_hold',
    end_reason: null, vehicle_control: mode === 'SHADOW' ? 'none' : 'not_armed',
    stop_button: mode === 'SHADOW' ? 'ends_shadow_run_only' : 'vehicle_stop',
    log: {state: 'ok', dropped: 0, last_error: null}, loop_exceptions: 0, lidar_age: 0.05, imu_age: 0.02,
    counts: {scans_dropped: 0}, scan: {}, feasibility: {status: 'ok'}, onsite_limits: {}, params: null,
  }, extra || {});
}

function makeEnv(handler) {
  let now = 0;
  let nextId = 1;
  const timers = [];
  const elements = {};
  const requests = [];
  const listeners = {doc: {}, win: {}};
  function el(id) {
    if (!elements[id]) {
      const own = {};
      const set = new Set();
      elements[id] = {
        id, textContent: '', className: '', hidden: false, disabled: false, value: '', innerHTML: '',
        classList: {add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c)},
        addEventListener: (t, fn) => { (own[t] = own[t] || []).push(fn); },
        setPointerCapture: () => {},
        fire: (t) => { for (const fn of (own[t] || [])) fn({preventDefault() {}, pointerId: 1}); },
      };
    }
    return elements[id];
  }
  class FakeAbortController {
    constructor() { this.signal = {aborted: false, fns: [], addEventListener(t, fn) { this.fns.push(fn); }}; }
    abort() { this.signal.aborted = true; for (const fn of this.signal.fns) fn(); }
  }
  const sandbox = {
    console,
    document: {getElementById: el, hidden: false,
               addEventListener: (t, fn) => { (listeners.doc[t] = listeners.doc[t] || []).push(fn); }},
    window: {addEventListener: (t, fn) => { (listeners.win[t] = listeners.win[t] || []).push(fn); }},
    location: {reloads: 0, reload() { this.reloads += 1; }},
    crypto: {getRandomValues(a) { for (let i = 0; i < a.length; i++) a[i] = (i * 37 + nextId * 11) & 255; nextId++; return a; }},
    AbortController: FakeAbortController,
    setTimeout: (fn, ms) => { const id = nextId++; timers.push({id, at: now + (ms || 0), fn, every: 0}); return id; },
    clearTimeout: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    setInterval: (fn, ms) => { const id = nextId++; timers.push({id, at: now + ms, fn, every: ms}); return id; },
    clearInterval: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    fetch: (path, opts) => {
      const body = opts && opts.body ? JSON.parse(opts.body) : undefined;
      const req = {path, body, t: now};
      requests.push(req);
      const answer = handler(path, body, req);
      return new Promise((resolve, reject) => {
        const signal = opts && opts.signal;
        if (signal) signal.addEventListener('abort', () => reject(new Error('aborted')));
        const deliver = (a) => resolve({status: a.status, json: () => Promise.resolve(a.body)});
        if (answer === 'hang') return;
        if (answer && typeof answer.then === 'function') { answer.then(deliver); return; }
        deliver(answer);
      });
    },
  };
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  const env = {
    sandbox, el, requests, listeners,
    async flush() { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); },
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        const t = timers[0];
        if (!t || t.at > end) break;
        now = t.at;
        if (t.every) t.at += t.every; else timers.shift();
        t.fn();
        await env.flush();
      }
      now = end;
      await env.flush();
    },
    get now() { return now; },
  };
  vm.runInContext(CODE, sandbox);
  return env;
}

async function scenario(mode, stopAnswer, opts) {
  opts = opts || {};
  let statusAnswer = {status: 200, body: statusBody(mode, opts.status)};
  const env = makeEnv((path) => {
    if (path === '/api/status') return opts.statusAnswer || statusAnswer;
    if (path === '/api/stop') return stopAnswer;
    return {status: 200, body: {ok: true}};
  });
  await env.flush();
  await env.advance(600);                     // first status polls
  const before = {banner: env.el('banner').textContent, bannerClass: env.el('banner').className,
                  stop: env.el('stop').textContent, stopClass: env.el('stop').className,
                  hold: env.el('hold').textContent, alert: env.el('alert').textContent,
                  alertHidden: env.el('alert').hidden, holdDisabled: env.el('hold').disabled};
  let after = null;
  if (stopAnswer !== undefined) {
    env.el('stop').fire('click');
    await env.flush();
    await env.advance(3000);                  // STOP fetch timeout is 2.5 s
    after = {alert: env.el('alert').textContent, alertClass: env.el('alert').className,
             alertHidden: env.el('alert').hidden, reloads: env.sandbox.location.reloads,
             holdDisabled: env.el('hold').disabled};
    await env.advance(6000);                  // 403: reload after 5 s
    after.reloadsLater = env.sandbox.location.reloads;
  } else {
    await env.advance(6000);
    before.reloadsLater = env.sandbox.location.reloads;
    before.tableClass = env.el('tab').className;
    before.alertLater = env.el('alert').textContent;
  }
  return {before, after, requests: env.requests.map((r) => r.path)};
}

async function releaseOrder() {
  // one device: release then a quick new press; the new press's first beat must follow the release request
  let resolveRelease = null;
  const env = makeEnv((path) => {
    if (path === '/api/status') return {status: 200, body: statusBody('MOTOR')};
    if (path === '/api/release') return new Promise((r) => { resolveRelease = () => r({status: 200, body: {ok: true}}); });
    return {status: 200, body: {ok: true}};
  });
  await env.advance(600);
  const H = env.el('hold');
  H.fire('pointerdown');
  await env.flush();
  await env.advance(250);
  H.fire('pointerup');
  await env.flush();
  const releaseIndex = env.requests.length - 1;
  const firstPress = env.requests.filter((r) => r.path === '/api/hold')[0].body.press;
  H.fire('pointerdown');                      // new press while the release is still in flight
  await env.flush();
  const beatsWhilePending = env.requests.slice(releaseIndex + 1).filter((r) => r.path === '/api/hold').length;
  resolveRelease();
  await env.flush();
  await env.advance(150);
  const later = env.requests.slice(releaseIndex + 1).filter((r) => r.path === '/api/hold');
  // a hung release must not block the next press for longer than 0.7 s
  H.fire('pointerup');
  await env.flush();
  H.fire('pointerdown');
  await env.flush();
  const idx = env.requests.length;
  await env.advance(400);
  const beforeCap = env.requests.slice(idx).filter((r) => r.path === '/api/hold').length;
  await env.advance(500);
  const afterCap = env.requests.slice(idx).filter((r) => r.path === '/api/hold').length;
  return {releasePath: env.requests[releaseIndex].path, beatsWhilePending, beatsAfterRelease: later.length,
          newPressDiffers: later.length > 0 && later[0].body.press !== firstPress, beforeCap, afterCap};
}

(async () => {
  const out = {};
  out.shadow_stop = await scenario('SHADOW', {status: 200, body: {ok: true, stop_sent: null, shadow: true}});
  out.motor_ok = await scenario('MOTOR', {status: 200, body: {ok: true, stop_sent: true, shadow: false}});
  out.motor_502 = await scenario('MOTOR', {status: 502, body: {ok: true, stop_sent: false, error: 'timeout'}});
  out.motor_403 = await scenario('MOTOR', {status: 403, body: {ok: false, error: 'forbidden'}});
  out.motor_timeout = await scenario('MOTOR', 'hang');
  out.armed = await scenario('MOTOR', undefined, {status: {vehicle_control: 'armed'}});
  out.status_403 = await scenario('MOTOR', undefined, {statusAnswer: {status: 403, body: {ok: false}}});
  out.status_lost = await scenario('MOTOR', undefined, {statusAnswer: 'hang'});
  out.log_failing = await scenario('SHADOW', undefined,
                                   {status: {log: {state: 'failing', dropped: 12, last_error: 'OSError: [Errno 28]'}}});
  out.release_order = await releaseOrder();
  out.conflict = await (async () => {
    const env = makeEnv((path) => {
      if (path === '/api/status') return {status: 200, body: statusBody('MOTOR')};
      if (path === '/api/hold') return {status: 409, body: {ok: false, error: 'press_conflict'}};
      return {status: 200, body: {ok: true}};
    });
    await env.advance(600);
    env.el('hold').fire('pointerdown');
    await env.flush();
    await env.advance(300);
    return {alert: env.el('alert').textContent, alertClass: env.el('alert').className,
            holdOn: env.el('hold').classList.contains('on'),
            beats: env.requests.filter((r) => r.path === '/api/hold').length,
            releases: env.requests.filter((r) => r.path === '/api/release').length};
  })();
  out.calib = await (async () => {
    // calibration card (calib_runs.py): options from the status, bounded request bodies, results table
    const options = {floor_pct: 34, floor_source: 'breakaway_duty_pct', start_floor_pct: 34,
      start_floor_source: 'breakaway_duty_pct', base_pct: 55, arc_default: 'd10_d20', stop: {duty: 55, ok: true, why: null},
      arc_sets: [{id: 'd10', steps: [[55, 45], [45, 55]], ok: true, why: null},
                 {id: 'd10_d20', steps: [[55, 45], [45, 55], [55, 35], [35, 55]], ok: true, why: null},
                 {id: 'sym20', steps: [[65, 45], [45, 65]], ok: false, why: 'outer 65 above duty_cap_pct 62'}],
      rolldown: [{step_pct: 3, duties: [55, 52, 49, 46, 43, 40], ok: true, why: null}], distance_m: [2, 15], time_s: [5, 45]};
    const calib = {kind: 'arc', phase: 'ended', step_index: 3, steps_total: 4, step: null, counted_m: 9.1, distance_cap_m: 15,
      end_reason: 'calib_complete', results: [{i: 0, L: 55, R: 45, diff: -10, seconds: 3, gyro_yaw_dps: -1.5, lidar_yaw_dps: -1.48,
      speed_m_s: 0.42, gain_dps_per_pct: 0.148, valid: true}, {i: 1, L: 45, R: 55, diff: 10, seconds: 1, gyro_yaw_dps: 1.5,
      lidar_yaw_dps: null, speed_m_s: 0.4, gain_dps_per_pct: 0.15, valid: false}],
      outcome: {sides: {left: {gain: 0.15}, right: {gain: 0.148}}, full_diff_pct: 20, yaw_at_full_diff_dps: 2.96, extrapolated: false}};
    const env = makeEnv((path) => {
      if (path === '/api/status') return {status: 200, body: statusBody('MOTOR', {calib_options: options, calib: calib,
        row_motor: false, calib_allowed: true, yaw_params: {measured_yaw_at_max_diff_deg_s: 3.0, max_yaw_rate_deg_s: 1.8}})};
      return {status: 200, body: {ok: true}};
    });
    await env.advance(600);
    const planArc = env.el('calibplan').textContent;
    const filled = {set: env.el('c_set').value, pct: env.el('c_pct').value, dist: env.el('c_dist').value,
                    setOptions: env.el('c_set').innerHTML};
    env.el('c_dist').value = '8';
    env.el('calibstart').fire('click');
    await env.flush();
    env.el('c_kind').value = 'rolldown';
    env.el('calibstart').fire('click');
    await env.flush();
    await env.advance(600);
    const planRoll = env.el('calibplan').textContent;
    env.el('c_kind').value = 'stop';                       // day-2: C7 stop kind
    env.el('calibstart').fire('click');
    await env.flush();
    await env.advance(600);
    const planStop = env.el('calibplan').textContent;
    const stopCalib = {kind: 'stop', phase: 'ended', step_index: 0, steps_total: 1, step: null, counted_m: 3.2,
      distance_cap_m: 15, end_reason: 'calib_stop_test', results: [{i: 0, duty: 55, seconds: 3, speed_m_s: 0.45,
      min_speed_m_s: 0.43, stalled: false, steady: true}], outcome: {steady: true, speed_m_s: 0.45},
      stop: {done: true, ok: true, stop_distance_m: 0.31, v_at_zero_m_s: 0.45, time_to_rest_s: 1.2}};
    const envStop = makeEnv((path) => (path === '/api/status' ? {status: 200, body: statusBody('MOTOR',
      {calib_options: options, calib: stopCalib, row_motor: false, calib_allowed: true})} : {status: 200, body: {ok: true}}));
    await envStop.advance(600);
    const shadow = makeEnv((path) => (path === '/api/status' ? {status: 200, body: statusBody('SHADOW')} : {status: 200, body: {ok: true}}));
    await shadow.advance(600);
    return {cardHidden: env.el('calibcard').hidden, shadowCardHidden: shadow.el('calibcard').hidden, filled,
            plan: planRoll, planArc, planStop, tab: env.el('calibtab').innerHTML, out: env.el('calibout').textContent,
            stopOut: envStop.el('calibout').textContent, stopTab: envStop.el('calibtab').innerHTML,
            limits: env.el('limits').textContent, banner: env.el('banner').textContent,
            bodies: env.requests.filter((r) => r.path === '/api/calib_start').map((r) => r.body)};
  })();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
