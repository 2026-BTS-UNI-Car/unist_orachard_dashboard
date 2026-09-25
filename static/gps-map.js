import * as maplibregl from './vendor/maplibre-gl/maplibre-gl.mjs';
import {FollowModel, validFix, coordinates, frameAt, pathFeatures, accuracyCircle} from './gps-map-model.mjs';

const byId = id => document.getElementById(id);
const text = (id, value) => { const element = byId(id); if (element) element.textContent = value; };
const number = (value, digits=2) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const empty = () => ({type:'FeatureCollection', features:[]});
const protocol = new window.pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', protocol.tile);
const qualityName = {autonomous:'일반 GNSS', dgps:'DGNSS', rtk_float:'RTK FLOAT', rtk_fixed:'RTK FIX', dead_reckoning:'추정 위치', no_fix:'위치 미확정'};
let bundlePromise;
function bundle() {
  if (!bundlePromise) bundlePromise = fetch('/api/maps/status').then(r => r.json());
  return bundlePromise;
}

async function localStyle() {
  const theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  const style = await fetch(`/static/gps-style-${theme}.json`).then(r => r.json());
  style.sources.basemap.url = `pmtiles://${location.origin}/maps/korea.pmtiles`;
  style.glyphs = `${location.origin}/maps/fonts/{fontstack}/{range}.pbf`;
  style.sprite = `${location.origin}/maps/sprites/v4/${theme}`;
  return style;
}

