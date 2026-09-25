export function coordinates(sample) {
  return sample && typeof sample.latitude === 'number' && typeof sample.longitude === 'number'
    && Number.isFinite(sample.latitude) && Number.isFinite(sample.longitude)
    && Math.abs(sample.latitude) <= 90 && Math.abs(sample.longitude) <= 180
    ? [sample.longitude, sample.latitude] : null;
}

export function validFix(sample) { return sample?.valid === true && Boolean(coordinates(sample)); }

export class FollowModel {
  constructor() { this.follow = true; this.position = null; this.fresh = false; }
  userPan() { this.follow = false; }
  recenter() { this.follow = true; return this.position; }
  update(sample) {
    this.fresh = validFix(sample);
    const point = coordinates(sample);
    if (this.fresh || (sample?.state === 'stale' && point)) this.position = point;
    return this.follow ? this.position : null;
  }
}

export function frameAt(frames, seconds, maxAge = 3) {
  let lo = 0, hi = frames.length - 1, index = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t_s <= seconds) { index = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (index < 0 || seconds-frames[index].t_s > maxAge) return null;
  return frames[index];
}

export function pathFeatures(frames, until = Infinity) {
  const features = [];
  let segment = [], lastTime = null;
  const finish = () => {
    if (segment.length > 1) features.push({type:'Feature', properties:{}, geometry:{type:'LineString', coordinates:segment}});
    segment = []; lastTime = null;
  };
  for (const frame of frames) {
    if (frame.t_s > until) break;
    if (!validFix(frame.sample)) { finish(); continue; }
    if (lastTime !== null && frame.t_s-lastTime > 5) finish();
    segment.push(coordinates(frame.sample)); lastTime = frame.t_s;
  }
  finish();
  return {type:'FeatureCollection', features};
}

export function accuracyCircle(point, meters) {
  if (!point || !Number.isFinite(meters) || meters <= 0 || meters > 100000) return {type:'FeatureCollection', features:[]};
  const radius = meters / 6371008.8, lat = point[1]*Math.PI/180, lon = point[0]*Math.PI/180;
  const ring = [];
  for (let i = 0; i <= 64; i++) {
    const bearing = i*Math.PI/32;
    const phi = Math.asin(Math.sin(lat)*Math.cos(radius)+Math.cos(lat)*Math.sin(radius)*Math.cos(bearing));
    const lambda = lon+Math.atan2(Math.sin(bearing)*Math.sin(radius)*Math.cos(lat),Math.cos(radius)-Math.sin(lat)*Math.sin(phi));
    ring.push([lambda*180/Math.PI, phi*180/Math.PI]);
  }
  return {type:'FeatureCollection', features:[{type:'Feature', properties:{}, geometry:{type:'Polygon', coordinates:[ring]}}]};
}
