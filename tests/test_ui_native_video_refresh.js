const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = require('./helpers/app_source').readAppSource();
const begin = source.indexOf('function configureArchiveNativeVideo('), end = source.indexOf('function archiveRenderClock(', begin);
assert.ok(begin >= 0 && end > begin);
function fixture(playing) {
  const events = [], video = {duration: 50, currentTime: 0,
    closest: () => ({classList: {toggle() {}}}), pause: () => events.push('pause'),
    removeAttribute() {}, load: () => events.push('load'), play: async () => events.push('play')};
  const state = {recordings: {selectedId: 'r'}, archivePlayback: {playing, timeS: 20, durationS: 40,
    nativeVideoUrl: 'old.mp4', nativeVideoOffsetS: 0, videoPlayer: {destroy: () => events.push('destroy')}}};
  const context = {state, Number, String, Math, Promise, fmtNumber: String,
    $: id => id === 'roverArchiveCameraVideo' ? video : null, document: {},
    window: {ArchiveVideoPlayer: class {constructor() {events.push('player');} destroy() {events.push('destroy');}}},
    syncArchiveScrubberBounds() {}, renderArchiveFrame() {}, stopArchivePlayback: () => events.push('stop'),
    archiveNativeVideo: () => video, scheduleArchiveSeek() {}};
  const seekBegin = source.indexOf('function setArchiveTime('), seekEnd = source.indexOf('function renderArchiveFrame(', seekBegin);
  vm.createContext(context); vm.runInContext(source.slice(begin, end) + source.slice(seekBegin, seekEnd), context);
  return {context, state, events, video};
}
(async () => {
  {
    const f = fixture(true);
    const payload = {id: 'r', video_url: 'new.mp4', source_video: {timeline_offset_s: 5}};
    f.context.configureArchiveNativeVideo(payload, {preservePlayback: true});
    const player = f.state.archivePlayback.videoPlayer;
    // A second list/detail refresh finishes before the new URL's metadata.
    f.context.configureArchiveNativeVideo({...payload, metadata: {state: 'archived'}}, {preservePlayback: true});
    f.video.onloadedmetadata(); await Promise.resolve();
    assert.equal(f.events.filter(e => e === 'play').length, 1,
      'pending source resume survives another same-URL metadata refresh');
    assert.equal(f.state.archivePlayback.videoPlayer, player, 'same URL must not replace the pending player');
    assert.equal(f.state.archivePlayback.timeS, 20); assert.equal(f.video.currentTime, 15);
  }
  {
    const f = fixture(true), payload = {video_url: 'new.mp4', source_video: {timeline_offset_s: 5}};
    f.context.configureArchiveNativeVideo(payload, {preservePlayback: true});
    f.context.setArchiveTime(30);
    f.context.configureArchiveNativeVideo(payload, {preservePlayback: true});
    f.video.onloadedmetadata(); await Promise.resolve();
    assert.equal(f.state.archivePlayback.timeS, 30); assert.equal(f.video.currentTime, 25);
    assert.equal(f.events.filter(e => e === 'play').length, 1, 'explicit seek survives the additional refresh');
  }
  {
    const f = fixture(true), payload = {video_url: 'new.mp4'};
    f.context.configureArchiveNativeVideo(payload, {preservePlayback: true});
    f.state.archivePlayback.playing = false; f.state.archivePlayback.videoPlayRevision++;
    f.context.configureArchiveNativeVideo(payload, {preservePlayback: true});
    f.video.onloadedmetadata(); await Promise.resolve();
    assert.ok(!f.events.includes('play'), 'Pause wins after the additional pending refresh');
    assert.equal(f.state.archivePlayback.timeS, 20);
  }
  {
    for (const resume of [false, true]) {
      const f = fixture(true); let reject;
      f.video.play = () => new Promise((_, fail) => {reject = fail;});
      f.context.configureArchiveNativeVideo({video_url: 'new.mp4'}, {preservePlayback: true});
      f.video.onloadedmetadata();
      f.state.archivePlayback.playing = false;
      f.state.archivePlayback.videoPlayRevision++;
      if (resume) {f.state.archivePlayback.playing = true; f.state.archivePlayback.videoPlayRevision++;}
      reject(Object.assign(new Error('old request interrupted'), {name: 'AbortError'}));
      await new Promise(done => setImmediate(done));
      assert.ok(!f.events.includes('stop'), 'an obsolete restore play rejection cannot stop a newer Pause/Play');
      assert.equal(f.state.archivePlayback.nativeVideoError, '');
      assert.equal(f.state.archivePlayback.playing, resume);
    }
  }
  {
    const f = fixture(true);
    f.context.configureArchiveNativeVideo({video_url: 'new.mp4', source_video: {timeline_offset_s: 5}}, {preservePlayback: true});
    f.context.setArchiveTime(30);
    assert.equal(f.state.archivePlayback.sourceSwitchTimeS, 30, 'explicit seek replaces the pending metadata cursor');
    f.video.onloadedmetadata(); await Promise.resolve();
    assert.equal(f.state.archivePlayback.timeS, 30); assert.equal(f.video.currentTime, 25);
  }
  {
    const f = fixture(true);
    f.context.configureArchiveNativeVideo({id: 'r', video_url: 'old.mp4'}, {preservePlayback: true});
    assert.deepEqual(f.events, [], 'the same media URL must keep the existing player');
    f.context.configureArchiveNativeVideo({id: 'r', video_url: 'new.mp4', source_video: {timeline_offset_s: 5}}, {preservePlayback: true});
    assert.equal(f.state.archivePlayback.sourceSwitchTimeS, 20);
    f.video.onloadedmetadata(); await Promise.resolve();
    assert.equal(f.video.currentTime, 15); assert.equal(f.state.archivePlayback.timeS, 20);
    assert.equal(f.state.archivePlayback.playing, true); assert.equal(f.events.filter(e => e === 'play').length, 1);
  }
  {
    const f = fixture(true);
    f.context.configureArchiveNativeVideo({video_url: 'new.mp4'}, {preservePlayback: true});
    f.state.archivePlayback.playing = false; f.video.onloadedmetadata();
    assert.equal(f.video.currentTime, 20); assert.ok(!f.events.includes('play'), 'Pause while metadata loads must win');
  }
  {
    const f = fixture(true); f.state.archivePlayback.selectedSourceVideo = {width: 1920, height: 1080};
    f.context.configureArchiveNativeVideo({video_url: 'primary.mp4'}, {preservePlayback: true});
    assert.deepEqual(f.events, []); assert.equal(f.state.archivePlayback.nativeVideoUrl, 'old.mp4');
  }
  {
    const f = fixture(false);
    f.context.configureArchiveNativeVideo({video_url: 'first.mp4'}, {preservePlayback: true}); const obsolete = f.video.onloadedmetadata;
    f.context.configureArchiveNativeVideo({video_url: 'second.mp4'}, {preservePlayback: true});
    obsolete(); assert.equal(f.state.archivePlayback.nativeVideoReady, false, 'stale metadata must not complete a newer source');
    f.video.onloadedmetadata(); assert.equal(f.video.currentTime, 20); assert.equal(f.state.archivePlayback.nativeVideoReady, true);
  }
  console.log('Native archive metadata refresh preserves cursor, player, Pause, explicit source and stale-source guard: PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
