"""Bounded historical native evidence tiles, never sensor/planner inputs.

Serialization runs on the separately owned map worker, outside the native
callback/fit worker. Two latest generations plus at most two fixed-lived client
pins are retained. GET never rasterizes or samples an original source clock.
"""
import base64
from collections import OrderedDict
import hashlib
import json
import math
import re
import secrets
import threading
import time

import numpy as np

PAGE_BYTES = 128 * 1024
MAX_TILES = 512
MAX_CELLS = 250000
GENERATION_BYTES = 4 * 1024 * 1024
CACHE_BYTES = 16 * 1024 * 1024
LEASE_SECONDS = 120.
PATH_VERTICES = 256
SCHEMA = 'ubts.native.accepted_map.tiles.v1'
FALSE_FLAGS = dict(runtime_authority=False, motor_command_sent=False,
                   autonomy_output_enabled=False, execution_eligible=False,
                   physical_accuracy_verified=False, live_input_eligible=False)


def require(value, reason):
    if not value:
        raise ValueError(reason)


def raw_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def encode_cells(states):
    """Exact existing 64x64 RLE2/packed2 encoding and compact-length tie rule."""
    require(len(states) == 4096 and all(type(v) is int and 0 <= v <= 3 for v in states),
            'invalid_tile_states')
    runs = []
    for value in states:
        if runs and runs[-2] == value:
            runs[-1] += 1
        else:
            runs.extend((value, 1))
    if len(raw_json(runs)) <= 1370:
        return dict(encoding='rle2-v1', cells=runs)
    packed = bytes(sum(states[i + j] << (2 * j) for j in range(4)) for i in range(0, 4096, 4))
    return dict(encoding='packed2-v1', cells=base64.b64encode(packed).decode('ascii'))


_PACK_WEIGHTS = np.array([1, 4, 16, 64], dtype=np.uint16)


def encode_tile_bytes(states):
    """encode_cells for one 4096-byte tile, same output, without per-cell Python work."""
    a = np.frombuffer(states, dtype=np.uint8)
    change = np.flatnonzero(a[1:] != a[:-1]) + 1
    starts = np.concatenate(([0], change))
    lengths = np.diff(np.concatenate((starts, [4096])))
    # Each run adds at least 4 JSON characters, so long run lists never fit.
    if 4 * len(starts) + 1 <= 1370:
        runs = np.empty(2 * len(starts), dtype=np.int64)
        runs[0::2] = a[starts]
        runs[1::2] = lengths
        runs = runs.tolist()
        if len(raw_json(runs)) <= 1370:
            return dict(encoding='rle2-v1', cells=runs)
    packed = (a.reshape(-1, 4).astype(np.uint16) * _PACK_WEIGHTS).sum(axis=1).astype(np.uint8).tobytes()
    return dict(encoding='packed2-v1', cells=base64.b64encode(packed).decode('ascii'))


def tile_facts(states):
    """Exact tile facts from its state bytes: (encoding, count, nonzero box, occupied box) in tile cells."""
    require(type(states) is bytes and len(states) == 4096, 'native_tile_states_required')
    a = np.frombuffer(states, dtype=np.uint8).reshape(64, 64)
    require(int(a.max()) <= 2, 'native_tile_states_required')

    def box(mask):
        if not mask.any():
            return None
        ys, xs = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
        return (int(xs[0]), int(ys[0]), int(xs[-1]), int(ys[-1]))
    return encode_tile_bytes(states), int(np.count_nonzero(a)), box(a != 0), box(a == 2)


