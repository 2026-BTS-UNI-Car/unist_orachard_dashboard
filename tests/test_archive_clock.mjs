import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';
const text=(await import('node:module')).createRequire(import.meta.url)('./helpers/app_source.js').readAppSource();
const start=text.indexOf('function queueArchivePlaybackTick()');const end=text.indexOf('function stopArchivePlayback(',start);
const timers=new Map(),frames=new Map();let id=0,ticks=0;
const state={archivePlayback:{}};
const context=vm.createContext({state,performance:{now:()=>500},
  setTimeout:(cb,ms)=>{assert.equal(ms,80);timers.set(++id,cb);return id;},clearTimeout:id=>timers.delete(id),
  requestAnimationFrame:cb=>{frames.set(++id,cb);return id;},cancelAnimationFrame:id=>frames.delete(id),
  archivePlaybackTick:now=>{assert.equal(now,500);ticks++;}});
vm.runInContext(text.slice(start,end),context);
vm.runInContext('queueArchivePlaybackTick()',context);
const delayed=[...frames.values()][0];[...timers.values()][0]();
assert.equal(ticks,1);assert.equal(frames.size,0);assert.equal(timers.size,0);
delayed();assert.equal(ticks,1,'late rAF must not advance the clock twice');
vm.runInContext('queueArchivePlaybackTick()',context);
const timeout=[...timers.values()][0];[...frames.values()][0]();timeout();
assert.equal(ticks,2);assert.equal(timers.size,0);assert.equal(frames.size,0);
console.log('Media clock continues when rAF is delayed; timer/frame callbacks coalesce without duplicate ticks');
