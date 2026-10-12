"""Default-OFF native laser-local display map; no physical/motor authority.

The injected acceptance/bridge validators are trusted adapter boundaries, not
implemented native admission. Descriptor fields cannot certify sensor custody.
No IO, threads, clocks, localization solver or source timestamp is created here.
"""
from collections.abc import Mapping
from dataclasses import dataclass, replace, fields
import hashlib
import json
import math
import struct
import threading
from types import MappingProxyType

import numpy as np

MAX_NS = (1 << 63) - 1
SOURCE_AGE_NS, RAW_AGE_NS = 250_000_000, 200_000_000
CONTEXT = ('source_session','generation','source_clock_domain','receipt_clock_domain','map_epoch','map_frame')


def require(value, reason):
    if not value: raise ValueError(reason)


def integer(x):
    require(type(x) is int and 0 <= x <= MAX_NS, 'signed63_integer_required')
    return x


def finite(x, bound=1e6):
    require(type(x) in (int,float) and math.isfinite(x) and abs(x) <= bound, 'bounded_finite_number_required')
    return float(x)


def token(x):
    require(type(x) is str and 0 < len(x) <= 128 and all(ord(c)>=32 for c in x), 'explicit_token_required')
    return x


def context(x):
    require(type(x) is tuple and len(x)==6, 'exact_native_map_context_required')
    for key,value in zip(CONTEXT,x): integer(value) if key=='generation' else token(value)
    return x


def digest(x):
    meta={f.name:getattr(x,f.name) for f in fields(x) if f.name!='ranges'}
    h=hashlib.sha256(json.dumps(meta,sort_keys=True,allow_nan=False).encode())
    h.update(struct.pack('<'+str(len(x.ranges))+'d',*x.ranges))
    return h.digest()


@dataclass(frozen=True)
class AcceptedNativeFrame:
    context: tuple
    sequence: int  # Monotonic accepted diagnostic version, not callback row seq.
    scan_stamps_ns: tuple
    scan_receipts_ns: tuple
    scan_cdr_sha256: tuple
    scan_gid: str
    imu_stamp_ns: int
    imu_receipt_ns: int
    imu_cdr_sha256: str
    imu_gid: str
    laser_frame: str
    # Full current original scan. Invalid/nonfinite beams remain UNKNOWN.
    ranges: tuple
    angle_min: float
    angle_increment: float
    range_min: float
    range_max: float
    time_increment: float
    # Exact native registration convention: p_previous = R(yaw)p_current + xy.
    current_to_previous: tuple


@dataclass(frozen=True)
class RegisteredNativeBridge:
    context: tuple
    historical_current_cdr_sha256: str
    new_current_cdr_sha256: str
    map_version: int
    current_pose_in_map: tuple


@dataclass(frozen=True)
class AcceptedContiguousNativeChain:
    context: tuple
    historical_current_cdr_sha256: str
    new_current_cdr_sha256: str
    map_version: int
    current_pose_in_map: tuple
    chain_token: str
    from_capture_sequence: int
    to_capture_sequence: int


@dataclass(frozen=True)
class _Cell:
    kind: int
    source_ns: int
    receipt_ns: int
    lineage: tuple = ()


def _frozen(array):
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class _Tile:
    """One immutable 64x64 block of retained evidence (copy-on-write).

    Index (y%64)*64+x%64. states: 0 unknown, 1 free, 2 occupied (the tile
    transport's ternary display value). lineage indexes the owning view's
    context table. Bounds are inclusive cell coordinates.
    """
    states: np.ndarray
    source_ns: np.ndarray
    receipt_ns: np.ndarray
    lineage: np.ndarray
    count: int
    bounds: tuple
    occupied: tuple | None
    states_bytes: bytes


def _empty_tile():
    return (np.zeros(4096, np.uint8), np.zeros(4096, np.int64), np.zeros(4096, np.int64),
            np.full(4096, -1, np.int16), None, None)