class NativeMapTransport:
    def __init__(self, source, *, max_tiles=MAX_TILES, page_bytes=PAGE_BYTES, push_only=False, clock=time.monotonic):
        require(callable(getattr(source, 'display_snapshot', None)) and callable(getattr(source, 'status', None)),
                'complete_native_map_source_required')
        require(type(max_tiles) is int and 1 <= max_tiles <= MAX_TILES and
                type(page_bytes) is int and 4096 <= page_bytes <= PAGE_BYTES, 'native_tile_budgets_required')
        require(type(push_only) is bool and callable(clock), 'native_transport_mode_required')
        self._source = source
        self._lock = threading.RLock()
        self._generations = OrderedDict()
        self._leases = {}
        self._clock, self.push_only = clock, push_only
        self._last_status = {}
        self._reason = 'waiting_first_native_map'
        self._trail = []
        self._trail_compressions = 0
        self._clear_serial = 0
        # Encoded tiles of the previous complete generation, keyed by their
        # exact 4096-byte state content. Bounded to one generation's tiles.
        self._tile_cache = {}
        self.max_tiles, self.page_bytes = max_tiles, page_bytes

    def _build(self, snapshot, path=(), compressions=0):
        require(type(snapshot) is dict and snapshot.get('schema') == 'ubts.native.laser_local.complete_display.v1',
                'complete_native_display_schema_required')
        require(all(snapshot.get(k) is False for k in ('runtime_authority', 'motor_command_sent',
                'execution_eligible', 'physical_accuracy_verified', 'live_input_eligible')),
                'native_map_has_authority')
        version = snapshot.get('map_version')
        require(type(version) is int and 0 < version < 2**63 and type(snapshot.get('context')) is dict,
                'native_complete_generation_required')
        ctx = snapshot['context']
        require(set(ctx) == {'source_session', 'generation', 'source_clock_domain', 'receipt_clock_domain',
                            'map_epoch', 'map_frame'} and
                type(ctx['generation']) is int and 0 <= ctx['generation'] < 2**63 and
                all(type(ctx[k]) is str and 1 <= len(ctx[k]) <= 128 for k in ctx if k != 'generation'),
                'native_map_context_required')
        resolution = snapshot.get('resolution_m')
        require(type(resolution) in (int, float) and math.isfinite(resolution) and .02 <= resolution <= 1,
                'native_map_resolution_required')
        if snapshot.get('tiles') is not None:
            encoded, cell_count, (min_x, min_y, max_x, max_y), occupied_box = self._tiles_from_summaries(snapshot)
        else:
            encoded, cell_count, (min_x, min_y, max_x, max_y), occupied_box = self._tiles_from_rows(snapshot)
        pose = snapshot.get('historical_laser_pose')
        require(type(pose) is tuple and len(pose) == 3 and all(type(v) in (int, float) and math.isfinite(v) for v in pose),
                'native_historical_laser_pose_required')
        source_ns, raw_ns = snapshot.get('latest_scan_source_ns'), snapshot.get('latest_scan_callback_RAW_ns')
        sha = snapshot.get('latest_scan_CDR_sha256')
        require(type(source_ns) is int and 0 <= source_ns < 2**63 and type(raw_ns) is int and 0 <= raw_ns < 2**63 and
                type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha), 'native_original_stamp_required')
        transition=snapshot.get('registered_context_transition')
        require(transition is None or (type(transition) is dict and len(raw_json(transition))<=4096 and
                transition.get('registered_native_bridge') is True and
                transition.get('physical_accuracy_verified') is False and transition.get('map_epoch')==ctx['map_epoch']),
                'native_registered_context_transition_required')
        occupied_bounds = None
        if occupied_box is not None:
            occupied_bounds = dict(minX=occupied_box[0]*resolution,
                minY=occupied_box[1]*resolution,
                maxX=(occupied_box[2]+1)*resolution,
                maxY=(occupied_box[3]+1)*resolution)
        # Identity includes the whole context, not only the map epoch.
        identity = hashlib.sha256(raw_json(ctx)).hexdigest()
        header = dict(schema=SCHEMA, tile_epoch=identity, map_epoch=ctx['map_epoch'],
            context=ctx, tile_version=version, scan_count=version, cell_count=cell_count,
            tile_size=64, tiles_reset=True, path=list(path), path_append=False,
            path_representation='accepted_render_pose_vertices_uniform_thinning',
            path_vertex_limit=PATH_VERTICES, path_compressions=compressions,
            storage='ternary_native_evidence_v1', retained_historical_generation=True,
            latest_scan_source_ns=source_ns, latest_scan_callback_RAW_ns=raw_ns,
            latest_scan_CDR_sha256=sha, pose=dict(x=pose[0], y=pose[1], yaw=pose[2],stamp=source_ns/1e9),
            registered_context_transition=transition,
            grid=dict(width=max_x-min_x+1, height=max_y-min_y+1, resolution=resolution,
                source_resolution=resolution, origin=dict(x=min_x*resolution, y=min_y*resolution),
                occupied_bounds=occupied_bounds, encoding='tiles-cells-v1', row_order='min_y_first'), **FALSE_FLAGS)
        # Fixed eight-tile pages stay well below the existing 262144-byte UDS reply cap.
        pages = [encoded[i:i+8] for i in range(0, len(encoded), 8)]
        require(len(pages) <= 64, 'native_tile_page_capacity')
        generation = hashlib.sha256(raw_json([header, encoded])).hexdigest()
        payloads = []
        for index, page in enumerate(pages):
            value = dict(header, tiles=page, page_index=index, page_count=len(pages),
                tile_count=len(encoded), generation_id=generation,
                next_cursor=None if index+1 == len(pages) else f'{generation}:{index+1}')
            raw = raw_json(value)
            require(len(raw) <= self.page_bytes, 'native_tile_page_bytes')
            payloads.append(raw)
        require(sum(map(len, payloads)) <= GENERATION_BYTES, 'native_tile_generation_bytes')
        return generation, tuple(payloads), json.loads(raw_json(header))

    def _tiles_from_rows(self, snapshot):
        """Reference path: complete sorted cell rows, every tile re-encoded."""
        cells = snapshot.get('cells')
        require(type(cells) is tuple and 0 < len(cells) <= MAX_CELLS, 'native_map_cells_capacity')
        tiles, previous, occupied = {}, None, []
        min_x = min_y = 2**63
        max_x = max_y = -(2**63)
        for row in cells:
            require(type(row) is tuple and len(row) == 5 and all(type(v) is int for v in row),
                    'native_cell_tuple_required')
            x, y, kind, source_ns, raw_ns = row
            require(abs(x) <= 2**30 and abs(y) <= 2**30 and kind in (0, 100) and
                    0 <= source_ns < 2**63 and 0 <= raw_ns < 2**63 and
                    (previous is None or (x, y) > previous), 'native_cell_order_or_value')
            previous = (x, y)
            key = (x // 64, y // 64)
            if key not in tiles:
                require(len(tiles) < self.max_tiles, 'native_tile_capacity')
                tiles[key] = [0] * 4096
            tiles[key][(y % 64) * 64 + x % 64] = 1 if kind == 0 else 2
            min_x, min_y, max_x, max_y = min(min_x, x), min(min_y, y), max(max_x, x), max(max_y, y)
            if kind == 100:
                occupied.append((x, y))
        box = None
        if occupied:
            box = (min(x for x, y in occupied), min(y for x, y in occupied),
                   max(x for x, y in occupied), max(y for x, y in occupied))
        encoded = [dict(x=x, y=y, **encode_cells(states)) for (x, y), states in sorted(tiles.items())]
        return encoded, len(cells), (min_x, min_y, max_x, max_y), box

    def _tiles_from_summaries(self, snapshot):
        """Same complete generation from immutable owner tiles.

        Only tiles whose exact state bytes are new since the previous
        generation are encoded again; unchanged tiles reuse their encoding.
        """
        rows, cell_count = snapshot.get('tiles'), snapshot.get('cell_count')
        # Same reason order as the rows path: an empty generation is a cell-capacity failure.
        require(type(cell_count) is int and 0 < cell_count <= MAX_CELLS, 'native_map_cells_capacity')
        require(type(rows) is tuple and 0 < len(rows) <= self.max_tiles, 'native_tile_capacity')
        cache, fresh, encoded, previous, counted = self._tile_cache, {}, [], None, 0
        min_x = min_y = 2**63
        max_x = max_y = -(2**63)
        box = None
        for row in rows:
            require(type(row) is tuple and len(row) == 6, 'native_tile_summary_required')
            tx, ty, states, count, bounds, occupied = row
            require(type(tx) is int and type(ty) is int and abs(tx) <= 2**24 and abs(ty) <= 2**24 and
                    (previous is None or (tx, ty) > previous), 'native_tile_order_or_value')
            previous = (tx, ty)
            require(type(states) is bytes and len(states) == 4096, 'native_tile_states_required')
            # Facts are a pure function of the exact state bytes: computed and
            # validated once per distinct tile content, then reused.
            facts = cache.get(states) or fresh.get(states) or tile_facts(states)
            fresh[states] = facts
            value, derived_count, local, local_occupied = facts
            require(local is not None, 'native_tile_states_required')
            shift = lambda b: None if b is None else (tx*64 + b[0], ty*64 + b[1], tx*64 + b[2], ty*64 + b[3])
            derived, derived_occupied = shift(local), shift(local_occupied)
            # The owner's summary must equal the facts derived from its own states.
            require(count == derived_count, 'native_tile_states_required')
            require(bounds == derived, 'native_tile_bounds_required')
            require(occupied == derived_occupied, 'native_tile_occupied_bounds_required')
            require(all(abs(v) <= 2**30 for v in derived), 'native_cell_order_or_value')
            counted += count
            min_x, min_y = min(min_x, derived[0]), min(min_y, derived[1])
            max_x, max_y = max(max_x, derived[2]), max(max_y, derived[3])
            if derived_occupied is not None:
                box = derived_occupied if box is None else (
                    min(box[0], derived_occupied[0]), min(box[1], derived_occupied[1]),
                    max(box[2], derived_occupied[2]), max(box[3], derived_occupied[3]))
            encoded.append(dict(x=tx, y=ty, **value))
        require(counted == cell_count, 'native_tile_cell_count_mismatch')
        self._tile_cache = fresh
        return encoded, cell_count, (min_x, min_y, max_x, max_y), box

    def _expire_and_trim(self):
        now = self._clock()
        require(type(now) in (int, float) and math.isfinite(now) and now >= 0, 'native_lease_clock_required')
        for token, (_, deadline) in tuple(self._leases.items()):
            if now >= deadline:
                del self._leases[token]
        protected = {key for key, _ in self._leases.values()} | set(list(self._generations)[-2:])
        for key in tuple(self._generations):
            if key not in protected:
                del self._generations[key]
        return now

    def prepare_snapshot(self, snapshot, status):
        """Heavy encoder phase outside all publisher/owner locks.

        The asynchronous map owner invokes the returned tiny pointer swap only
        under its exact serial/Stop fence. A callback which finishes after Stop
        cannot install a new encoded generation. No transport worker is added.
        """
        try:
            status_raw = raw_json(status)
            require(len(status_raw) <= 8192, 'native_map_status_capacity')
            owned_status = json.loads(status_raw)
            require(type(snapshot) is dict, 'complete_native_display_schema_required')
            pose = snapshot.get('historical_laser_pose')
            require(type(pose) is tuple and len(pose)==3 and
                    all(type(v) in (int,float) and math.isfinite(v) for v in pose),
                    'native_historical_laser_pose_required')
            source_ns, raw_ns = snapshot.get('latest_scan_source_ns'),snapshot.get('latest_scan_callback_RAW_ns')
            require(type(source_ns) is int and 0<=source_ns<2**63 and
                    type(raw_ns) is int and 0<=raw_ns<2**63,'native_original_stamp_required')
            with self._lock:
                trail = list(self._trail)
                compressions, clear_serial = self._trail_compressions, self._clear_serial
                previous = next(reversed(self._generations.values()))[1] if self._generations else None
                previous_status = self._last_status
            bridge = owned_status.get('counters',{}).get('bridges',0)
            old_bridge = previous_status.get('counters',{}).get('bridges',0)
            require(type(bridge) is int and 0<=bridge<2**63 and
                    type(old_bridge) is int and 0<=old_bridge<2**63,'native_map_bridge_counter_required')
            trail.append(dict(x=pose[0],y=pose[1],yaw=pose[2],stamp=source_ns/1e9,
                original_source_ns=source_ns,original_callback_RAW_ns=raw_ns,
                break_before=previous is not None and
                    (previous['context']!=snapshot.get('context') or bridge!=old_bridge)))
            if len(trail)>PATH_VERTICES:
                # Select only original vertices; never fabricate interpolation.
                # Preserve the first/last anchor and every discontinuity fence.
                indexes=[0,*range(2,len(trail)-1,2),len(trail)-1]
                compressed=[]
                for n,index in enumerate(indexes):
                    point=dict(trail[index])
                    if n and any(p['break_before'] for p in trail[indexes[n-1]+1:index+1]):
                        point['break_before']=True
                    compressed.append(point)
                trail=compressed;compressions+=1
            require(len(raw_json(trail))<=65536,'native_path_capacity')
            key, pages, header = self._build(snapshot,trail,compressions)
        except (ValueError, TypeError, OverflowError) as error:
            with self._lock:
                self._reason = str(error)[:128]
            return None

        def publish():
            with self._lock:
                self._expire_and_trim()
                if clear_serial != self._clear_serial:
                    return False
                if self._generations:
                    latest = next(reversed(self._generations.values()))[1]
                    if latest['map_epoch'] != header['map_epoch']:
                        self._reason = 'waiting_registered_native_resume'
                        return False  # only explicit new_map clears history
                    require(header['tile_version'] > latest['tile_version'], 'native_map_version_rewind')
                self._generations[key] = (pages, header)
                self._expire_and_trim()
                require(sum(sum(map(len, value[0])) for value in self._generations.values()) <= CACHE_BYTES,
                        'native_tile_cache_capacity')
                self._last_status = owned_status
                self._trail = trail
                self._trail_compressions = compressions
                self._reason = 'native_historical_tiles_available'
                return True
        return publish

    def _refresh(self):
        if self._source is None:
            return
        self._last_status = self._source.status()
        snapshot = self._source.display_snapshot()
        version, ctx = snapshot.get('map_version'), snapshot.get('context')
        if not version:
            return
        if self._generations:
            latest = next(reversed(self._generations.values()))[1]
            if latest['tile_version'] == version and latest['context'] == ctx:
                return
            if latest['context'] == ctx:
                require(version > latest['tile_version'], 'native_map_version_rewind')
        publish = self.prepare_snapshot(snapshot, self._last_status)
        if publish is not None:
            publish()

    def _status_locked(self):
        latest = next(reversed(self._generations.values()))[1] if self._generations else None
        return dict(enabled=True, available=latest is not None, reason=self._reason,
            map=json.loads(raw_json(self._last_status)), map_version=None if latest is None else latest['tile_version'],
            retained_historical_generation=True, source_detached=self._source is None,
            transport_max_tiles=self.max_tiles, transport_page_bytes=self.page_bytes,
            generation_lease_count=len(self._leases), lease_seconds=LEASE_SECONDS,
            transport_cache_bytes_limit=CACHE_BYTES, **FALSE_FLAGS)

    def status(self):
        # Never call the owner's lock-taking method under the transport lock:
        # the owner publication fence invokes our tiny swap in the other order.
        with self._lock:
            source = self._source
        observed = None if source is None else source.status()
        if observed is not None:
            require(len(raw_json(observed)) <= 8192, 'native_map_status_capacity')
        with self._lock:
            self._expire_and_trim()
            if self._source is source and observed is not None:
                self._last_status = json.loads(raw_json(observed))
            return self._status_locked()

    def page(self, cursor=None):
        with self._lock:
            now = self._expire_and_trim()
            if cursor is None:
                if not self.push_only:
                    self._refresh()
                if not self._generations:
                    return dict(ok=True, available=False, status=self._status_locked(), **FALSE_FLAGS)
                require(len(self._leases) < 2, 'native_map_two_clients_capacity')
                key, index = next(reversed(self._generations)), 0
                token = secrets.token_hex(16)
                self._leases[token] = (key, now + LEASE_SECONDS)
            else:
                require(type(cursor) is str and re.fullmatch('[0-9a-f]{32}:[0-9]{1,2}', cursor),
                        'native_tile_cursor_required')
                token, text = cursor.split(':')
                require(token in self._leases, 'native_map_lease_expired_or_released')
                key, _ = self._leases[token]
                index = int(text)
            require(key in self._generations, 'native_tile_generation_evicted')
            pages, _ = self._generations[key]
            require(index < len(pages), 'native_tile_page_out_of_range')
            # Fresh owned JSON response, immutable cache never escapes by alias.
            page = json.loads(pages[index])
            page['lease_id'] = token
            page['next_cursor'] = None if index + 1 == len(pages) else f'{token}:{index+1}'
            if page['next_cursor'] is None:
                del self._leases[token]  # final owned reply no longer needs a cache pin
            return dict(ok=True, available=True, page=page, status=self._status_locked(), **FALSE_FLAGS)

    def release(self, token):
        require(type(token) is str and re.fullmatch('[0-9a-f]{32}', token), 'native_map_lease_required')
        with self._lock:
            released = self._leases.pop(token, None) is not None
            self._expire_and_trim()
            return dict(ok=True, released=released, **FALSE_FLAGS)

    def attach_source(self, source):
        require(callable(getattr(source, 'display_snapshot', None)) and callable(getattr(source, 'status', None)),
                'complete_native_map_source_required')
        with self._lock:
            self._source = source

    def clear_for_new_map(self):
        """Only the service's explicit new-map owner operation calls this."""
        with self._lock:
            self._generations.clear(); self._leases.clear()
            self._trail.clear(); self._trail_compressions = 0; self._clear_serial += 1
            self._reason = 'waiting_first_native_map'

    def retain_and_detach(self):
        with self._lock:
            source = self._source
        observed = None if source is None else source.status()
        with self._lock:
            try:
                if not self.push_only:
                    self._refresh()
                elif self._source is source and observed is not None:
                    require(len(raw_json(observed)) <= 8192, 'native_map_status_capacity')
                    self._last_status = json.loads(raw_json(observed))
            except (ValueError, TypeError, OverflowError) as error:
                self._reason = str(error)[:128]
            finally:
                self._source = None  # no factory, CDR or prepared capability retained
            return True


class NativeMapSession:
    """One service-owned map/keyframe lifetime across source Start/Stop.

    Construction is inert. The service serializes Start/New-map; the existing
    runner owns Stop and its absolute join deadline. Unknown map workers stay
    attached and reject another source. Only explicit new_map erases history.
    """
    def __init__(self, adapter_factory, *, map_epoch,pose_ledger_enabled=False,scan_to_map_bridge_enabled=False):
        require(callable(adapter_factory) and type(map_epoch) is str and
                re.fullmatch('[0-9a-f]{32}', map_epoch), 'native_map_session_constructor_required')
        require(type(pose_ledger_enabled) is bool,'typed_pose_ledger_default_OFF_required')
        self._pose_ledger_enabled=pose_ledger_enabled
        require(type(scan_to_map_bridge_enabled) is bool,'typed_scan_to_map_bridge_required')
        self._scan_to_map_bridge_enabled=scan_to_map_bridge_enabled
        self._factory = adapter_factory
        self._epoch = map_epoch
        self._adapter = None
        self.transport = None
        self._closed = True
        self._lock = threading.RLock()

    def bind_factory(self, factory):
        with self._lock:
            require(self._closed, 'previous_native_map_worker_join_required')
            if self._adapter is None:
                self._adapter = self._factory(factory, enabled=True, map_epoch=self._epoch,
                    accepted_native_chain_enabled=True, batched_rays_enabled=True,pose_ledger_enabled=self._pose_ledger_enabled,
                    scan_to_map_bridge_enabled=self._scan_to_map_bridge_enabled)
                self.transport = NativeMapTransport(self._adapter, push_only=True)
            else:
                self._adapter.attach_factory(factory, explicit=True)
                self.transport.attach_source(self._adapter)
            self._closed = False  # track before Thread.start may create a worker
            self._adapter.start(explicit=True, on_display_commit=self.transport.prepare_snapshot)
            return self._adapter

    def stop(self, reason='operator_stop'):
        # Do not wait for Start/encoding or joins under this manager lock.
        adapter = self._adapter
        if adapter is not None:
            adapter.stop(reason)

    def close(self, timeout):
        adapter = self._adapter
        if adapter is None:
            return dict(joined=True, worker_alive=False, owned_worker_retained=False,
                        publication_inhibited=True)
        proof = adapter.close(timeout)
        require(type(proof) is dict and all(type(proof.get(k)) is bool for k in
                ('joined', 'worker_alive', 'owned_worker_retained', 'publication_inhibited')),
                'native_map_worker_close_proof_required')
        if (proof['joined'] is True and proof['worker_alive'] is False and
                proof['owned_worker_retained'] is False and proof['publication_inhibited'] is True):
            adapter.detach_factory()
            self.transport.retain_and_detach()
            with self._lock:
                self._closed = True
        return proof

    def new_map(self, expected_epoch):
        with self._lock:
            require(self._closed and type(expected_epoch) is str and expected_epoch == self._epoch,
                    'explicit_joined_current_epoch_new_map_required')
            epoch = secrets.token_hex(16)
            if self._adapter is not None:
                self._adapter.new_map(explicit=True, map_epoch=epoch)
            if self.transport is not None:
                self.transport.clear_for_new_map()
            self._epoch = epoch
            return dict(ok=True, map_epoch=epoch, map_cleared=True, **FALSE_FLAGS)


def dispatch_map_request(runner, value, retained):
    """Called only by the existing single local service request loop."""
    require(type(value) is dict and value.get('op') in ('map_status', 'map_tiles', 'map_release') and
            set(value) <= {'op', 'cursor', 'lease'} and
            ('cursor' not in value or value['op'] == 'map_tiles') and
            ('lease' not in value or value['op'] == 'map_release'), 'native_map_get_request_required')
    if isinstance(retained, NativeMapSession):
        selected = retained.transport
        if selected is None:
            return dict(ok=True, available=False, status=dict(enabled=True,
                map_epoch=retained._epoch, reason='waiting_first_native_map'), **FALSE_FLAGS)
        if value['op'] == 'map_release':
            return selected.release(value.get('lease'))
        if value['op'] == 'map_status':
            return dict(ok=True, status=dict(selected.status(), map_epoch=retained._epoch), **FALSE_FLAGS)
        return selected.page(value.get('cursor'))
    # Isolated legacy test seam; the runtime uses the persistent session above.
    with runner.lock:
        resource = runner.resource
        transport = None if resource is None else getattr(resource, 'map_transport', None)
    # A newly starting owner cannot erase the previous complete display.
    if transport is not None:
        if value['op'] == 'map_tiles' and value.get('cursor') is None:
            answer = transport.page()
            if answer['available']:
                retained[0] = transport
                return answer
        elif retained[0] is transport or retained[0] is None:
            retained[0] = transport
    selected = retained[0]
    if selected is None:
        return dict(ok=True, available=False, status=dict(enabled=False, reason='native_map_default_OFF'), **FALSE_FLAGS)
    if value['op'] == 'map_release':
        return selected.release(value.get('lease'))
    if value['op'] == 'map_status':
        return dict(ok=True, status=selected.status(), **FALSE_FLAGS)
    return selected.page(value.get('cursor'))
