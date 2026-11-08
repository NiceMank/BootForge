#!/usr/bin/env python3
"""Windows entry point for BootForge PyInstaller bundle."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if "--engine-job" in sys.argv:
    from engine.bootforge_engine import main as engine_main

    args = list(sys.argv[1:])
    idx = args.index("--engine-job")
    job_path = args[idx + 1] if idx + 1 < len(args) else ""
    event_log = ""
    if "--event-log" in args:
        e_idx = args.index("--event-log")
        event_log = args[e_idx + 1] if e_idx + 1 < len(args) else ""

    sub_args = ["run-job", "--job", job_path]
    if event_log:
        sub_args.extend(["--event-log", event_log])
    raise SystemExit(engine_main(sub_args))

from bootforge_win.window import run_windows_app

if __name__ == "__main__":
    raise SystemExit(run_windows_app(sys.argv))