class GpsMapView {
  constructor(prefix, archive=false) {
    this.prefix = prefix; this.archive = archive;
    this.model = new FollowModel(); this.map = null; this.marker = null;
    this.sample = {}; this.frames = []; this.until = Infinity; this.lastTrackKey = '';
    this.initializing = null; this.lastCentered = '';
    byId(`${prefix}Follow`)?.addEventListener('click', () => {
      const center = this.model.recenter();
      if (center && this.map) this.map.jumpTo({center});
      this.renderFollow();
    });
    this.renderFollow();
  }
  renderFollow() {
    const button = byId(`${this.prefix}Follow`);
    if (!button) return;
    button.setAttribute('aria-pressed', String(this.model.follow));
    button.classList.toggle('active', this.model.follow);
    button.textContent = this.model.follow ? '로버 중심 추적 중' : '로버 중심으로';
  }
  async init() {
    if (this.map) { this.map.resize(); return; }
    if (this.initializing) return this.initializing;
    this.initializing = this.create().catch(error => {
      text(`${this.prefix}MapStatus`, `지도를 불러오지 못했습니다 · ${error.message}`);
      this.initializing = null;
    });
    return this.initializing;
  }
  async create() {
    const info = await bundle();
    if (!info.ok) throw new Error(info.error || '오프라인 지도 파일 확인 필요');
    const font = new FontFace('UBTS Map Korean', 'url(/maps/fonts/korean.woff2)', {weight:'400'});
    document.fonts.add(await font.load());
    const style = await localStyle();
    this.map = new maplibregl.Map({container:`${this.prefix}Map`, style,
      center:[129.2, 35.55], zoom:11, minZoom:5, maxZoom:20,
      maxBounds:[[123.8,32.5],[132.3,39.1]],
      localIdeographFontFamily:'UBTS Map Korean',
      dragRotate:false, pitchWithRotate:false, touchPitch:false,
      attributionControl:true, fadeDuration:0});
    this.map.addControl(new maplibregl.NavigationControl({showCompass:false}), 'top-right');
    this.map.addControl(new maplibregl.ScaleControl({unit:'metric'}), 'bottom-left');
    this.map.on('dragstart', () => { this.model.userPan(); this.renderFollow(); });
    this.map.on('boxzoomstart', () => { this.model.userPan(); this.renderFollow(); });
    this.map.on('style.load', () => { this.addOverlays(); this.paint(); });
    this.map.on('error', event => {
      text(`${this.prefix}MapStatus`, `지도 일부를 표시하지 못했습니다 · ${event.error?.message || '파일 확인 필요'}`);
    });
    const element = document.createElement('div'); element.className = 'gps-rover-marker';
    element.innerHTML = '<span class="gps-marker-arrow"></span><span class="gps-marker-dot"></span>';
    element.setAttribute('role', 'img'); element.setAttribute('aria-label', '로버 GPS 위치');
    this.marker = new maplibregl.Marker({element, anchor:'center'});
    this.markerElement = element;
    this.resizeObserver = new ResizeObserver(() => this.map?.resize());
    this.resizeObserver.observe(byId(`${this.prefix}Map`));
    text(`${this.prefix}MapStatus`, `오프라인 지도 · 대한민국 전역 · ${info.data_date || '날짜 확인 중'} · ${(info.bytes/1048576).toFixed(0)} MiB`);
  }
  addOverlays() {
    for (const id of ['gps-accuracy', 'gps-track']) this.map.addSource(id, {type:'geojson', data:empty()});
    this.map.addLayer({id:'gps-accuracy-fill', type:'fill', source:'gps-accuracy', paint:{'fill-color':'#20b5cf','fill-opacity':.15}});
    this.map.addLayer({id:'gps-accuracy-line', type:'line', source:'gps-accuracy', paint:{'line-color':'#20b5cf','line-width':1}});
    this.map.addLayer({id:'gps-track-line', type:'line', source:'gps-track', paint:{'line-color':'#00b4cf','line-width':3,'line-opacity':.8}});
    this.lastTrackKey = '';
  }
  update(sample, frames, until=Infinity) {
    this.sample = sample || {}; this.frames = frames || []; this.until = until;
    this.model.update(this.sample);
    this.paint();
    const sampleValid = validFix(this.sample);
    text(`${this.prefix}Position`, sampleValid ? `${number(this.sample.latitude, 7)}, ${number(this.sample.longitude, 7)}` : this.model.position ? `마지막 위치 ${number(this.model.position[1], 7)}, ${number(this.model.position[0], 7)}` : 'GPS 위치 수신 대기');
    text(`${this.prefix}Quality`, sampleValid ? `${qualityName[this.sample.fix_type] || 'GNSS'} · 위성 ${this.sample.satellites ?? '—'}개` : this.sample.state === 'stale' ? '수신 끊김 · 마지막 위치' : this.sample.state === 'no_fix' ? `위치 미확정 · 위성 ${this.sample.satellites ?? '—'}개` : 'GPS 데이터 없음');
    text(`${this.prefix}Accuracy`, sampleValid && this.sample.h_acc_m != null ? `수평 ±${number(this.sample.h_acc_m, 3)} m` : '정확도 미보고');
    text(`${this.prefix}Speed`, sampleValid && this.sample.speed_mps != null ? `${number(this.sample.speed_mps*3.6, 1)} km/h` : '— km/h');
    text(`${this.prefix}Altitude`, sampleValid ? `해발 ${number(this.sample.altitude_msl_m, 1)} m · HDOP ${number(this.sample.hdop, 1)}` : '현재 위치가 확정되면 표시됩니다.');
    const badge = byId(`${this.prefix}FixState`);
    if (badge) { badge.dataset.state = sampleValid ? 'fix' : 'waiting'; badge.textContent = sampleValid ? qualityName[this.sample.fix_type] || 'GNSS' : '수신 대기'; }
  }
  paint() {
    if (!this.map || !this.marker) return;
    const point = this.model.position;
    if (point) {
      this.marker.setLngLat(point).addTo(this.map);
      this.markerElement.classList.toggle('stale', !this.model.fresh);
      const heading = this.model.fresh && this.sample.speed_mps > .3 && Number.isFinite(this.sample.course_deg);
      this.markerElement.classList.toggle('has-course', heading);
      this.markerElement.style.setProperty('--gps-course', `${heading ? this.sample.course_deg : 0}deg`);
      const centerKey = point.join(',');
      // Repeated status frames must not cancel a user's drag/zoom animation.
      if (this.model.follow && this.lastCentered !== centerKey) this.map.jumpTo({center:point});
      this.lastCentered = centerKey;
    } else this.marker.remove();
    const accuracy = this.map.getSource('gps-accuracy');
    if (accuracy) accuracy.setData(accuracyCircle(point, this.model.fresh ? this.sample.h_acc_m : null));
    const track = this.map.getSource('gps-track');
    const trackKey = `${this.frames.length}:${this.frames.at(-1)?.t_s}:${this.sample.received_at}:${this.until}`;
    if (track && trackKey !== this.lastTrackKey) {
      track.setData(pathFeatures(this.frames, this.until)); this.lastTrackKey = trackKey;
    }
  }
  reset() { this.model = new FollowModel(); this.frames = []; this.marker?.remove(); this.lastTrackKey = ''; this.lastCentered = ''; this.renderFollow(); }
  async theme() { if (this.map) this.map.setStyle(await localStyle()); }
}

