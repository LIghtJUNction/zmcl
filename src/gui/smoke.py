#!/usr/bin/env python3
"""Capture the real launcher in a private Xvfb display and measure its idle process.

Usage: python3 src/gui/smoke.py --binary target/release/zmcl
This does not interact with the user's desktop or modify an existing launcher root.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import time


def memory(pid: int) -> dict[str, int]:
    result = {}
    for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in {"Rss", "Pss", "Private_Clean", "Private_Dirty", "SwapPss"}:
            result[key + "_KiB"] = int(rest.split()[0])
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    result["cpu_ticks"] = int(fields[11]) + int(fields[12])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".test-evidence/gui"))
    parser.add_argument("--seconds", type=int, default=12)
    args = parser.parse_args()
    if args.seconds < 8 or args.seconds > 120:
        parser.error("--seconds must be between 8 and 120")
    for tool in ("Xvfb", "xdotool", "import"):
        if not shutil.which(tool):
            parser.error(f"Missing visual-test utility: {tool}")
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temp = output / "tmp"
    temp.mkdir(exist_ok=True)
    data = output / "data"
    env = dict(os.environ, TMPDIR=str(temp), LIBGL_ALWAYS_SOFTWARE="1", LP_NUM_THREADS="1")
    read_fd, write_fd = os.pipe()
    display_log = (output / "display.log").open("wb")
    display = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x900x24", "-nolisten", "tcp", "-noreset"],
        pass_fds=(write_fd,), env=env, stdout=subprocess.DEVNULL, stderr=display_log,
    )
    os.close(write_fd)
    gui = None
    try:
        if not select.select([read_fd], [], [], 10)[0]:
            raise RuntimeError("Private Xvfb display did not become ready")
        number = os.read(read_fd, 64).decode().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb did not return a display number")
        env["DISPLAY"] = ":" + number
        with (output / "launcher.log").open("wb") as log:
            gui = subprocess.Popen([str(binary), "--root", str(data), "gui", "--smoke-seconds", str(args.seconds)], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            window = subprocess.run(["xdotool", "search", "--sync", "--onlyvisible", "--pid", str(gui.pid)], env=env, capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()[0]
            time.sleep(1)
            ready_deadline = time.monotonic() + 4
            quiet_since = None
            while time.monotonic() < ready_deadline:
                if gui.poll() is not None:
                    raise RuntimeError("Launcher exited before its initial frame")
                children = Path(f"/proc/{gui.pid}/task/{gui.pid}/children").read_text().strip()
                if children:
                    quiet_since = None
                elif quiet_since is None:
                    quiet_since = time.monotonic()
                elif time.monotonic() - quiet_since > 0.6:
                    break
                time.sleep(0.1)
            if children:
                raise RuntimeError("Launcher CLI children did not reach an idle state")
            first = memory(gui.pid)
            started = time.monotonic()
            subprocess.run(["import", "-display", env["DISPLAY"], "-window", window, str(output / "library.png")], env=env, check=True, timeout=10)
            time.sleep(2)
            last = memory(gui.pid)
            elapsed = time.monotonic() - started
            result = {
                "backend": "Xvfb / llvmpipe, software rendering; not a hardware GPU comparison",
                "binary_bytes": binary.stat().st_size,
                "first": first,
                "idle": last,
                "idle_interval_seconds": elapsed,
                "idle_cpu_percent_one_core": (last["cpu_ticks"] - first["cpu_ticks"]) / os.sysconf("SC_CLK_TCK") / elapsed * 100,
                "scope": "launcher process only; excludes Xvfb, installed game data and Java",
            }
            result["exit_code"] = gui.wait(timeout=args.seconds + 5)
            (output / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
            if result["exit_code"] != 0:
                raise RuntimeError("Launcher returned a nonzero exit status")
    finally:
        os.close(read_fd)
        if gui is not None and gui.poll() is None:
            gui.terminate()
            try:
                gui.wait(timeout=3)
            except subprocess.TimeoutExpired:
                gui.kill()
                gui.wait()
        display.terminate()
        try:
            display.wait(timeout=3)
        except subprocess.TimeoutExpired:
            display.kill()
            display.wait()
        display_log.close()


if __name__ == "__main__":
    main()
