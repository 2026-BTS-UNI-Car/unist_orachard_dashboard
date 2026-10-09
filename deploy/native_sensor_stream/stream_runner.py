"""Explicit persistent original-native shadow stream; default CLI is inert.

Only one injected, owned Popen is supervised. This runner does not publish ROS,
control a device, restart a driver, archive raw rows, or grant BODY/autonomy
authority. stream_rows owns the original CDR/GID/RAW/freshness checks. Process
factories and adapter hooks are trusted cooperative seams: an unknown creation
or unjoined reader retains lifecycle ownership. No automatic restart exists.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import stat
import subprocess
import sys
import threading
import time

from dashboard_server.native_owner_lifecycle import NativeOwnerLifecycle, ResourceHooks

MAX_LINE = 2*262144 + 8192
QUEUE_ROWS = 16
STDERR_BYTES = 64 * 1024
SCHEMA = "ubts.native.original_stream.runner.v1"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def bounded_timeout(value):
    require(type(value) in (int, float) and 0 <= value <= 60 and math.isfinite(value),
            "bounded_stream_timeout_required")
    return float(value)


def reason(error):
    try:
        return (type(error).__name__ + ": " + str(error))[:256]
    except BaseException:
        return type(error).__name__[:256]


def bounded_status(value):
    require(type(value) is dict, "stream_protocol_status_required")
    raw = json.dumps(value, allow_nan=False, separators=(",", ":"))
    require(len(raw.encode("utf8")) <= 8192, "stream_protocol_status_capacity")
    return json.loads(raw)


class _StreamResource:
    def __init__(self, context, adapter_factory, process_factory, identity, thread_factory):
        self.context, self.adapter_factory = context, adapter_factory
        self.process_factory, self.identity, self.thread_factory = process_factory, identity, thread_factory
        self.lock = threading.RLock()
        self.rows = queue.Queue(maxsize=QUEUE_ROWS)
        self.adapter = self.child = None
        self.threads = []
        self.stdout_done, self.stderr_done = threading.Event(), threading.Event()
        self.worker_done, self.stop_requested = threading.Event(), threading.Event()
        self.admission_failed = threading.Event()
        self.explicit_stop = self.unknown = self.joined = self.closed = False
        self.terminate_attempted = self.kill_attempted = self.adapter_stop_attempted = False
        self.lines_received = self.lines_processed = self.stdout_bytes = self.stderr_bytes = 0
        self.queue_peak = self.diagnostics = self.publications = 0
        self.discarded_after_cleanup = 0
        self.discarded_after_failure = 0
        self.drained_chunks_after_failure = self.drained_bytes_after_failure = 0
        self.stderr = bytearray()
        self.first_error = None
        self.protocol = None
        self.adapter_final = None
        self.adapter_live = None
        self.adapter_fault_status_error = None

    def fail(self, error):
        with self.lock:
            self.first_error = self.first_error or reason(error)
            self.admission_failed.set()
        self.context.fail(error)

    def activate(self):
        # Return exact hooks even after partial activation. Generic factory
        # exceptions must never lose a Popen/reader that already exists.
        try:
            self.adapter = self.adapter_factory()
            require(all(callable(getattr(self.adapter, name, None)) for name in
                        ("bind_process", "receive_line", "stop", "finish", "status")),
                    "original_stream_adapter_required")
            if self.context.cancelled:
                return
            with self.lock:
                self.unknown = True  # a raising factory may have created a handle
            child = self.process_factory()
            self.child = child
            with self.lock:
                self.unknown = False
            require(type(child.pid) is int and child.pid > 0 and
                    child.stdout is not None and child.stderr is not None,
                    "owned_native_Popen_and_two_binary_pipes_required")
            self.adapter.bind_process(child.pid, self.identity())
            if self.context.cancelled:
                return
            for name, target in (("stdout", self.read_stdout), ("stderr", self.read_stderr),
                                 ("worker", self.work)):
                thread = self.thread_factory(target=target, name="native-stream-" + name,
                                             daemon=False)
                self.threads.append(thread)  # pretrack before Thread.start
                thread.start()
        except BaseException as error:
            self.fail(error)
            # Adapter factory exceptions can hide partial receiver resources.
            if self.adapter is None:
                self.unknown = True
        finally:
            # Stop may have run while the adapter/Popen factory was in flight.
            # Reconcile the newly returned exact handle, never a newer owner.
            if self.context.cancelled:
                self.request_stop()

    def read_stdout(self):
        try:
            while True:
                raw = self.child.stdout.readline(MAX_LINE + 1)
                if not raw:
                    break
                require(type(raw) is bytes and len(raw) <= MAX_LINE + 1,
                        "bounded_native_stdout_read_required")
                valid_line = len(raw) <= MAX_LINE and raw.endswith(b"\n")
                overflow = False
                with self.lock:
                    self.stdout_bytes += len(raw)  # visibility only, no cumulative cap
                    self.lines_received += int(valid_line)
                    draining = (self.admission_failed.is_set() or self.worker_done.is_set() or
                                (self.context.cancelled and not self.explicit_stop))
                    if valid_line and not draining:
                        try:
                            self.rows.put_nowait(raw)
                        except queue.Full:
                            overflow = True
                        self.queue_peak = max(self.queue_peak, self.rows.qsize())
                if not valid_line and not self.admission_failed.is_set():
                    self.fail(ValueError("native_stdout_line_capacity_or_unterminated"))
                elif overflow:
                    self.fail(ValueError("native_stdout_processing_capacity_exceeded"))
                # A failed worker cannot relieve its queue. Continue bounded
                # reads through EOF so producer writes can finish and SIGTERM
                # can be observed. These bytes never reach the adapter/history.
                # Oversized lines are drained as bounded fragments, never joined.
                if draining or not valid_line or overflow:
                    with self.lock:
                        self.drained_chunks_after_failure += 1
                        self.drained_bytes_after_failure += len(raw)
        except BaseException as error:
            self.fail(error)
        finally:
            self.stdout_done.set()

    def read_stderr(self):
        try:
            while True:
                chunk = self.child.stderr.read(4096)
                if not chunk:
                    break
                require(type(chunk) is bytes and len(chunk) <= 4096,
                        "bounded_native_stderr_chunk_required")
                with self.lock:
                    self.stderr_bytes += len(chunk)
                    self.stderr.extend(chunk)
                    if len(self.stderr) > STDERR_BYTES:
                        del self.stderr[:-STDERR_BYTES]
        except BaseException as error:
            self.fail(error)
        finally:
            self.stderr_done.set()

    def work(self):
        try:
            while True:
                if self.admission_failed.is_set() or (self.context.cancelled and not self.explicit_stop):
                    break  # fault: no next-generation publication or admission
                try:
                    raw = self.rows.get(timeout=.02)
                except queue.Empty:
                    if self.stdout_done.is_set() and self.rows.empty():
                        if not self.explicit_stop:
                            raise ValueError("native_stream_unexpected_EOF")
                        break
                    continue
                if self.admission_failed.is_set():
                    with self.lock:
                        self.discarded_after_failure += 1
                    break
                # The EXACT original bytes reach the adapter. No parse/reencode
                # of CDR/header/GID/RAW takes place in this runner.
                if self.explicit_stop:
                    self.request_stop()  # inhibit admission even before lifecycle hook dispatch
                records = self.adapter.receive_line(raw)
                require(type(records) is tuple and len(records) <= 16,
                        "bounded_physical_pair_outputs_required")
                live_status = bounded_status(self.adapter.status())
                with self.lock:
                    self.lines_processed += 1
                    self.adapter_live = live_status
                for item in records:
                    require(type(item) is dict and set(item) == {"diagnostic", "evidence"},
                            "physical_stream_record_required")
                    diagnostic = item["diagnostic"]
                    require(type(diagnostic) is dict and
                            diagnostic.get("runtime_authority") is False and
                            diagnostic.get("motor_command_sent") is False and
                            diagnostic.get("autonomy_output_enabled") is False and
                            "body_velocity_mps" in diagnostic and diagnostic["body_velocity_mps"] is None,
                            "physical_stream_HOLD_only_required")
                    with self.lock:
                        self.diagnostics += 1
                        # Serialize publication with the first fault latch,
                        # including the interval before context.fail cancels.
                        if not self.admission_failed.is_set() and self.context.publish(diagnostic, item["evidence"]):
                            self.publications += 1
        except BaseException as error:
            self.fail(error)
            try:
                # Fault timing belongs to this failed row, not the last
                # successful diagnostic. Capture only bounded adapter status.
                live_status = bounded_status(self.adapter.status())
                with self.lock:
                    self.adapter_live = live_status
            except BaseException as status_error:
                with self.lock:
                    self.adapter_fault_status_error = reason(status_error)
        finally:
            self.worker_done.set()

    def request_stop(self):
        self.stop_requested.set()
        errors = []
        with self.lock:
            adapter_stop = self.adapter is not None and not self.adapter_stop_attempted
            self.adapter_stop_attempted = self.adapter_stop_attempted or adapter_stop
            terminate = self.child is not None and not self.terminate_attempted
            self.terminate_attempted = self.terminate_attempted or terminate
        if adapter_stop:
            try:
                self.adapter.stop()
            except BaseException as error:
                errors.append(error)
        if terminate:
            try:
                if self.child.poll() is None:
                    self.child.terminate()  # only this exact Popen
            except BaseException as error:
                errors.append(error)
        for error in errors:
            self.fail(error)

    def join(self, timeout):
        # NativeOwnerLifecycle reconciles a cancelled factory automatically.
        # A fault remains owned until the caller explicitly requests Stop here.
        if not self.explicit_stop:
            return False
        deadline = time.monotonic() + bounded_timeout(timeout)
        child = self.child
        if child is not None:
            try:
                if child.poll() is None:
                    # Reserve part of the caller's original deadline for kill
                    # and readers. No second timeout renews the total budget.
                    child.wait(timeout=min(1., max(0., deadline-time.monotonic()) / 2))
            except subprocess.TimeoutExpired:
                with self.lock:
                    kill = not self.kill_attempted
                    self.kill_attempted = True
                if kill:
                    try:
                        child.kill()
                    except BaseException as error:
                        self.fail(error)
                try:
                    child.wait(timeout=max(0., deadline-time.monotonic()))
                except BaseException as error:
                    self.fail(error)
            except BaseException as error:
                self.fail(error)
        threads_joined = True
        for thread in self.threads:
            try:
                thread.join(max(0., deadline-time.monotonic()))
                threads_joined = threads_joined and not thread.is_alive()
            except BaseException as error:
                self.fail(error)
                threads_joined = False
        exited = child is None or child.poll() is not None
        self.joined = exited and threads_joined and not self.unknown
        if self.joined and self.adapter is not None and self.protocol is None:
            try:
                self.protocol = bounded_status(self.adapter.finish(None if child is None else child.poll(), True))
                self.adapter_final = bounded_status(self.adapter.status())
                if child is None:
                    self.protocol = dict(ok=True, owner_cleanup=True, process_started=False,
                                         ready_observed=False, data_ready_before_stop=False)
                if self.admission_failed.is_set():
                    # Pipe drainage cannot qualify discarded CDR/DONE or data.
                    self.protocol = dict(self.protocol, ok=False, protocol_qualified=False)
                if child is not None:
                    require(self.protocol.get("ok") is True and self.protocol.get("owner_cleanup") is True,
                            "native_stream_protocol_unqualified:" + str(self.protocol.get("error"))[:128])
            except BaseException as error:
                self.fail(error)
                if self.protocol is None:
                    self.protocol = {"owner_cleanup": False, "protocol_qualified": False,
                                     "error": reason(error)}
        return self.joined

    def close(self):
        if not self.joined or self.unknown:
            return False
        errors = []
        if self.child is not None:
            for pipe in (self.child.stdout, self.child.stderr):
                try:
                    pipe.close()  # never close a pipe underneath an unjoined reader
                except BaseException as error:
                    errors.append(error)
        if self.adapter is not None and callable(getattr(self.adapter, "close", None)):
            try:
                require(self.adapter.close() is True, "stream_adapter_close_unproved")
            except BaseException as error:
                errors.append(error)
        for error in errors:
            self.fail(error)
        self.closed = not errors
        if self.closed:
            self.adapter = None  # release bounded physical caches, retain only status
            while True:
                try:
                    self.rows.get_nowait()
                    self.discarded_after_cleanup += 1
                except queue.Empty:
                    break  # discard failed-prefix tail only after all producers joined
        return self.closed

    def snapshot(self):
        with self.lock:
            return dict(process_pid=None if self.child is None else self.child.pid,
                exit_code=None if self.child is None else self.child.poll(),
                native_lines_received=self.lines_received, native_lines_processed=self.lines_processed,
                native_stdout_bytes=self.stdout_bytes, native_stderr_bytes=self.stderr_bytes,
                queue_depth=self.rows.qsize(), queue_peak=self.queue_peak, queue_limit=QUEUE_ROWS,
                queued_rows_discarded_after_cleanup=self.discarded_after_cleanup,
                queued_rows_discarded_after_failure=self.discarded_after_failure,
                stdout_chunks_drained_after_failure=self.drained_chunks_after_failure,
                stdout_bytes_drained_after_failure=self.drained_bytes_after_failure,
                diagnostics=self.diagnostics, publications=self.publications,
                stderr_tail=self.stderr.decode("utf8", errors="replace"),
                stderr_retained_bytes=len(self.stderr), stderr_limit=STDERR_BYTES,
                first_error=self.first_error, explicit_stop=self.explicit_stop,
                readers_joined=self.joined, resources_closed=self.closed,
                creation_unknown=self.unknown,
                terminate_attempted=self.terminate_attempted, kill_attempted=self.kill_attempted,
                protocol=None if self.protocol is None else bounded_status(self.protocol),
                adapter_last_processed_row=None if self.adapter_live is None else bounded_status(self.adapter_live),
                adapter_fault_status_error=self.adapter_fault_status_error,
                adapter_final=None if self.adapter_final is None else bounded_status(self.adapter_final),
                raw_rows_archived=False,
                runtime_authority=False, motor_command_sent=False,
                autonomy_output_enabled=False, body_velocity_mps=None)


class NativeStreamRunner:
    """Inert until explicit Start; injected factories create one owner generation."""
    def __init__(self, adapter_factory, process_factory, *, identity=None,
                 thread_factory=threading.Thread):
        require(callable(adapter_factory) and callable(process_factory) and callable(thread_factory),
                "explicit_stream_factories_required")
        self.lock, self.resource = threading.RLock(), None
        self.adapter_factory, self.process_factory = adapter_factory, process_factory
        self.identity = identity or (lambda: os.readlink("/proc/self/ns/time"))
        require(callable(self.identity), "process_time_namespace_identity_required")
        self.thread_factory = thread_factory
        self.pending_explicit_stop_generation = None
        self.lifecycle = NativeOwnerLifecycle(self._factory)

    def _factory(self, context):
        resource = _StreamResource(context, self.adapter_factory, self.process_factory,
                                   self.identity, self.thread_factory)
        with self.lock:
            self.resource = resource
            resource.explicit_stop = self.pending_explicit_stop_generation == context.generation
        resource.activate()
        return ResourceHooks(resource.request_stop, resource.join, resource.close)

    def start(self, session=None):
        return self.lifecycle.start(session)

    def stop(self, timeout=3.):
        deadline = time.monotonic() + bounded_timeout(timeout)
        # Use the existing lifecycle's generation-bound cleanup seam, including
        # its tiny owner-created / factory-not-yet-entered window. Do not hold
        # its state lock while waiting for processes or reader threads.
        with self.lifecycle._condition:
            owner = self.lifecycle._owner
            if owner is not None:
                with self.lock:
                    self.pending_explicit_stop_generation = owner.generation
                    resource = self.resource
                    if resource is not None and resource.context.generation == owner.generation:
                        resource.explicit_stop = True
        return self.lifecycle._stop_owner(owner, deadline)

    def status(self):
        owner = self.lifecycle.status()
        result = asdict(owner)
        if owner.latest is not None:
            result["latest"] = dict(session=owner.latest.session, generation=owner.latest.generation,
                sequence=owner.latest.sequence, diagnostic=json.loads(owner.latest.diagnostic_json),
                evidence=None if not owner.latest.evidence_json else json.loads(owner.latest.evidence_json))
        with self.lock:
            resource = self.resource
        return dict(schema=SCHEMA, owner=result,
                    stream=None if resource is None else resource.snapshot(),
                    runtime_authority=False, motor_command_sent=False,
                    autonomy_output_enabled=False, body_velocity_mps=None)


def sha_file(path, cap=32*1024*1024):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
            "bounded_regular_stream_input_required")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for raw in iter(lambda: source.read(65536), b""):
            digest.update(raw)
    after = path.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    require(tuple(getattr(before, key) for key in fields) == tuple(getattr(after, key) for key in fields),
            "stream_input_changed_during_SHA")
    return digest.hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_stream_manifest_key")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("nonfinite_stream_manifest_number")

    def finite(value):
        number = float(value)
        require(math.isfinite(number), "nonfinite_stream_manifest_number")
        return number

    return json.loads(raw.decode("utf8", errors="strict"), object_pairs_hook=unique,
                      parse_constant=invalid, parse_float=finite)


def read_regular(path, cap=1024*1024):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= cap,
            "bounded_regular_stream_input_required")
    with path.open("rb") as source:
        raw = source.read(cap+1)
    after = path.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
    require(len(raw) <= cap and tuple(getattr(before, key) for key in fields) ==
            tuple(getattr(after, key) for key in fields), "stream_input_changed_during_read")
    return raw


def verify_stream_manifest(path, expected_sha, args):
    """Root build/image logs supply provenance; this enforces their exact bytes."""
    path = Path(path).resolve(strict=True)
    raw = read_regular(path)
    require(hashlib.sha256(raw).hexdigest() == expected_sha, "reviewed_stream_manifest_SHA_required")
    manifest = strict_json(raw)
    require(type(manifest) is dict and manifest.get("schema") == "ubts.actual.native_stream.build.v1",
            "reviewed_stream_build_schema_required")
    for key, expected in (("observer_contract_sha256", args.observer_contract_sha256),
                          ("observer_binary_sha256", args.observer_binary_sha256),
                          ("physical_package_manifest_sha256", args.package_manifest_sha256)):
        require(manifest.get(key) == expected, "stream_build_digest_binding_required:" + key)
    authority = ("runtime_authority", "motor_command_sent", "autonomy_output_enabled", "mission_admission")
    require(all(manifest.get(key) is False for key in authority), "stream_build_authority_forbidden")
    files = manifest.get("files_sha256")
    expected_files = {"stream_runner.py", "stream_rows.py", "dashboard_server/native_owner_lifecycle.py",
                      "observer-src/actual_cdr_stream.cpp", "observer-src/CMakeLists.txt", "OBSERVER_CONTRACT.json"}
    require(type(files) is dict and set(files) == expected_files, "exact_stream_source_closure_required")
    base = path.parent
    for name, expected in files.items():
        require(type(name) is str and len(name) <= 256 and "\\" not in name and
                not Path(name).is_absolute() and ".." not in Path(name).parts and
                type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected) is not None,
                "bounded_relative_stream_source_SHA_required")
        source = base / name
        require(source.resolve(strict=True).is_relative_to(base) and sha_file(source, 1024*1024) == expected,
                "stream_build_source_changed:" + name)
    require(files["OBSERVER_CONTRACT.json"] == args.observer_contract_sha256,
            "stream_contract_file_digest_required")
    contract = strict_json(read_regular(base / "OBSERVER_CONTRACT.json"))
    require(type(contract) is dict and contract.get("schema") == "ubts.actual.native_stream.observer_contract.v1" and
            all(contract.get(key) is False for key in authority), "reviewed_stream_contract_schema_and_HOLD_required")
    require(contract.get("source_sha256") == {name: files[name] for name in
        ("observer-src/actual_cdr_stream.cpp", "observer-src/CMakeLists.txt")},
        "stream_contract_CPP_source_binding_required")
    return base


def load_physical_package(path, expected_sha):
    """Explicit isolated-process validation; no package/binary IO on default plan."""
    path = Path(path).resolve(strict=True)
    handoff = path / "HANDOFF.json"
    require(sha_file(handoff) == expected_sha, "reviewed_physical_package_manifest_required")
    manifest = strict_json(read_regular(handoff))
    require(manifest.get("schema") == "ubts.private.physical_native_shadow_factory.v3" and
            manifest.get("owned_count") == 22 and len(manifest.get("files_sha256", {})) == 22 and
            manifest.get("deployment_closure_count") == 13 and
            manifest.get("runtime_authority") is False and
            manifest.get("after_last_association_final_scalar_fence") is True,
            "reviewed_physical_package_contract_required")
    closure_path = path / "DEPLOY_CLOSURE.json"
    require(sha_file(closure_path) == manifest["files_sha256"]["DEPLOY_CLOSURE.json"],
            "physical_deployment_closure_SHA_required")
    closure = strict_json(read_regular(closure_path))
    require(closure.get("schema") == "ubts.private.physical_native_shadow_deployment_closure.v1" and
            closure.get("count") == 13 and len(closure["files_sha256"]) == 13,
            "minimal_physical_deployment_closure_required")
    for name, expected in closure["files_sha256"].items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts and
                expected == manifest["files_sha256"].get(name) and sha_file(path / relative) == expected,
                "physical_deployment_source_changed:" + name)
    require(not any(name in sys.modules for name in ("physical_factory", "physical_ingress", "inputs")),
            "isolated_physical_module_loader_required")
    sys.path.insert(0, str(path))


def make_adapter(args):
    # Explicit execution only; no ROS init, node or publisher is constructed.
    from rclpy.clock import Clock, ClockType
    from common.native_ownership import LinuxRawClock
    from physical_ingress import PhysicalOriginalIngress
    from physical_factory import PhysicalShadowContract, PhysicalNativeShadowFactory, revision
    from stream_rows import StreamObserverRows
    clock = Clock(clock_type=ClockType.SYSTEM_TIME)
    require(clock.clock_type == ClockType.SYSTEM_TIME, "actual_RCL_SYSTEM_TIME_required")
    contract = PhysicalShadowContract("laser", "imu_link")
    receiver = PhysicalOriginalIngress(raw_clock=LinuxRawClock(), source_now_ns=lambda: clock.now().nanoseconds,
        source_clock_kind=lambda: "ROS_SYSTEM_TIME" if clock.clock_type == ClockType.SYSTEM_TIME else "invalid",
        scan_frame="laser", imu_frame="imu_link", profile_revision=revision(contract),
        scan_publisher_gid=bytes.fromhex(args.scan_gid), imu_publisher_gid=bytes.fromhex(args.imu_gid),
        observer_contract_sha256=args.observer_contract_sha256)
    receiver.reset()  # before Popen and before original callback delivery
    return StreamObserverRows(receiver, PhysicalNativeShadowFactory(receiver, contract),
                              observer_contract_sha256=args.observer_contract_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream-shadow", action="store_true")
    for name in ("package", "package-manifest-sha256", "observer-bin", "observer-binary-sha256",
                 "observer-contract-sha256", "stream-manifest", "stream-manifest-sha256", "scan-gid", "imu-gid"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    if not args.stream_shadow:
        print(json.dumps(dict(mode="plan", persistent=True, process_started=False,
            ROS_initialized=False, raw_rows_archived=False, queue_limit=QUEUE_ROWS,
            max_line_bytes=MAX_LINE, rolling_stderr_bytes=STDERR_BYTES,
            runtime_authority=False, motor_command_sent=False,
            autonomy_output_enabled=False, body_velocity_mps=None)))
        return 0
    require(sys.platform.startswith("linux") and os.geteuid() == 0 and
            os.environ.get("ROS_DISTRO") == "iron" and os.environ.get("ROS_DOMAIN_ID") == "0" and
            os.environ.get("UBTS_MAP_FIT_WORKERS", "1") == "1", "reviewed_actual_Iron_UID0_domain0_required")
    require(all(getattr(args, name.replace("-", "_")) for name in
        ("package", "package-manifest-sha256", "observer-bin", "observer-binary-sha256",
         "observer-contract-sha256", "stream-manifest", "stream-manifest-sha256", "scan-gid", "imu-gid")), "explicit_verified_stream_inputs_required")
    for digest in (args.package_manifest_sha256, args.observer_binary_sha256, args.observer_contract_sha256,
                   args.stream_manifest_sha256):
        require(re.fullmatch("[0-9a-f]{64}", digest) is not None, "explicit_SHA256_required")
    for gid in (args.scan_gid, args.imu_gid):
        require(re.fullmatch("[0-9a-f]{32}", gid) is not None and gid != "0"*32,
                "explicit_original_publisher_GID16_required")
    require(args.scan_gid != args.imu_gid, "distinct_original_sensor_GIDs_required")
    verify_stream_manifest(args.stream_manifest, args.stream_manifest_sha256, args)
    load_physical_package(args.package, args.package_manifest_sha256)
    binary = Path(args.observer_bin).resolve(strict=True)
    require(sha_file(binary) == args.observer_binary_sha256 and os.access(binary, os.X_OK),
            "root_verified_persistent_observer_binary_required")
    stopped = threading.Event()
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, lambda _signal, _frame: stopped.set())
    runner = NativeStreamRunner(lambda: make_adapter(args), lambda: subprocess.Popen(
        [str(binary), "--observe-until-stop", "--expected-scan-gid", args.scan_gid,
         "--expected-imu-gid", args.imu_gid], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False))
    failed = False
    try:
        runner.start()
        while not stopped.wait(.2):
            if runner.lifecycle.status().first_failure_reason is not None:
                failed = True
                break
    finally:
        final = runner.stop(3.)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print(json.dumps(runner.status(), allow_nan=False))
    return 0 if not failed and not final.owned and final.first_failure_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