const live = new GpsMapView('roverGps');
const archive = new GpsMapView('roverArchiveGps', true);
let socket = null, reconnect = null, latest = null, receivedAt = 0, frames = [], frameKey = '', archiveId = null;
const active = () => location.pathname.replace(/\/$/, '') === '/rover-control' && !document.hidden;
function closeSocket() {
  clearTimeout(reconnect); reconnect = null;
  const current = socket; socket = null;
  if (current) { current.onclose = null; current.close(); }
}
function paintLive() {
  if (!latest || !active()) return;
  const age = (latest.age_s ?? 0) + (Date.now()-receivedAt)/1000;
  const sample = age > 3 ? {...latest, valid:false, state:'stale'} : latest;
  live.update(sample, frames);
  const driver = latest.driver || {};
  text('roverGpsConnection', driver.error || `${driver.serial_open ? 'UART 열림' : 'UART 대기'} · ${driver.baud || '—'} baud · 수신 ${driver.bytes_received ?? 0} bytes · 유효 패킷 ${driver.valid_packets ?? 0}`);
  text('roverGpsAge', latest.received_at ? `${number(age, 1)}초 전 GPS 표본` : 'GPS 표본 수신 전');
}
function connect() {
  if (!active() || socket) return;
  let profile = 'tiny';
  try { profile = localStorage.getItem('ubts-live-network-profile') || 'tiny'; } catch (_) {}
  if (profile === 'auto') profile = 'tiny';
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/gps?profile=${encodeURIComponent(profile)}`);
  socket = ws;
  ws.onmessage = event => {
    if (socket !== ws) return;
    try {
      const payload = JSON.parse(event.data);
      if (payload.type !== 'gps') return;
      latest = payload; receivedAt = Date.now();
      const key = `${payload.received_at}:${payload.sequence}`;
      if (key !== frameKey && payload.received_at) {
        frames.push({t_s:payload.received_at, sample:payload});
        if (frames.length > 6000) frames.splice(0, frames.length-6000);
        frameKey = key;
      }
      paintLive();
      if (payload.stream_seq !== undefined && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ack:payload.stream_seq}));
    } catch (_) { text('roverGpsConnection', 'GPS 메시지 확인 실패'); }
  };
  ws.onclose = () => { if (socket === ws) { socket = null; if (active()) reconnect = setTimeout(connect, 1500); } };
  ws.onerror = () => text('roverGpsConnection', 'GPS 브리지 연결 대기');
}
function routeChanged() {
  if (active()) { live.init(); connect(); }
  else closeSocket();
}
window.RoverGps = {
  routeChanged,
  profileChanged() { closeSocket(); connect(); },
  archive(frames, seconds, id, enabled=false) {
    const panel = byId('roverArchiveGpsPanel');
    if (!panel) return;
    panel.hidden = !enabled && !frames?.length;
    if (panel.hidden) return;
    if (archiveId !== id) { archiveId = id; archive.reset(); }
    archive.init();
    const frame = frameAt(frames || [], seconds);
    if (!frame || !validFix(frame.sample)) archive.model.position = null;
    archive.update(frame?.sample || {valid:false, state:'no_data'}, frames || [], frame?.t_s ?? Math.floor(seconds));
    text('roverArchiveGpsTime', frame ? `GPS ${number(frame.t_s, 2)}s · 재생 ${number(seconds, 2)}s` : `재생 ${number(seconds, 2)}s · 동기 GPS 표본 없음`);
  },
};
new MutationObserver(() => { live.theme().catch(console.error); archive.theme().catch(console.error); }).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
document.addEventListener('visibilitychange', routeChanged);
window.addEventListener('pagehide', closeSocket);
setInterval(paintLive, 500);
routeChanged();
