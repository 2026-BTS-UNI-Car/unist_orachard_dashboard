#!/usr/bin/env python3
"""Backward-compatible entry point for the current orchard-fusion evaluator.

The evaluator was originally published under this v9 module name.  Keep the
import and CLI surface working for archived commands while the implementation
lives in the version-neutral module.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_orchard_fusion import evaluate_timeline, load_timeline, main

__all__ = ["evaluate_timeline", "load_timeline", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
