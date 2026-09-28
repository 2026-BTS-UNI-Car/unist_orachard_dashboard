"""Persistent ROS execution retains ordering, cleanup, and wall-clock watchdogs."""
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.ros_executor import persistent_executor
from scripts.rover_control_core import RoverControl


class PersistentExecutorTest(unittest.TestCase):
    def test_exception_removes_node_and_shuts_down_without_destroying_it(self):
        events = []
        class Executor:
            def __init__(self, context): events.append(('create', context))
            def add_node(self, node): events.append(('add', node))
            def remove_node(self, node): events.append(('remove', node))
            def shutdown(self): events.append(('shutdown', None))
        node = SimpleNamespace(context=object())
        with patch.dict(sys.modules, {'rclpy.executors': SimpleNamespace(SingleThreadedExecutor=Executor)}):
            with self.assertRaisesRegex(RuntimeError, 'callback failure'):
                with persistent_executor(node):
                    raise RuntimeError('callback failure')
        self.assertEqual([event[0] for event in events], ['create', 'add', 'remove', 'shutdown'])

    def test_single_thread_callbacks_and_paused_simulation_watchdog(self):
        try:
            import rclpy
            from rclpy.context import Context
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.node import Node
        except ImportError:
            self.skipTest('ROS executor behavior runs in the simulation image')

        class Motor:
            ready=True;firmware='UNIT';error=None;stopped=False;outputs={}
            def enable(self): pass
            def drive(self, left, right): self.requested=(left,right)
            def hard_stop(self): self.requested=(0,0)
            def release(self): self.requested=(0,0)

        registrations = []
        class ObservedExecutor(SingleThreadedExecutor):
            def add_node(self, node):
                added = super().add_node(node)
                if added: registrations.append('add')
                return added
            def remove_node(self, node):
                was_present = node in self.get_nodes()
                result = super().remove_node(node)
                if was_present: registrations.append('remove')
                return result

        wall = [0.0]
        motor = Motor();control = RoverControl(motor, clock=lambda:wall[0])
        grant = control.dispatch({'op':'arm','owner':'unit'})
        control.dispatch({'op':'drive','owner':'unit','lease':grant['lease'],
                          'ticket':grant['ticket'],'seq':1,'left':20,'right':20,'deadman':True})
        context = Context();rclpy.init(context=context, domain_id=174)
        node = Node('executor_watchdog_unit', context=context)
        callbacks = []
        def callback():
            callbacks.append((len(callbacks),threading.get_ident()))
            # Only wall time advances: a paused /clock must not retain motion.
            wall[0] += .25
        node.create_timer(.002, callback)
        try:
            with patch('rclpy.executors.SingleThreadedExecutor', ObservedExecutor):
                with persistent_executor(node) as executor:
                    deadline = time.monotonic()+2
                    while len(callbacks)<5 and time.monotonic()<deadline:
                        executor.spin_once(timeout_sec=.005)
                        control.tick()  # same callback-then-watchdog ordering as Adapter
                    self.assertEqual(registrations, ['add'])
            self.assertEqual(registrations, ['add','remove'])
            self.assertEqual([item[0] for item in callbacks], list(range(5)))
            self.assertEqual({item[1] for item in callbacks}, {threading.get_ident()})
            self.assertFalse(control.lease)
            self.assertEqual(control.reason, 'input_expired')
            self.assertEqual(motor.requested, (0,0))
            self.assertNotIn(node, executor.get_nodes())
        finally:
            node.destroy_node();rclpy.shutdown(context=context)


if __name__ == '__main__':
    unittest.main()
