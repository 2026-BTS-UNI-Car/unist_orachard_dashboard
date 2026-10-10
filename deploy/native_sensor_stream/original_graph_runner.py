"""Asynchronous tracked graph activation; original stream runner is unchanged."""
import time
from dashboard_server.native_owner_lifecycle import ResourceHooks
from stream_runner import NativeStreamRunner, _StreamResource, bounded_timeout, require
from original_topic_graph import GraphBindingDenied


class _GraphResource(_StreamResource):
    def __init__(self, context, adapter_factory, process_factory, identity,
                 thread_factory, map_session, resolver):
        super().__init__(context, adapter_factory, process_factory, identity,
                         thread_factory, map_session)
        self.resolver = resolver
        self.activation_thread = None
        self.graph_binding = None
        self.graph_cleanup_verified = False
        self.graph_pending = True

    def activate_with_graph(self):
        try:
            binding = self.resolver.resolve(self.context)
            require(binding.owner_session == self.context.session and
                    binding.owner_generation == self.context.generation,
                    'original_graph_generation_required')
            self.graph_cleanup_verified = True
            if self.context.cancelled:
                return
            self.graph_binding = binding
            original_adapter, original_process = self.adapter_factory, self.process_factory
            # The same immutable object supplies receiver, GPS dispatch and CPP.
            self.adapter_factory = lambda: original_adapter(binding)
            self.process_factory = lambda: original_process(binding)
            super().activate()
        except BaseException as error:
            self.graph_cleanup_verified = (type(error) is GraphBindingDenied and
                                           error.cleanup_verified is True)
            if not (self.context.cancelled and self.graph_cleanup_verified):
                self.fail(error)
            self.unknown = not self.graph_cleanup_verified
        finally:
            with self.lock:
                self.graph_pending = False
            if self.context.cancelled:
                self.request_stop()

    def join(self, timeout):
        if not self.explicit_stop:
            return False
        deadline = time.monotonic() + bounded_timeout(timeout)
        thread = self.activation_thread
        if thread is not None:
            try:
                thread.join(max(0., deadline-time.monotonic()))
            except BaseException as error:
                self.fail(error)
                return False
            if thread.is_alive():
                return False  # exact generation remains owned; no new Start
        if self.graph_pending or not self.graph_cleanup_verified:
            return False
        return super().join(max(0., deadline-time.monotonic()))

    def snapshot(self):
        answer = super().snapshot()
        with self.lock:
            answer['original_graph_pending'] = self.graph_pending
            answer['original_graph_cleanup_verified'] = self.graph_cleanup_verified
            binding = self.graph_binding
        answer['original_graph_bound_generation'] = None if binding is None else binding.owner_generation
        answer['original_graph_bound_topics'] = [] if binding is None else [p.topic for p in binding.publishers]
        return answer


class OriginalGraphStreamRunner(NativeStreamRunner):
    """Enabled by the service's explicit private flag, never by RPC status.

    start() returns after its activation thread is tracked. Native lifecycle
    cancellation exists before any graph operation, and Stop shares its original
    deadline across activation/process/reader/map joins. SDK stalls retain owner.
    """
    def __init__(self, adapter_factory, process_factory, resolver, **kwargs):
        require(callable(getattr(resolver, 'resolve', None)), 'original_graph_resolver_required')
        self.resolver = resolver
        super().__init__(adapter_factory, process_factory, **kwargs)

    def _factory(self, context):
        resource = _GraphResource(context, self.adapter_factory, self.process_factory,
                                 self.identity, self.thread_factory, self.map_session, self.resolver)
        with self.lock:
            self.resource = resource
            resource.explicit_stop = self.pending_explicit_stop_generation == context.generation
        try:
            thread = self.thread_factory(target=resource.activate_with_graph,
                                        name='native-original-graph-activation', daemon=False)
            resource.activation_thread = thread
            resource.threads.append(thread)  # exact attempted handle before start
            thread.start()
        except BaseException as error:
            resource.unknown = True
            resource.fail(error)
        return ResourceHooks(resource.request_stop, resource.join, resource.close)