class _TiledCells(Mapping):
    """Read-only (x,y)->_Cell view over immutable tiles; unchanged tiles are shared."""
    __slots__ = ('tiles', 'lineages', '_count')

    def __init__(self, tiles=None, count=0, lineages=()):
        self.tiles = MappingProxyType({} if tiles is None else tiles)
        self.lineages = lineages
        self._count = count

    def _cell(self, tile, index):
        return _Cell(0 if tile.states[index] == 1 else 100, int(tile.source_ns[index]),
                     int(tile.receipt_ns[index]), self.lineages[tile.lineage[index]])

    def get(self, key, default=None):
        tile = self.tiles.get((key[0] >> 6, key[1] >> 6))
        index = (key[1] & 63) * 64 + (key[0] & 63)
        if tile is None or not tile.states[index]:
            return default
        return self._cell(tile, index)

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        tile = self.tiles.get((key[0] >> 6, key[1] >> 6))
        return tile is not None and bool(tile.states[(key[1] & 63) * 64 + (key[0] & 63)])

    def __len__(self):
        return self._count

    def _tile_items(self, tx, ty, tile):
        for index in np.flatnonzero(tile.states).tolist():
            yield (tx * 64 + index % 64, ty * 64 + index // 64), self._cell(tile, index)

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def items(self):
        for (tx, ty), tile in self.tiles.items():
            yield from self._tile_items(tx, ty, tile)

    def window_items(self, gx, gy, n):
        """Cells of the tiles overlapping [gx,gx+n)x[gy,gy+n); callers still bound-check."""
        for tx in range(gx >> 6, ((gx + n - 1) >> 6) + 1):
            for ty in range(gy >> 6, ((gy + n - 1) >> 6) + 1):
                tile = self.tiles.get((tx, ty))
                if tile is not None:
                    yield from self._tile_items(tx, ty, tile)

    def proposal_window(self, gx, gy, n, lineage, source_now_ns, receipt_now_ns):
        """Exact vectorized form of the per-cell proposal raster loop."""
        array = np.full((n, n), -1, dtype=np.int8)
        oldest = None
        if lineage not in self.lineages:
            return array, oldest
        lid = self.lineages.index(lineage)
        local = np.arange(4096)
        for tx in range(gx >> 6, ((gx + n - 1) >> 6) + 1):
            for ty in range(gy >> 6, ((gy + n - 1) >> 6) + 1):
                tile = self.tiles.get((tx, ty))
                if tile is None:
                    continue
                x, y = tx * 64 + local % 64, ty * 64 + local // 64
                age_s, age_r = source_now_ns - tile.source_ns, receipt_now_ns - tile.receipt_ns
                keep = ((tile.states != 0) & (tile.lineage == lid) & (x >= gx) & (x < gx + n) & (y >= gy) & (y < gy + n) &
                        (age_s >= 0) & (age_s < SOURCE_AGE_NS) & (age_r >= 0) & (age_r < RAW_AGE_NS))
                if keep.any():
                    array[y[keep] - gy, x[keep] - gx] = np.where(tile.states[keep] == 1, 0, 100)
                    low = int(tile.source_ns[keep].min())
                    oldest = low if oldest is None else min(oldest, low)
        return array, oldest

    def with_evidence(self, free_xy, hit_xy, source_ns, receipt_ns, lineage):
        """New view after one scan: free never overwrites occupied; hits always win.

        free_xy and hit_xy are disjoint unique integer cell arrays. Returns the
        view and the number of newly observed cells. Only touched tiles copy.
        """
        lineages = self.lineages
        if lineage not in lineages:
            lineages = lineages + (lineage,)
        lid = lineages.index(lineage)
        xy = np.concatenate([np.asarray(free_xy, np.int64).reshape(-1, 2), np.asarray(hit_xy, np.int64).reshape(-1, 2)])
        hit = np.zeros(len(xy), bool)
        hit[len(xy) - len(hit_xy):] = True
        tiles = dict(self.tiles)
        added = 0
        if len(xy):
            tx, ty = xy[:, 0] >> 6, xy[:, 1] >> 6
            order = np.lexsort((ty, tx))
            xy, hit, tx, ty = xy[order], hit[order], tx[order], ty[order]
            index = (xy[:, 1] & 63) * 64 + (xy[:, 0] & 63)
            change = np.flatnonzero((np.diff(tx) != 0) | (np.diff(ty) != 0)) + 1
            for a, b in zip(np.concatenate([[0], change]).tolist(), np.concatenate([change, [len(xy)]]).tolist()):
                key = (int(tx[a]), int(ty[a]))
                old = tiles.get(key)
                if old is None:
                    states, source, receipt, owner, bounds, occupied = _empty_tile()
                else:
                    states, source, receipt, owner = (old.states.copy(), old.source_ns.copy(),
                                                      old.receipt_ns.copy(), old.lineage.copy())
                    bounds, occupied = old.bounds, old.occupied
                gi, gh, gxy = index[a:b], hit[a:b], xy[a:b]
                free_i = gi[~gh]
                free_i = free_i[states[free_i] != 2]
                hit_i = gi[gh]
                added += int(np.count_nonzero(states[free_i] == 0) + np.count_nonzero(states[hit_i] == 0))
                states[free_i] = 1
                states[hit_i] = 2
                for target in (free_i, hit_i):
                    source[target] = source_ns
                    receipt[target] = receipt_ns
                    owner[target] = lid
                lo, hi = gxy.min(axis=0).tolist(), gxy.max(axis=0).tolist()
                bounds = ((lo[0], lo[1], hi[0], hi[1]) if bounds is None else
                          (min(bounds[0], lo[0]), min(bounds[1], lo[1]), max(bounds[2], hi[0]), max(bounds[3], hi[1])))
                if gh.any():
                    hxy = gxy[gh]
                    lo, hi = hxy.min(axis=0).tolist(), hxy.max(axis=0).tolist()
                    occupied = ((lo[0], lo[1], hi[0], hi[1]) if occupied is None else
                                (min(occupied[0], lo[0]), min(occupied[1], lo[1]),
                                 max(occupied[2], hi[0]), max(occupied[3], hi[1])))
                tiles[key] = _Tile(_frozen(states), _frozen(source), _frozen(receipt), _frozen(owner),
                                   int(np.count_nonzero(states)), bounds, occupied, states.tobytes())
        return _TiledCells(tiles, self._count + added, lineages), added

    def tile_rows(self):
        """Sorted (tx,ty,states,count,bounds,occupied) for the tile transport."""
        return tuple((tx, ty, t.states_bytes, t.count, t.bounds, t.occupied)
                     for (tx, ty), t in sorted(self.tiles.items()))

    def rows(self):
        """Complete (x,y,kind,source_ns,receipt_ns) rows in (x,y) order."""
        parts = []
        for (tx, ty), tile in self.tiles.items():
            index = np.flatnonzero(tile.states)
            parts.append(np.column_stack([tx * 64 + index % 64, ty * 64 + index // 64,
                                          np.where(tile.states[index] == 1, 0, 100),
                                          tile.source_ns[index], tile.receipt_ns[index]]))
        if not parts:
            return ()
        table = np.concatenate(parts)
        table = table[np.lexsort((table[:, 1], table[:, 0]))]
        return tuple(tuple(row) for row in table.tolist())


@dataclass(frozen=True)
class _State:
    cells: object
    pose: tuple | None = None
    frame: AcceptedNativeFrame | None = None
    version: int = 0

    @property
    def cell_rows(self):
        # Complete sorted rows, built only when a caller asks for them.
        rows = self.__dict__.get('_rows')
        if rows is None:
            rows = self.cells.rows()
            self.__dict__['_rows'] = rows
        return rows


class _MapCapacityReached(Exception):
    pass


@dataclass(frozen=True)
class _Ticket:
    owner: object
    serial: int
    nonce: object


@dataclass(frozen=True)
class _Prepared:
    ticket: _Ticket
    nonce: object
    serial: int
    base: object
    frame: AcceptedNativeFrame
    frame_digest: bytes
    bridge: RegisteredNativeBridge | None
    bridge_digest: bytes | None
    next_state: _State


class NativeAcceptedMapOwner:
    def __init__(self, *, enabled=False, accepted_native_validator=None,
                 registered_bridge_validator=None, resolution_m=.1,
                 max_cells=250_000, local_width_cells=300, max_ray_visits=4_096_000,
                 historical_display_only=False):
        require(type(enabled) is bool, 'typed_default_OFF_required')
        require(type(historical_display_only) is bool,'typed_historical_policy_required')
        self._historical=historical_display_only
        self._transition=None
        require(accepted_native_validator is None or callable(accepted_native_validator), 'native_validator_callable_required')
        require(registered_bridge_validator is None or callable(registered_bridge_validator), 'bridge_validator_callable_required')
        require(.02 <= finite(resolution_m) <= 1., 'bounded_resolution_required')
        require(type(max_cells) is int and 1 <= max_cells <= 250_000 and
                type(local_width_cells) is int and 4 <= local_width_cells <= 300 and
                type(max_ray_visits) is int and 1 <= max_ray_visits <= 4_096_000, 'bounded_map_work_required')
        self._enabled=enabled;self._validator=accepted_native_validator;self._bridge_validator=registered_bridge_validator
        self._resolution=float(resolution_m);self._capacity=max_cells;self._width=local_width_cells;self._visits=max_ray_visits
        self._lock=threading.RLock();self._publication_lock=threading.Lock();self._stop=threading.Event()
        self._active=False;self._serial=0;self._context=None
        self._state=_State(_TiledCells());self._pending=None;self._continuous=False;self._reason='default_off'

    def start(self, ctx, *, explicit=False):
        ctx=context(ctx)
        with self._lock:
            require(self._enabled and explicit is True and callable(self._validator), 'explicit_native_adapter_start_required')
            require(not self._active, 'map_owner_already_active')
            require(self._context is None or self._context==ctx or (self._historical and
                    self._context[4:]==ctx[4:]),'new_source_context_requires_separate_retained_map_owner')
            self._context=ctx;self._serial+=1;self._active=True
            with self._publication_lock:self._stop.clear()
            self._reason='waiting_first_native_fit' if not self._state.cells else 'waiting_registered_native_bridge'
            return self.status()

    def stop(self, reason='operator_stop'):
        token(reason)
        # Same tiny fence as the final pointer swap; never wait on raster work.
        with self._publication_lock:self._stop.set()
        with self._lock:
            self._serial+=1;self._active=False;self._continuous=False;self._pending=None;self._reason=reason
            return self.status()

    def invalidate_gap(self, reason='native_pair_gap'):
        token(reason)
        with self._lock:
            self._serial+=1;self._continuous=False;self._pending=None;self._reason=reason+'_needs_registered_native_bridge'
            return self.status()

    def status(self):
        with self._lock:
            return dict(enabled=self._enabled,active=self._active and not self._stop.is_set(),
                mode='UNVERIFIED_LASER_LOCAL_DISPLAY_PROPOSAL',reason=self._reason,
                context=dict(zip(CONTEXT,self._context)) if self._context else None,
                historical_display_only=self._historical,
                registered_context_transition=self._transition,
                map_version=self._state.version,historical_cell_count=len(self._state.cells),
                pose_continuity=self._continuous and not self._stop.is_set(),
                native_source_adapter_qualified=False,physical_calibration_verified=False,
                body_velocity_mps=None,physical_accuracy_verified=False,runtime_authority=False,
                motor_command_sent=False,autonomy_output_enabled=False,execution_eligible=False,
                global_last_row_confirmed=False,mission_execution_complete=False)

    def new_map(self, ctx, *, explicit=False):
        ctx=context(ctx)
        with self._lock:
            require(explicit is True and not self._active and self._stop.is_set(),
                    'explicit_stopped_new_map_required')
            self._serial+=1;self._context=ctx;self._pending=None
            self._state=_State(_TiledCells());self._continuous=False
            self._transition=None;self._reason='explicit_new_map'
            return self.status()

    def historical_cells(self):
        with self._lock:
            s=self._state
        # The state is immutable; its rows are built outside the owner lock.
        return s.cell_rows

    def display_snapshot(self, *, tiles=False):
        """One complete historical generation; no clock read/live eligibility.

        Ternary evidence is not a probability/log-odds grid. An external tile
        transport must preserve that meaning and bound its own serialization.
        tiles=True hands over the immutable tile summaries instead of sorted
        cell rows; the transport encodes the same complete generation.
        """
        with self._lock:
            s=self._state;f=s.frame;owner_context=self._context;transition=self._transition
        # One immutable generation; serialization work runs outside the owner lock.
        body=dict(tiles=s.cells.tile_rows(),cell_count=len(s.cells)) if tiles else dict(cells=s.cell_rows)
        return dict(schema='ubts.native.laser_local.complete_display.v1',
                map_version=s.version,resolution_m=self._resolution,
                context=dict(zip(CONTEXT,f.context if f else owner_context)) if owner_context else None,
                registered_context_transition=transition,
                historical_laser_pose=s.pose,
                **body,
                latest_scan_source_ns=f.scan_stamps_ns[1] if f else None,
                latest_scan_callback_RAW_ns=f.scan_receipts_ns[1] if f else None,
                latest_scan_CDR_sha256=f.scan_cdr_sha256[1] if f else None,
                retained_historical_generation=True,live_input_eligible=False,
                runtime_authority=False,motor_command_sent=False,execution_eligible=False,
                physical_accuracy_verified=False,global_last_row_confirmed=False)

    def _frame(self, f, now_s, now_r):
        require(type(f) is AcceptedNativeFrame, 'owned_native_frame_type_required')
        require(context(f.context)==self._context, 'native_source_context_changed')
        integer(f.sequence);require(f.sequence>0, 'accepted_native_version_required')
        for value in (f.scan_stamps_ns,f.scan_receipts_ns):
            require(type(value) is tuple and len(value)==2, 'exact_original_pair_metadata_required')
            for n in value:integer(n)
        require(0 < f.scan_stamps_ns[1]-f.scan_stamps_ns[0] <= 200_000_000 and
                f.scan_receipts_ns[0] <= f.scan_receipts_ns[1], 'adjacent_native_pair_required')
        require(type(f.scan_cdr_sha256) is tuple and len(f.scan_cdr_sha256)==2, 'exact_original_CDR_pair_required')
        for value,size in ((f.scan_cdr_sha256[0],64),(f.scan_cdr_sha256[1],64),(f.imu_cdr_sha256,64),(f.scan_gid,32),(f.imu_gid,32)):
            require(type(value) is str and len(value)==size and set(value)<=set('0123456789abcdef') and value!='0'*size,'original_provenance_binding_required')
        require(f.scan_gid!=f.imu_gid,'distinct_original_publishers_required');token(f.laser_frame)
        require(type(f.ranges) is tuple and 120<=len(f.ranges)<=4096 and
                all(type(x) in (int,float) for x in f.ranges),'full_bounded_original_ranges_required')
        amin,inc,mn,mx,dt=(finite(v) for v in (f.angle_min,f.angle_increment,f.range_min,f.range_max,f.time_increment))
        require(0 < inc <= .1 and len(f.ranges)*inc<=2*math.pi+2*inc and 0<=mn<mx<=100 and
                dt>=0 and (len(f.ranges)-1)*dt<=.2, 'original_scan_geometry_required')
        require(type(f.current_to_previous) is tuple and len(f.current_to_previous)==3,'explicit_native_SE2_required')
        dx,dy,yaw=(finite(v) for v in f.current_to_previous)
        require(math.hypot(dx,dy)<=.6 and abs(yaw)<math.pi,'bounded_native_fit_required')
        integer(f.imu_stamp_ns);integer(f.imu_receipt_ns)
        # Causal means original Header <= scan Header and actually available at
        # commit. DDS may deliver that lower IMU after the scan callback.
        require(f.imu_stamp_ns<=f.scan_stamps_ns[1], 'causal_original_IMU_required')
        if not self._historical:self._fresh(f,now_s,now_r)

    @staticmethod
    def _fresh(f, now_s, now_r):
        integer(now_s);integer(now_r)
        require(all(0<=now_s-stamp<SOURCE_AGE_NS for stamp in (f.scan_stamps_ns[1],f.imu_stamp_ns)) and
                all(0<=now_r-stamp<RAW_AGE_NS for stamp in (f.scan_receipts_ns[1],f.imu_receipt_ns)),
                'stale_or_future_native_pose')

    @staticmethod
    def _compose(pose, delta):
        x,y,a=pose;dx,dy,da=delta;c,s=math.cos(a),math.sin(a)
        return (x+c*dx-s*dy,y+s*dx+c*dy,math.atan2(math.sin(a+da),math.cos(a+da)))

    def _rays(self, f, pose):
        """All valid original returns, no invented free ray for unknown/max beams."""
        r=self._resolution;ox,oy=math.floor(pose[0]/r),math.floor(pose[1]/r)
        free=set();hits=set();seen=set();visits=0
        def observed(key):
            seen.add(key)
            if len(seen)>self._capacity:raise _MapCapacityReached()
            return key
        for i,d in enumerate(f.ranges):
            if not math.isfinite(d) or not max(.05,f.range_min)<=d<f.range_max:continue
            a=pose[2]+f.angle_min+i*f.angle_increment
            ex,ey=math.floor((pose[0]+d*math.cos(a))/r),math.floor((pose[1]+d*math.sin(a))/r)
            dx,dy=abs(ex-ox),abs(ey-oy);major=max(dx,dy)
            visits+=major+1;require(visits<=self._visits,'native_ray_work_budget')
            sx,sy=(1 if ex>=ox else -1),(1 if ey>=oy else -1)
            for n in range(major):
                m=(2*n*min(dx,dy)+major)//(2*major)
                free.add(observed((ox+sx*(n if dx>=dy else m),oy+sy*(m if dx>=dy else n))))
            hits.add(observed((ex,ey)))
        free.difference_update(hits)
        return free,hits

    def _ray_cells(self, f, pose):
        """The same free/hit cells as integer arrays for the tile update."""
        free,hits=self._rays(f,pose)
        return (np.array(sorted(free),dtype=np.int64).reshape(-1,2),
                np.array(sorted(hits),dtype=np.int64).reshape(-1,2))

    def _bridge(self, b, f, state):
        require(type(b) in (RegisteredNativeBridge,AcceptedContiguousNativeChain) and callable(self._bridge_validator), 'independent_registered_native_bridge_required')
        if type(b) is AcceptedContiguousNativeChain:
            require(self._historical and self._continuous and state.frame is not None and
                state.frame.context==f.context and b.from_capture_sequence==state.frame.sequence and
                b.to_capture_sequence==f.sequence and b.to_capture_sequence>b.from_capture_sequence,
                'accepted_contiguous_chain_cannot_bridge_native_gap')
            token(b.chain_token)
        require(b.context==self._context and b.map_version==state.version and state.frame is not None and
                b.historical_current_cdr_sha256==state.frame.scan_cdr_sha256[1] and
                b.new_current_cdr_sha256==f.scan_cdr_sha256[1], 'exact_native_keyframe_bridge_binding_required')
        require(type(b.current_pose_in_map) is tuple and len(b.current_pose_in_map)==3,'registered_bridge_pose_required')
        pose=tuple(finite(v) for v in b.current_pose_in_map)
        require(self._bridge_validator(b,f) is True,'registered_native_bridge_not_admitted')
        return pose

    def prepare(self, frame, *, source_now_ns, receipt_now_ns, registered_bridge=None):
        with self._lock:
            if not self._active or self._stop.is_set():return None
            require(self._pending is None,'one_map_generation_in_flight_required')
            base,serial,continuous=self._state,self._serial,self._continuous
        try:
            self._frame(frame,source_now_ns,receipt_now_ns)
            if base.frame is not None and frame.context==base.frame.context:
                require(frame.sequence>base.frame.sequence and frame.scan_stamps_ns[1]>base.frame.scan_stamps_ns[1] and
                        frame.scan_receipts_ns[1]>base.frame.scan_receipts_ns[1] and
                        frame.imu_stamp_ns>=base.frame.imu_stamp_ns and frame.imu_receipt_ns>=base.frame.imu_receipt_ns,
                        'native_progress_watermark_required')
                require(frame.scan_gid==base.frame.scan_gid and frame.imu_gid==base.frame.imu_gid and
                        frame.laser_frame==base.frame.laser_frame,'native_source_identity_changed')
            require(self._validator(frame) is True,'native_acceptance_witness_not_admitted')
            stamp=digest(frame)
            if base.frame is None:
                require(registered_bridge is None,'bridge_without_history_rejected')
                pose=self._compose((0.,0.,0.),frame.current_to_previous)
            elif continuous and type(registered_bridge) is AcceptedContiguousNativeChain:
                with self._lock:
                    if self._stop.is_set() or not self._active or serial!=self._serial or base is not self._state:return None
                    pose=self._bridge(registered_bridge,frame,base)
            elif not continuous:
                if registered_bridge is None:
                    with self._lock:self._reason='waiting_registered_native_bridge'
                    return None
                with self._lock:
                    if self._stop.is_set() or not self._active or serial!=self._serial or base is not self._state:return None
                    pose=self._bridge(registered_bridge,frame,base)
            else:
                if not (frame.sequence==base.frame.sequence+1 and
                        frame.scan_stamps_ns[0]==base.frame.scan_stamps_ns[1] and
                        frame.scan_receipts_ns[0]==base.frame.scan_receipts_ns[1] and
                        frame.scan_cdr_sha256[0]==base.frame.scan_cdr_sha256[1] and
                        frame.scan_gid==base.frame.scan_gid and frame.imu_gid==base.frame.imu_gid and
                        frame.laser_frame==base.frame.laser_frame):
                    self.invalidate_gap('native_pair_discontinuity');return None
                require(registered_bridge is None,'unsolicited_bridge_rejected')
                pose=self._compose(base.pose,frame.current_to_previous)
            try:free,hits=self._ray_cells(frame,pose)
            except _MapCapacityReached:
                self.invalidate_gap('native_map_capacity');return None
            # A later free ray does not renew or erase an old occupied claim.
            cells,_added=base.cells.with_evidence(free,hits,frame.scan_stamps_ns[1],
                                                  frame.scan_receipts_ns[1],frame.context)
            if len(cells)>self._capacity:self.invalidate_gap('native_map_capacity');return None
            require(digest(frame)==stamp and self._validator(frame) is True,'native_frame_changed_during_map_prepare')
            with self._lock:
                if self._stop.is_set() or not self._active or serial!=self._serial or base is not self._state:return None
                if registered_bridge is not None:self._bridge(registered_bridge,frame,base)
            bstamp=hashlib.sha256(repr(registered_bridge).encode()).digest() if registered_bridge is not None else None
            require(base.version < MAX_NS,'native_map_version_exhausted')
            next_state=_State(cells,pose,replace(frame),base.version+1)
            nonce=object();ticket=_Ticket(self,serial,nonce)
            prepared=_Prepared(ticket,nonce,serial,base,frame,stamp,registered_bridge,bstamp,next_state)
            with self._lock:
                if self._stop.is_set() or not self._active or serial!=self._serial or base is not self._state:return None
                require(self._pending is None,'one_map_generation_in_flight_required');self._pending=prepared
            return ticket
        except Exception as error:
            self.stop(str(error));raise

    def commit(self, ticket, *, source_now_ns, receipt_now_ns):
        with self._lock:
            if self._stop.is_set() or not self._active:return False
            try:
                prepared=self._pending
                require(type(ticket) is _Ticket and prepared is not None and ticket is prepared.ticket and ticket.owner is self and
                        ticket.nonce is prepared.nonce and ticket.serial==prepared.serial==self._serial and
                        prepared.base is self._state,'owned_map_generation_required')
                self._frame(prepared.frame,source_now_ns,receipt_now_ns)
                require(digest(prepared.frame)==prepared.frame_digest and self._validator(prepared.frame) is True,'native_frame_changed_before_map_commit')
                if prepared.bridge is not None:
                    require(hashlib.sha256(repr(prepared.bridge).encode()).digest()==prepared.bridge_digest,'native_bridge_changed_before_commit')
                    self._bridge(prepared.bridge,prepared.frame,prepared.base)
                with self._publication_lock:
                    if self._stop.is_set():return False
                    if prepared.base.frame is not None and prepared.base.frame.context!=prepared.frame.context:
                        self._transition=dict(map_epoch=prepared.frame.context[4],
                            from_context=dict(zip(CONTEXT,prepared.base.frame.context)),
                            to_context=dict(zip(CONTEXT,prepared.frame.context)),
                            registered_native_bridge=True,map_version=prepared.next_state.version,
                            physical_accuracy_verified=False)
                    self._state=prepared.next_state;self._pending=None;self._continuous=True;self._reason='unverified_native_local_map_complete'
                return True
            except Exception as error:self.stop(str(error));raise

    def proposal_snapshot(self, *, source_now_ns, receipt_now_ns):
        with self._lock:
            s=self._state
            if not self._active or self._stop.is_set() or not self._continuous or s.frame is None:return None
            try:self._fresh(s.frame,source_now_ns,receipt_now_ns)
            except ValueError:
                self._reason='stale_or_invalid_native_proposal_snapshot';return None
            f=s.frame
            if f.context!=self._context:return None
            n=self._width;r=self._resolution
            gx=math.floor(s.pose[0]/r)-n//2;gy=math.floor(s.pose[1]/r)-n//2
            array,low=s.cells.proposal_window(gx,gy,n,f.context,source_now_ns,receipt_now_ns)
            # A floor for known cells only. Empty grid stays UNKNOWN; no observation is invented.
            oldest=low if low is not None else f.scan_stamps_ns[1]
            frozen=np.frombuffer(array.tobytes(),dtype=np.int8).reshape(array.shape)
            ctx=dict(zip(CONTEXT,self._context))
            pose=dict(x_m=s.pose[0],y_m=s.pose[1],yaw_rad=s.pose[2],sequence=f.sequence,
                source_stamp_ns=f.scan_stamps_ns[1],receipt_stamp_ns=f.scan_receipts_ns[1],
                imu_source_stamp_ns=f.imu_stamp_ns,imu_receipt_stamp_ns=f.imu_receipt_ns,
                accepted=True,original_native=True,localization_inputs=('original_lidar','original_imu'),
                motion_prior=False,path_break=False,scan_cdr_sha256=f.scan_cdr_sha256[1],imu_cdr_sha256=f.imu_cdr_sha256,
                scan_gid=f.scan_gid,imu_gid=f.imu_gid,source_session=ctx['source_session'],map_epoch=ctx['map_epoch'],map_frame=ctx['map_frame'])
            grid=dict(cells=frozen,resolution_m=r,origin_xy=(gx*r,gy*r),frame_id=ctx['map_frame'],epoch=ctx['map_epoch'],
                source_stamp_ns=f.scan_stamps_ns[1],receipt_stamp_ns=f.scan_receipts_ns[1],oldest_observed_source_ns=oldest,
                accepted=True,original_native=True,source_session=ctx['source_session'])
            return dict(context=ctx,pose=pose,occupancy=grid,source_now_ns=source_now_ns,receipt_now_ns=receipt_now_ns)
