#!/usr/bin/env python3
"""Deprecated CMake compatibility entry point for :mod:`probe_runner`."""
from __future__ import annotations

import sys

from probe_runner import main


if __name__ == "__main__":
    sys.argv.insert(1, "run")
    sys.argv.extend(["--adapter", "cmake"])
    main()
