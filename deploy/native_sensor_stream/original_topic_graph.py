"""Per-explicit-Start original graph binding. Import/default creates no ROS."""
from dataclasses import dataclass
import math
import re
import time
import uuid


EXPECTED = (
    ('/scan', 'sensor_msgs/msg/LaserScan', 'sllidar_node', '/'),
    ('/imu/data_raw', 'sensor_msgs/msg/Imu', 'ubts_bno085_imu_driver', '/'),
    ('/gps/data', 'std_msgs/msg/String', 'ubts_gps_driver', '/'),
    ('/gps/fix', 'sensor_msgs/msg/NavSatFix', 'ubts_gps_driver', '/'),
)


def require(ok, why):
    if not ok:
        raise ValueError(why)


@dataclass(frozen=True)
class PublisherIdentity:
    topic: str
    ros_type: str
    node_name: str
    namespace: str
    gid_hex: str


@dataclass(frozen=True)
class ResolvedPublishers:
    owner_session: str
    owner_generation: int
    publishers: tuple

    def gid(self, topic):
        return next(p.gid_hex for p in self.publishers if p.topic == topic)


class GraphBindingDenied(ValueError):
    def __init__(self, reason, cleanup_verified, observed=None):
        super().__init__(reason)
        self.cleanup_verified = cleanup_verified is True
        self.observed = observed


class IronGraphOnly:
    """Iron low-level node: unlike rclpy.Node, no parameter-event publisher.

    SDK methods are cooperative. The caller tracks the activation thread and
    retains owner generation if destruction/join is not proved.
    """
    def __init__(self):
        self.context = self.node = self.sdk = None
        self.context_attempted = self.node_attempted = False

    def open(self):
        from rclpy.context import Context
        from rclpy.impl.implementation_singleton import rclpy_implementation
        self.sdk = rclpy_implementation
        self.context = Context()
        self.context_attempted = True
        self.context.init(args=[], initialize_logging=False, domain_id=0)
        self.node_attempted = True
        with self.context.handle:
            self.node = self.sdk.Node('ubts_original_graph_' + uuid.uuid4().hex,
                '/', self.context.handle, [], False, False)

    def publishers(self, topic):
        with self.node:
            return self.sdk.rclpy_get_publishers_info_by_topic(self.node, topic, False)

    def close(self):
        if self.node_attempted and self.node is None:
            return False
        if self.context_attempted and (self.context is None or self.context.handle is None):
            return False
        try:
            if self.node is not None:
                self.node.destroy_when_not_in_use()
                self.node = None
            if self.context is not None:
                self.context.try_shutdown()
                self.context.destroy()
                self.context = None
            return True
        except BaseException:
            return False


