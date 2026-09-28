"""Keep a ROS node in one executor across a stoppable callback loop."""
from contextlib import contextmanager


@contextmanager
def persistent_executor(node):
    # Import lazily so sensor codecs and offline tests do not require ROS.
    from rclpy.executors import SingleThreadedExecutor

    executor = SingleThreadedExecutor(context=node.context)
    try:
        executor.add_node(node)
        yield executor
    finally:
        executor.remove_node(node)
        executor.shutdown()
