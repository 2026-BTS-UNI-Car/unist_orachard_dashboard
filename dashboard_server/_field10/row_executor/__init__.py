"""UBTS row executor (field weekend 2026-10-24): supervised, forward-only row following.

Pure-Python core shared by the live ROS node (later) and the offline shadow replay:
  config.py         ExecutorConfig (frozen, strict JSON, validate(), feasibility rules)
  guard.py          OutputGuard (last check before any duty leaves the executor), Backstop
  core.py           ScanAdapter, ImuYawRate, Runner (vendored SIM estimator + controller)
  bridge_client.py  BridgeClient for the existing ESP bridge socket (/run/ubts-rover/control.sock)
  vendor/           SIM estimator/controller/scan_corridor, byte-identical (vendor/SOURCE_MANIFEST.json)

Nothing in this package sends a motor command by itself: the Runner only returns Decisions; a
caller decides whether a BridgeClient is used at all (ExecutorConfig.motor_output_enabled, default
False). No module here imports ROS. See OPERATIONS.txt for the protocol and recovery rules.
"""
