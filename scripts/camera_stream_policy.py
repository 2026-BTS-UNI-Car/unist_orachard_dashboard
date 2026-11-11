"""Camera preview policy only; source capture, archives and other sensors are untouched."""
import math
import time
from collections import deque

from scripts.stream_policy import PROFILES


class CameraStreamBudget:
    """Preserve Auto detail before cadence, with one ACK-serviced frame at a time.

    ACK service rate includes RTT and browser work; it is not link bandwidth.
    Explicit Tiny/Slow are retained for an intentional emergency tradeoff.
    """
    TIERS = ((640, 360), (960, 540), (1280, 720))
    MAX_BYTES_S = 4_000_000
    QUALITY = 70

    def __init__(self, profile="auto", local_camera_30fps=False, *, clock=None):
        self.profile = profile if profile in {*PROFILES, "auto"} else "auto"
        self.level = 0
        self.clock = clock or time.monotonic
        self.ceiling = 5_000_000 if local_camera_30fps else self.MAX_BYTES_S
        self.ack_latency_ms = None
        self.service_s = None
        self.service_bytes = None
        self.fast_count = self.slow_count = 0
        self.next_upgrade_at = self.clock() + 3.0
        self.delivery_state = "starting"
        self.last_decode_status = "pending"

    @property
    def effective_profile(self):
        return ("readable", "clear", "detail")[self.level] if self.profile == "auto" else self.profile

    @property
    def values(self):
        if self.profile in ("tiny", "slow"):
            return PROFILES[self.profile]
        width, height = self.TIERS[self.level] if self.profile == "auto" else ((640, 360) if self.profile == "balanced" else (1280, 720))
        fps, byte_rate = 30., float(self.ceiling)
        if self.service_s is not None:
            # A fraction of observed service capacity reserves headroom. Both
            # limits describe the same interval; FramePacer takes their max.
            # FramePacer's timer floor is 1 FPS. Slower updates remain bounded
            # by byte spacing; report the actual timer ceiling, not a lower
            # fractional value the shared timer does not enforce.
            fps = max(1., min(fps, .85 / self.service_s))
            byte_rate = min(byte_rate, .85 * self.service_bytes / self.service_s)
        return width, height, fps, self.QUALITY, max(1., byte_rate)

    def acknowledge(self, elapsed, frame_bytes, *, decode_status="unknown"):
        self.last_decode_status = decode_status
        valid = (isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool)
                 and math.isfinite(elapsed) and elapsed > 0
                 and isinstance(frame_bytes, int) and not isinstance(frame_bytes, bool) and frame_bytes > 0)
        if not valid:
            self.delivery_state = "invalid_feedback"
            self.fast_count = 0
            return
        self.ack_latency_ms = round(elapsed * 1000, 2)
        alpha = .25
        self.service_s = elapsed if self.service_s is None else (1-alpha)*self.service_s + alpha*elapsed
        self.service_bytes = frame_bytes if self.service_bytes is None else (1-alpha)*self.service_bytes + alpha*frame_bytes
        self.delivery_state = "bandwidth_limited" if self.service_s > .35 else "live"
        if decode_status != "decoded":
            self.delivery_state = "decode_" + decode_status
            self.fast_count = 0
            return
        if self.profile != "auto":
            return
        now = self.clock()
        self.slow_count = self.slow_count + 1 if elapsed > .45 else 0
        if elapsed > 1.0 or self.slow_count >= 3:
            self.level = max(0, self.level-1)
            self.fast_count = self.slow_count = 0
            self.next_upgrade_at = now + 5.0
        elif elapsed < .22 and self.service_s < .22 and now >= self.next_upgrade_at:
            self.fast_count += 1
            if self.fast_count >= 10:
                self.level = min(len(self.TIERS)-1, self.level+1)
                self.fast_count = 0
                self.next_upgrade_at = now + 5.0
        else:
            self.fast_count = 0

    def status(self):
        width, height, fps, quality, rate = self.values
        return dict(camera_policy="readability_first_v1", effective_profile=self.effective_profile,
                    output_limit_width=width, output_limit_height=height, preview_quality=quality,
                    effective_fps_limit=round(fps, 2), budget_bytes_s=round(rate),
                    ack_latency_ms=self.ack_latency_ms, delivery_state=self.delivery_state,
                    decode_status=self.last_decode_status,
                    automatic_quality_floor=self.profile == "auto")


class CameraWindowStreamBudget(CameraStreamBudget):
    """Explicit V2 only: a window/ACK-service ceiling, never measured bandwidth.

    The legacy budget and its EMA are unchanged. N concurrent serviced frames
    have at most N/RTT completion capacity; the configured byte ceiling still
    applies independently and FramePacer still spaces actual packet bytes.
    """
    def __init__(self, *args, ack_window, **kwargs):
        if type(ack_window)is not int or ack_window not in (2,3):
            raise ValueError('explicit_camera_v2_window_2_or_3')
        self.ack_window=ack_window
        super().__init__(*args,**kwargs)

    @property
    def values(self):
        width,height,fps,quality,rate=super().values
        if self.profile in ('tiny','slow')or self.service_s is None:
            return width,height,fps,quality,rate
        fps=max(1.,min(30.,.85*self.ack_window/self.service_s))
        rate=min(float(self.ceiling),.85*self.ack_window*self.service_bytes/self.service_s)
        return width,height,fps,quality,max(1.,rate)

    def status(self):
        result=super().status()
        result.update(camera_policy='readability_first_window_v2',ack_window=self.ack_window,
            service_capacity_basis='negotiated_window_over_ACK_service_time_upper_bound',
            service_estimate_is_link_bandwidth=False)
        return result