class OriginalTopicResolver:
    def __init__(self, contract, *, include_gps=False, ops_factory=IronGraphOnly,
                 clock=time.monotonic, timeout=6.0):
        require(type(include_gps) is bool, 'typed_optional_original_GPS_required')
        require(type(timeout) in (int, float) and math.isfinite(timeout) and
                0 < timeout <= 6.0, 'bounded_original_graph_deadline_required')
        require(type(contract) is dict and contract.get('topics') == ['/scan', '/imu/data_raw'] and
                contract.get('expected_publisher_gid_bytes') == 16 and
                contract.get('original_cdr_required') is True and
                contract.get('original_headers_required') is True,
                'exact_original_observer_topics_required')
        if include_gps:
            require(contract.get('optional_original_GNSS', {}).get('topics') ==
                dict((x[0], x[1]) for x in EXPECTED[2:]), 'exact_original_GPS_topics_required')
        require(callable(ops_factory) and callable(clock), 'original_graph_hooks_required')
        self.expected = EXPECTED if include_gps else EXPECTED[:2]
        self.ops_factory, self.clock, self.timeout = ops_factory, clock, float(timeout)
        self._unresolved_ops = None  # retain exact SDK owner on unproved destruction
        self.last_observed = None

    def _snapshot(self, ops):
        found = []
        pending = False
        observed = {}
        self.last_observed = observed
        for topic, kind, node, namespace in self.expected:
            rows = ops.publishers(topic)
            require(type(rows) is list and len(rows) <= 8, 'bounded_original_graph_rows_required')
            # Last bounded graph snapshot only; diagnostics are not source freshness.
            observed[topic] = []
            for item in rows:
                if type(item) is dict:
                    entry = {}
                    for key in ('topic_type', 'node_name', 'node_namespace'):
                        value = item.get(key)
                        entry[key] = value[:128] if type(value) is str else {'type': type(value).__name__}
                    gid = item.get('endpoint_gid')
                    entry['endpoint_gid_hex'] = (bytes(gid).hex() if type(gid) in (bytes,list,tuple)
                        and len(gid) <= 32 and all(type(n) is int and 0 <= n <= 255 for n in gid)
                        else {'type': type(gid).__name__})
                    observed[topic].append(entry)
                else:
                    observed[topic].append({'type': type(item).__name__})
            if not rows:
                pending = True
                continue
            require(len(rows) == 1, 'ambiguous_original_publisher:' + topic)
            row = rows[0]
            require(type(row) is dict and row.get('topic_type') == kind,
                    'original_publisher_identity_required:' + topic)
            gid = row.get('endpoint_gid')
            require(type(gid) in (bytes, list, tuple) and len(gid) == 16 and
                all(type(n) is int and 0 <= n <= 255 for n in gid),
                'original_publisher_GID16_required:' + topic)
            raw = bytes(gid)
            require(any(raw), 'nonzero_original_publisher_GID_required:' + topic)
            unknown = False
            for key, expected, marker in (('node_name', node, '_NODE_NAME_UNKNOWN_'),
                    ('node_namespace', namespace, '_NODE_NAMESPACE_UNKNOWN_')):
                value = row.get(key)
                require(type(value) is str and (value == expected or value == marker),
                        'original_publisher_identity_required:' + topic)
                unknown = unknown or value == marker
            if unknown:
                pending = True
            found.append(PublisherIdentity(topic, kind, node, namespace, raw.hex()))
        # Pending earlier entries cannot conceal later known conflicts or aliases.
        require(len(set(p.gid_hex for p in found)) == len(found), 'distinct_original_publisher_GIDs_required')
        return None if pending else tuple(found)

    def resolve(self, context):
        require(self._unresolved_ops is None, 'original_graph_previous_cleanup_unproved')
        require(type(context.session) is str and re.fullmatch('[0-9a-f]{32}', context.session) and
                type(context.generation) is int and 0 < context.generation < 2**63,
                'exact_original_graph_owner_required')
        deadline = self.clock() + self.timeout
        ops = None
        opened = False
        answer = error = None
        try:
            require(not context.cancelled, 'original_graph_Start_cancelled')
            opened = True  # track open attempt, including a partial SDK creation
            ops = self.ops_factory()
            ops.open()
            prior = None
            while True:
                require(not context.cancelled, 'original_graph_Start_cancelled')
                require(self.clock() < deadline, 'original_graph_discovery_deadline')
                current = self._snapshot(ops)
                require(not context.cancelled, 'original_graph_Start_cancelled')
                require(self.clock() < deadline, 'original_graph_discovery_deadline')
                if current is not None and current == prior:
                    answer = ResolvedPublishers(context.session, context.generation, current)
                    break
                prior = current
                require(not context.wait_cancel(min(.02, max(0., deadline-self.clock()))),
                        'original_graph_Start_cancelled')
        except BaseException as exc:
            error = str(exc)[:256]
        cleanup = not opened
        if ops is not None:
            try:
                cleanup = ops.close() is True
            except BaseException:
                cleanup = False
        if not cleanup or error is not None:
            if not cleanup:
                self._unresolved_ops = ops if ops is not None else object()
            raise GraphBindingDenied(error or 'original_graph_cleanup_unproved', cleanup, self.last_observed)
        if context.cancelled:
            raise GraphBindingDenied('original_graph_Start_cancelled', True)
        return answer
