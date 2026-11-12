#!/usr/bin/env python3
"""TCP link emulator between the browser and the dashboard (HTTP and WebSocket alike).

One emulated link stands for one client device on the rover's 2.4 GHz access
point. All TCP connections of that client share one bottleneck per direction:

* serialization at the configured bit rate,
* half of the round-trip time as one-way delay per direction,
* a bounded queue in front of the bottleneck (a full queue pushes back on the
  sender instead of dropping),
* one round trip of setup delay per new TCP connection,
* optional periodic stalls during which nothing crosses the link in either
  direction (how a short radio outage looks to TCP once retransmission has
  hidden the loss).

Data is cut into segments of at most SEGMENT_BYTES so that connections
interleave at packet granularity, and senders that wait for queue space are
served in arrival order.

Limits: this is a TCP relay. There is no packet loss, no retransmission
back-off, no congestion-window collapse after a stall, no rate adaptation and
no per-flow fair queueing; the bottleneck is one first-in-first-out queue.

  python link_emulator.py --upstream 127.0.0.1:28080 --stats link.json \
      --link name=c0,port=29100,rate_kbps=500,rtt_ms=150,queue_bytes=16384,stall_every_s=20,stall_ms=1000
"""
import argparse
import asyncio
import collections
import json
import signal
import time

try:  # Windows: 1 ms timer resolution for the emulated delays.
    import ctypes
    ctypes.WinDLL("winmm").timeBeginPeriod(1)
except Exception:  # not Windows, or the call is unavailable
    pass

SEGMENT_BYTES = 1460
BURST_CREDIT_S = 0.004  # timer overshoot that may be made up, so the mean rate stays exact
READ_LIMIT = 8192       # asyncio stream buffer high-water mark per connection
_SLEEPERS = None


async def precise_sleep(delay):
    """asyncio timers on Windows follow the system tick; short waits sleep on a thread instead."""
    global _SLEEPERS
    if delay <= 0:
        return
    if delay >= 0.03:
        await asyncio.sleep(delay)
        return
    if _SLEEPERS is None:
        import concurrent.futures
        _SLEEPERS = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="link-sleep")
    await asyncio.get_running_loop().run_in_executor(_SLEEPERS, time.sleep, delay)