class CameraAckWindow:
    """Bounded metadata credits; no image bytes or actuator authority are kept.

    A connection/source change closes the stream rather than issuing fresh
    credits while older TCP frames remain unacknowledged. Only the exact
    session/epoch/sequence/frame ACK releases its own credit.
    """
    MAX_PACKET_BYTES=5*1024*1024
    MAX_INFLIGHT_BYTES=8*1024*1024
    ACK_FIELDS={'type','ack_protocol','stream_session','source_epoch','delivery_sequence','frame_count','decode_status'}
    STATUSES={'decoded','error','timeout','not_visible','unknown','superseded'}
    REASONS=('accepted','accepted_out_of_order','duplicate','old_ack','future','wrong_session','wrong_source','wrong_frame','malformed','expired','closed')

    def __init__(self, limit, stream_session, *, timeout_s=8., clock=None):
        if type(limit)is not int or limit not in (2,3)or type(stream_session)is not str or len(stream_session)!=32 or any(c not in '0123456789abcdef'for c in stream_session):
            raise ValueError('explicit_bounded_camera_window_session')
        if not isinstance(timeout_s,(int,float))or isinstance(timeout_s,bool)or not math.isfinite(timeout_s)or not 0<timeout_s<=8.:
            raise ValueError('original_camera_absolute_ACK_deadline')
        self.limit,self.stream_session=limit,stream_session
        self.clock=clock or time.monotonic;self.timeout_s=timeout_s
        self.source_epoch=1;self.source=None;self.source_count=None;self.source_stamp=None
        self.pending={};self.bytes=0;self.last_sequence=0;self.highest_acked=0
        self.recent=deque(maxlen=16);self.counts=dict.fromkeys(self.REASONS,0)
        self.peak_frames=self.peak_bytes=0;self.closed=False

    def _count(self,reason):
        self.counts[reason]=min(2**53-1,self.counts[reason]+1)
        return None

    def observe_source(self,key,count,stamp):
        if self.closed:raise ValueError('camera_window_closed')
        if type(key)is not tuple or type(count)is not int or not 0<count<2**53 or not isinstance(stamp,(int,float))or isinstance(stamp,bool)or not math.isfinite(stamp)or stamp<0:
            raise ValueError('camera_source_changed')
        if self.source is None:self.source=key
        elif key!=self.source or count<self.source_count or stamp<self.source_stamp or(count==self.source_count and stamp!=self.source_stamp):
            raise ValueError('camera_source_changed')
        self.source_count,self.source_stamp=count,stamp

    def can_send(self,size=1):
        return (not self.closed and type(size)is int and 0<size<=self.MAX_PACKET_BYTES and
            len(self.pending)<self.limit and self.bytes+size<=self.MAX_INFLIGHT_BYTES)

    def deadline(self):
        return min((row['sent']+self.timeout_s for row in self.pending.values()),default=None)

    def expired(self,now=None):
        deadline=self.deadline();return deadline is not None and (self.clock()if now is None else now)>=deadline

    def register(self,count,size,*,now=None):
        now=self.clock()if now is None else now
        if self.source is None or count!=self.source_count:raise ValueError('camera_exact_source_frame_required')
        if not self.can_send(size)or self.last_sequence>=2**53-1:raise ValueError('camera_window_frame_or_byte_bound')
        if not math.isfinite(now)or now<0 or self.expired(now):raise ValueError('camera_window_expired')
        self.last_sequence+=1
        self.pending[self.last_sequence]=dict(frame_count=count,bytes=size,sent=now)
        self.bytes+=size;self.peak_frames=max(self.peak_frames,len(self.pending));self.peak_bytes=max(self.peak_bytes,self.bytes)
        return self.last_sequence

    def acknowledge(self,payload,*,now=None):
        now=self.clock()if now is None else now
        if self.closed:return self._count('closed')
        if type(payload)is not dict or set(payload)!=self.ACK_FIELDS or payload.get('type')!='frame_ack'or type(payload.get('ack_protocol'))is not int or payload['ack_protocol']!=2:
            return self._count('malformed')
        if payload['stream_session']!=self.stream_session:return self._count('wrong_session')
        if type(payload['source_epoch'])is not int or payload['source_epoch']!=self.source_epoch:return self._count('wrong_source')
        seq,count,status=payload['delivery_sequence'],payload['frame_count'],payload['decode_status']
        if type(seq)is not int or not 0<seq<2**53 or type(count)is not int or not 0<count<2**53 or type(status)is not str or status not in self.STATUSES:
            return self._count('malformed')
        if seq>self.last_sequence:return self._count('future')
        row=self.pending.get(seq)
        if row is None:return self._count('duplicate'if seq in self.recent else'old_ack')
        if count!=row['frame_count']:return self._count('wrong_frame')
        if not math.isfinite(now)or now<row['sent']or now>=row['sent']+self.timeout_s:return self._count('expired')
        del self.pending[seq];self.bytes-=row['bytes'];self.recent.append(seq)
        self._count('accepted_out_of_order'if seq<self.highest_acked else'accepted')
        self.highest_acked=max(self.highest_acked,seq)
        return dict(elapsed=now-row['sent'],bytes=row['bytes'],decode_status=status,delivery_sequence=seq)

    def status(self):
        return dict(ack_protocol=2,source_epoch=self.source_epoch,max_inflight_frames=self.limit,
            inflight_frames=len(self.pending),inflight_bytes=self.bytes,max_inflight_bytes=self.MAX_INFLIGHT_BYTES,
            max_packet_bytes=self.MAX_PACKET_BYTES,peak_inflight_frames=self.peak_frames,peak_inflight_bytes=self.peak_bytes,
            ack_dispositions=dict(self.counts))

    def close(self):
        self.closed=True;self.pending.clear();self.bytes=0