class StallSchedule:
    """Periodic outage windows: [origin + first + k * every, ... + duration) for k = 0, 1, 2 ..."""

    def __init__(self, every_s=0.0, duration_s=0.0, first_s=None, origin=0.0):
        self.every, self.duration = float(every_s or 0.0), float(duration_s or 0.0)
        if self.every < 0 or self.duration < 0 or (self.enabled and self.duration >= self.every):
            raise ValueError("a stall must be shorter than its period")
        self.first = self.every / 2 if first_s is None else float(first_s)
        self.origin = float(origin)

    @property
    def enabled(self):
        return self.every > 0 and self.duration > 0

    def window_at(self, t):
        """The (start, end) window that contains t, or None."""
        if not self.enabled:
            return None
        offset = t - self.origin - self.first
        if offset < 0:
            return None
        start = self.origin + self.first + (offset // self.every) * self.every
        return (start, start + self.duration) if t < start + self.duration else None

    def next_start(self, t):
        """Start of the first window that begins at or after t (None when disabled)."""
        if not self.enabled:
            return None
        offset = t - self.origin - self.first
        k = 0 if offset <= 0 else -(-offset // self.every)
        return self.origin + self.first + k * self.every

    def finish_time(self, start, seconds):
        """When a transmission of `seconds` that may begin at `start` ends, pausing inside windows."""
        t, remaining = start, seconds
        for _ in range(100000):
            window = self.window_at(t)
            if window:
                t = window[1]
                continue
            upcoming = self.next_start(t)
            if upcoming is None or t + remaining <= upcoming:
                return t + remaining
            remaining -= upcoming - t
            t = upcoming
        raise RuntimeError("stall schedule did not converge")

    def windows_between(self, t0, t1):
        if not self.enabled or t1 <= t0:
            return []
        out, start = [], self.next_start(t0 - self.duration)
        while start is not None and start < t1:
            if start + self.duration > t0:
                out.append((start, start + self.duration))
            start += self.every
        return out


class Pipe:
    """One direction of one link: bounded FIFO, bottleneck rate, one-way delay."""

    def __init__(self, bps, one_way_s, queue_bytes, stalls):
        self.bps, self.one_way_s, self.queue_bytes, self.stalls = float(bps), one_way_s, int(queue_bytes), stalls
        self.queue = collections.deque()
        self.waiters = collections.deque()
        self.queued = 0
        self.free_at = 0.0
        self.wakeup = asyncio.Event()
        self.bytes = 0
        self.max_queued = 0
        self.max_queue_delay = 0.0
        self.samples = collections.deque(maxlen=3600)  # (second, bytes in that second)
        self._sec, self._sec_bytes = int(time.time()), 0

    async def put(self, delivery, data):
        # Waiting senders are admitted one at a time in arrival order, so a busy
        # connection cannot keep a later one out of the queue.
        if self.waiters or self.queued >= self.queue_bytes:
            turn = asyncio.get_running_loop().create_future()
            self.waiters.append(turn)
            await turn
        self.queue.append((delivery, data, time.monotonic()))
        self.queued += len(data or b"")
        self.max_queued = max(self.max_queued, self.queued)
        self.wakeup.set()

    def put_close(self, delivery):
        """Close marker: it has no size, so it needs no admission and never waits."""
        self.queue.append((delivery, None, time.monotonic()))
        self.wakeup.set()

    def _release(self, size):
        self.queued -= size
        while self.waiters and self.queued < self.queue_bytes:
            turn = self.waiters.popleft()
            if not turn.done():
                turn.set_result(None)
                break

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            while not self.queue:
                self.wakeup.clear()
                await self.wakeup.wait()
            delivery, data, enqueued = self.queue.popleft()
            if delivery.dead:
                self._release(len(data or b""))
                continue
            if data is None:  # close marker: travels behind everything queued before it
                delivery.schedule(max(loop.time(), self.free_at) + self.one_way_s, None)
                self._release(0)
                continue
            now = loop.time()
            start = max(self.free_at, now - BURST_CREDIT_S)
            self.free_at = self.stalls.finish_time(start, len(data) * 8.0 / self.bps)
            wait = self.free_at - now
            if wait > 0.001:
                await precise_sleep(wait)
            self._release(len(data))
            self.max_queue_delay = max(self.max_queue_delay, time.monotonic() - enqueued)
            self.bytes += len(data)
            sec = int(time.time())
            if sec != self._sec:
                self.samples.append((self._sec, self._sec_bytes))
                self._sec, self._sec_bytes = sec, 0
            self._sec_bytes += len(data)
            delivery.schedule(max(loop.time(), self.free_at) + self.one_way_s, data)

    def stats(self):
        series = list(self.samples) + [(self._sec, self._sec_bytes)]
        return dict(bytes=self.bytes, max_queued=self.max_queued, max_queue_delay_s=round(self.max_queue_delay, 3),
                    per_second=[[sec, count] for sec, count in series])  # [wall-clock second, bytes that crossed in it]


class Delivery:
    """In-order delivery of one connection direction after the one-way delay."""

    def __init__(self, writer):
        self.writer = writer
        self.items = collections.deque()
        self.event = asyncio.Event()
        self.closed = False
        self.dead = False
        self.task = asyncio.ensure_future(self.run())

    def schedule(self, at, data):
        self.items.append((at, data))
        self.event.set()

    async def run(self):
        loop = asyncio.get_running_loop()
        try:
            while True:
                while not self.items:
                    if self.closed:
                        self.writer.close()
                        return
                    self.event.clear()
                    await self.event.wait()
                at, data = self.items.popleft()
                if data is None:
                    self.closed = True
                    continue
                delay = at - loop.time()
                if delay > 0.0003:
                    await precise_sleep(delay)
                self.writer.write(data)
                await self.writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.dead = True
            self.items.clear()
            try:
                self.writer.close()
            except Exception:
                pass


class Link:
    def __init__(self, name, port, upstream, *, rate_kbps, rtt_ms, queue_bytes, up_rate_kbps=None, setup_rtts=1,
                 stall_every_s=0.0, stall_ms=0.0, stall_first_s=None, host="127.0.0.1"):
        self.name, self.port, self.upstream, self.host = name, int(port), upstream, host
        self.setup_rtts = int(setup_rtts)
        self.rtt = float(rtt_ms) / 1000.0
        self.rate_kbps, self.up_rate_kbps = float(rate_kbps), float(up_rate_kbps or rate_kbps)
        self.queue_bytes = int(queue_bytes)
        self.stall_args = (float(stall_every_s or 0.0), float(stall_ms or 0.0) / 1000.0, stall_first_s)
        self.connections = 0
        self.active = 0
        self.server = None
        self.tasks = []
        self.writers = set()

    async def start(self):
        loop = asyncio.get_running_loop()
        self.started_loop, self.started_wall = loop.time(), time.time()
        self.stalls = StallSchedule(*self.stall_args, origin=self.started_loop)
        self.down = Pipe(self.rate_kbps * 1000.0, self.rtt / 2, self.queue_bytes, self.stalls)
        self.up = Pipe(self.up_rate_kbps * 1000.0, self.rtt / 2, self.queue_bytes, self.stalls)
        self.tasks = [asyncio.ensure_future(self.down.run()), asyncio.ensure_future(self.up.run())]
        self.server = await asyncio.start_server(self.handle, self.host, self.port, limit=READ_LIMIT)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self.server is not None:
            self.server.close()
        for writer in list(self.writers):
            try:
                writer.close()
            except Exception:
                pass
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def pump(self, reader, source_writer, pipe, delivery):
        try:
            while not delivery.dead:
                data = await reader.read(SEGMENT_BYTES)
                if not data or delivery.dead:
                    break
                await pipe.put(delivery, data)
            if delivery.dead:  # receiver gone: stop the sender instead of draining into the link
                source_writer.close()
        except (ConnectionError, OSError):
            pass
        finally:
            pipe.put_close(delivery)

    async def handle(self, client_reader, client_writer):
        self.connections += 1
        self.active += 1
        opened = {client_writer}
        self.writers.add(client_writer)
        try:
            loop = asyncio.get_running_loop()
            if self.setup_rtts and self.rtt:
                # The handshake needs a round trip on a link that is up.
                ready = self.stalls.finish_time(loop.time(), self.rtt * self.setup_rtts)
                await precise_sleep(ready - loop.time())
            host, port = self.upstream
            server_reader, server_writer = await asyncio.open_connection(host, port, limit=READ_LIMIT)
            opened.add(server_writer)
            self.writers.add(server_writer)
            to_client = Delivery(client_writer)
            to_server = Delivery(server_writer)
            await asyncio.gather(self.pump(server_reader, server_writer, self.down, to_client),
                                 self.pump(client_reader, client_writer, self.up, to_server))
            await asyncio.gather(to_client.task, to_server.task, return_exceptions=True)
        except (ConnectionError, OSError):
            client_writer.close()
        finally:
            self.active -= 1
            self.writers -= opened

    def stats(self):
        loop_now = asyncio.get_running_loop().time()
        to_wall = self.started_wall - self.started_loop
        stalls = [[round(a + to_wall, 3), round(b + to_wall, 3)] for a, b in self.stalls.windows_between(self.started_loop, loop_now)]
        return dict(name=self.name, port=self.port, rtt_ms=round(self.rtt * 1000), setup_rtts=self.setup_rtts,
                    rate_kbps=self.rate_kbps, up_rate_kbps=self.up_rate_kbps, queue_bytes=self.queue_bytes,
                    segment_bytes=SEGMENT_BYTES, stall_every_s=self.stalls.every, stall_ms=round(self.stalls.duration * 1000),
                    stall_first_s=self.stalls.first if self.stalls.enabled else None, started_wall=round(self.started_wall, 3),
                    stalls_wall=stalls, connections=self.connections, active=self.active,
                    down=self.down.stats(), up=self.up.stats())


def parse_link(spec, upstream):
    """name=c0,port=29100,rate_kbps=500,rtt_ms=150,queue_bytes=16384[,up_rate_kbps=..][,setup_rtts=1][,stall_every_s=20,stall_ms=1000,stall_first_s=10]"""
    fields = dict(part.split("=", 1) for part in spec.split(",") if part)
    name, port = fields.pop("name"), int(fields.pop("port"))
    allowed = {"rate_kbps", "rtt_ms", "queue_bytes", "up_rate_kbps", "setup_rtts", "stall_every_s", "stall_ms", "stall_first_s"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown link field(s): {sorted(unknown)}")
    return Link(name, port, upstream, **{key: float(value) for key, value in fields.items()})


def link_spec(name, port, profile):
    """Command-line form of one profile entry of profiles.json."""
    keys = ("rate_kbps", "rtt_ms", "queue_bytes", "up_rate_kbps", "setup_rtts", "stall_every_s", "stall_ms", "stall_first_s")
    parts = [f"name={name}", f"port={port}"] + [f"{key}={profile[key]}" for key in keys if profile.get(key) is not None]
    return ",".join(parts)


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--upstream", required=True, help="host:port of the dashboard under test")
    parser.add_argument("--link", action="append", required=True, help=parse_link.__doc__)
    parser.add_argument("--stats", required=True, help="JSON file rewritten once per second")
    args = parser.parse_args(argv)
    host, port = args.upstream.rsplit(":", 1)
    if host != "127.0.0.1":
        raise SystemExit("the emulator only relays to 127.0.0.1")
    links = [await parse_link(spec, (host, int(port))).start() for spec in args.link]
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        payload = json.dumps(dict(t=time.time(), links=[link.stats() for link in links]))
        with open(args.stats, "wb") as out:
            out.write(payload.encode("utf-8"))
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except asyncio.TimeoutError:
            pass
    for link in links:
        await link.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
