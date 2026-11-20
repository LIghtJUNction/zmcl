#!/usr/bin/env python3
"""Linux launcher measurement with isolated data and a private Xvfb.

Reports the whole launcher process family/session, plus previously observed
children. RSS sums may double-count shared pages; PSS is the comparison metric.
The X server, benchmark controller, downloaded games and Java are excluded.
No machine-wide caches are dropped. Run applications sequentially on an otherwise
stable machine and preserve screenshots to describe the actual visible workload.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import tempfile
import time

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def family(leader, known):
    # Retaining a PID from the previous sample must not stop descendant traversal.
    pending = [leader, *known]
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            pid = int(entry.name)
            try:
                if os.getsid(pid) == leader:
                    pending.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
    visited = set()
    found = set()
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        proc = Path(f"/proc/{pid}")
        if not proc.exists():
            continue
        found.add(pid)
        try:
            children = (proc / "task" / str(pid) / "children").read_text().split()
            pending.extend(int(x) for x in children)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return found

def sample(pids):
    rss = pss = swap = swap_pss = 0
    ticks = {}
    names = []
    memory = {}
    unread = []
    for pid in sorted(pids):
        try:
            proc = Path(f"/proc/{pid}")
            before = (proc / "stat").read_text()
            before_start = before[before.rfind(")") + 2:].split()[19]
            # comm is a process label: a Python shim can still execute mise.
            # Bracket the memory read so an exec cannot mislabel its footprint.
            executable_before = os.readlink(proc / "exe")
            image_before = (proc / "exe").stat()
            content = (proc / "smaps_rollup").read_text()
            values = {line.split(":")[0]: int(line.split()[1])
                      for line in content.splitlines()
                      if line.startswith(("Rss:", "Pss:", "Swap:", "SwapPss:"))}
            stat = Path(f"/proc/{pid}/stat").read_text()
            parts = stat[stat.rfind(")") + 2:].split()
            if parts[0] == "Z":
                continue
            name = Path(f"/proc/{pid}/comm").read_text().strip()
            executable_after = os.readlink(proc / "exe")
            image_after = (proc / "exe").stat()
            image_id = (image_before.st_dev, image_before.st_ino)
            if (before_start != parts[19] or executable_before != executable_after or
                    image_id != (image_after.st_dev, image_after.st_ino)):
                unread.append(pid)
                continue
            # Store only the executable filename, never a private installation path.
            deleted = executable_after.endswith(" (deleted)")
            executable = Path(executable_after.removesuffix(" (deleted)")).name
            # starttime protects the CPU identity check against PID reuse.
            identity = str(pid) + ":" + parts[19]
            cpu_ticks = int(parts[11]) + int(parts[12])
            process = {"pid": pid, "name": name, "executable": executable,
                       "executable_identity": f"{image_id[0]}:{image_id[1]}",
                       "executable_deleted": deleted,
                       "rss_kib": values["Rss"], "pss_kib": values["Pss"],
                       "swap_kib": values["Swap"], "swap_pss_kib": values["SwapPss"]}
            # Commit a complete row only; a failed field must not leave CPU
            # identity or totals without its matching memory values.
            ticks[identity] = cpu_ticks
            names.append(name)
            memory[identity] = process
            rss += process["rss_kib"]
            pss += process["pss_kib"]
            swap += process["swap_kib"]
            swap_pss += process["swap_pss_kib"]
        except (OSError, ValueError, KeyError, IndexError):
            unread.append(pid)
    return {"rss_kib": rss, "pss_kib": pss, "swap_kib": swap, "swap_pss_kib": swap_pss, "processes": len(ticks),
            "cpu_ticks": ticks, "process_names": sorted(names), "process_memory": memory,
            "unread_processes": unread}

def main_process_memory(snapshot, pid):
    rows = [row for row in snapshot["process_memory"].values() if row["pid"] == pid]
    return rows[0] if len(rows) == 1 else None


def complete_main_median(samples, metric):
    # Never silently discard a failed main-process read to report a lower median.
    if not samples or any(s.get("main_process") is None for s in samples):
        return None
    return statistics.median(s["main_process"][metric] for s in samples)


def display_change(samples, before):
    # Keep external display cost visible, including negative deltas caused by
    # shared-page accounting. Replacement or unread processes invalidate it.
    snapshots = [s["display_server"] for s in samples]
    if before["unread_processes"] or before["processes"] != 1 or not snapshots:
        return None
    if any(s["unread_processes"] or s["processes"] != 1 or
           s["cpu_ticks"].keys() != before["cpu_ticks"].keys() for s in snapshots):
        return None
    return {metric: statistics.median(s[metric] for s in snapshots) - before[metric]
            for metric in ["rss_kib", "pss_kib", "swap_kib", "swap_pss_kib"]}


def summarize_cpu(samples, clock_ticks):
    intervals = []
    observed_ticks = 0
    stable = not any(x.get("unread_processes") for x in samples)
    for previous, current in zip(samples, samples[1:]):
        old = previous["cpu_ticks"]
        new = current["cpu_ticks"]
        shared = old.keys() & new.keys()
        nondecreasing = all(new[pid] >= old[pid] for pid in shared)
        complete = old.keys() == new.keys() and nondecreasing
        complete = complete and not previous.get("unread_processes") and not current.get("unread_processes")
        common_ticks = sum(max(0, new[pid] - old[pid]) for pid in shared)
        observed_ticks += common_ticks
        intervals.append({
            "elapsed_seconds": current["elapsed_seconds"] - previous["elapsed_seconds"],
            "process_set_stable_at_endpoints": bool(complete),
            "entered_identities": sorted(new.keys() - old.keys()),
            "left_identities": sorted(old.keys() - new.keys()),
            "observed_common_cpu_ticks": common_ticks,
        })
        stable = stable and complete
    elapsed = samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"]
    observed = observed_ticks / clock_ticks
    return {
        "cpu_process_set_stable_at_sample_points": bool(stable),
        "cpu_identity": "PID:starttime_ticks",
        "idle_cpu_seconds": observed if stable else None,
        "one_core_cpu_percent": 100 * observed / elapsed if stable and elapsed else None,
        "observed_common_cpu_seconds": observed,
        "cpu_intervals": intervals,
    }

def terminate_owned(process, known):
    # Every process ID is learned from our launched session; never use pkill.
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    for pid in known:
        try:
            if os.getsid(pid) == process.pid:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

def start_private_xvfb(log, authority):
    # Do not probe or alter existing displays, locks, sockets or authorization.
    for display_number in range(100, 200):
        lock = Path(f"/tmp/.X{display_number}-lock")
        socket = Path(f"/tmp/.X11-unix/X{display_number}")
        if lock.exists() or lock.is_symlink() or socket.exists() or socket.is_symlink():
            continue
        display = f":{display_number}"
        process = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=log,
        )
        ready = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            try:
                owner = int(lock.read_text().strip())
            except (FileNotFoundError, ValueError):
                time.sleep(0.05)
                continue
            if owner != process.pid:
                break
            if socket.exists():
                env = dict(os.environ, DISPLAY=display, XAUTHORITY=str(authority))
                try:
                    probe = subprocess.run(
                        ["xdotool", "getdisplaygeometry"], env=env,
                        capture_output=True, text=True, timeout=2,
                    )
                    ready = probe.returncode == 0
                except subprocess.TimeoutExpired:
                    pass
                if ready:
                    return process, display
            time.sleep(0.05)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            process.wait()
    raise RuntimeError("No available owned Xvfb in display range 100-199; existing display files were left untouched")

def trial(options, number):
    folder = options.output / f"{options.app}-{number}"
    folder.mkdir(parents=True, exist_ok=False)
    profile = folder / "profile"
    profile.mkdir()
    log = (folder / "launcher.log").open("w")
    xlog = (folder / "xvfb.log").open("w")
    xvfb = None
    process = None
    temporary = None
    known = set()
    try:
        authority = profile / "Xauthority"
        authority.write_bytes(b"")
        xvfb, display = start_private_xvfb(xlog, authority)
        env = os.environ.copy()
        env.update({"DISPLAY": display, "XAUTHORITY": str(authority),
                    "LIBGL_ALWAYS_SOFTWARE": "1", "XDG_SESSION_TYPE": "x11",
                    "SDL_VIDEO_DRIVER": "x11"})
        env.pop("WAYLAND_DISPLAY", None)
        # Chromium puts a Unix socket under TMPDIR; deeply nested evidence
        # directories can exceed its socket pathname limit.
        temp_root = Path(__file__).resolve().parent.parent / ".test-evidence" / "t"
        temp_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="b", dir=temp_root)
        env["TMPDIR"] = temporary.name
        display_renderer = None
        if shutil.which("glxinfo"):
            info = subprocess.run(["glxinfo", "-B"], env=env, capture_output=True, text=True, timeout=20)
            for line in info.stdout.splitlines():
                if line.strip().startswith("OpenGL renderer string:"):
                    display_renderer = line.split(":", 1)[1].strip()
        for key, name in [("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                          ("XDG_CACHE_HOME", "cache")]:
            path = profile / name
            path.mkdir()
            env[key] = str(path)
        if options.app == "zmcl":
            env["ZMCL_GUI_METRICS"] = "1"
            command = [str(options.binary), "--root", str(profile / "zmcl"), "gui"]
            title = "^zmcl"
        else:
            appdata = profile / "xmcl-app"
            game = profile / "xmcl-game"
            game.mkdir()
            (appdata / "xmcl").mkdir(parents=True)
            (appdata / "xmcl/root").write_text(str(game))
            env.update({"XMCL_E2E": "1", "XMCL_E2E_APP_DATA": str(appdata),
                        "XMCL_E2E_GAME_DATA": str(game)})
            command = [str(options.binary), "--ozone-platform=x11"]
            title = "XMCL|X Minecraft|xmcl"
        display_before = sample({xvfb.pid})
        started = time.monotonic()
        process = subprocess.Popen(command, cwd=options.binary.parent, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        window = None
        deadline = started + 90
        while time.monotonic() < deadline:
            known = family(process.pid, known)
            if process.poll() is not None:
                raise RuntimeError(f"Launcher exited during startup: {process.returncode}")
            result = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", title],
                                    env=env, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                window = result.stdout.splitlines()[0]
                break
            time.sleep(0.25)
        if window is None:
            raise RuntimeError("No visible launcher window within 90 seconds")
        visible_ms = round((time.monotonic() - started) * 1000, 3)
        subprocess.run(["xdotool", "windowsize", window, str(options.width), str(options.height)], env=env, check=True)
        warmup_end = time.monotonic() + options.warmup
        while time.monotonic() < warmup_end:
            known = family(process.pid, known)
            time.sleep(0.25)
        subprocess.run(["import", "-window", window, "-quality", "84",
                        str(folder / "window.jpg")], env=env, check=True, timeout=20)
        samples = []
        sampled_at = time.monotonic()
        for index in range(options.samples):
            if process.poll() is not None:
                raise RuntimeError("Launcher exited during sampling")
            known = family(process.pid, known)
            data = sample(known)
            data["main_process"] = main_process_memory(data, process.pid)
            data["display_server"] = sample({xvfb.pid})
            data["elapsed_seconds"] = time.monotonic() - sampled_at
            samples.append(data)
            if index + 1 < options.samples:
                time.sleep(options.interval)
        elapsed = samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"]
        memory_samples = [x for x in samples if not x["unread_processes"] and x["processes"]]
        if not memory_samples:
            raise RuntimeError("No complete process-tree memory sample")
        cpu = summarize_cpu(samples, os.sysconf("SC_CLK_TCK"))
        result = {
            "trial": number, "window_visible_ms": visible_ms,
            "window_size": {"width": options.width, "height": options.height},
            "main_rss_median_kib": complete_main_median(samples, "rss_kib"),
            "main_pss_median_kib": complete_main_median(samples, "pss_kib"),
            "display_server_before_app": display_before,
            "display_server_change_kib": display_change(samples, display_before),
            "rss_median_kib": statistics.median(x["rss_kib"] for x in memory_samples),
            "pss_median_kib": statistics.median(x["pss_kib"] for x in memory_samples),
            "pss_min_kib": min(x["pss_kib"] for x in memory_samples),
            "pss_max_kib": max(x["pss_kib"] for x in memory_samples),
            "swap_median_kib": statistics.median(x["swap_kib"] for x in memory_samples),
            "swap_pss_median_kib": statistics.median(x["swap_pss_kib"] for x in memory_samples),
            **cpu, "sample_elapsed_seconds": elapsed,
            "memory_complete_samples": len(memory_samples),
            "memory_all_samples_complete": len(memory_samples) == len(samples),
            "processes": samples[-1]["processes"],
            "processes_min": min(x["processes"] for x in samples),
            "processes_max": max(x["processes"] for x in samples),
            "display_renderer": display_renderer,
            "display_number": int(display[1:]),
            "samples": samples,
        }
        # Keep raw logs private; only copy this small public first-frame event.
        log.flush()
        if options.app == "zmcl":
            for line in (folder / "launcher.log").read_text(errors="replace").splitlines():
                if line.startswith("{"):
                    try:
                        event = json.loads(line)
                        if event.get("event") == "gui_first_frame":
                            result["first_frame"] = event
                    except json.JSONDecodeError:
                        pass
        (folder / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        if process is not None:
            terminate_owned(process, known)
        if xvfb is not None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait()
        log.close()
        xlog.close()
        if temporary is not None:
            temporary.cleanup()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=["zmcl", "xmcl"], required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", default="")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--warmup", type=float, default=20)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=760)
    options = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("This script measures Linux /proc only")
    if not 1 <= options.trials <= 10 or not 2 <= options.samples <= 120 or options.interval <= 0 or options.warmup < 0:
        parser.error("Invalid measurement bounds")
    if not 640 <= options.width <= 1280 or not 480 <= options.height <= 800:
        parser.error("Window must fit the fixed 1280x800 display")
    options.binary = options.binary.resolve(strict=True)
    options.output = options.output.absolute()
    options.output.mkdir(parents=True, exist_ok=True)
    for program in ["Xvfb", "xdotool", "import"]:
        if shutil.which(program) is None:
            parser.error(f"Missing system tool: {program}")
    report = {
        "schema": 5, "app": options.app, "version": options.version,
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "revision": options.revision, "binary_sha256": digest(options.binary),
        "benchmark_script_sha256": digest(Path(__file__)),
        "binary_bytes": options.binary.stat().st_size,
        "system": {"os": platform.system(), "arch": platform.machine(),
                   "kernel": platform.release()},
        "scenario": "Fresh isolated data, no account, no instance, visible default home/library",
        "display": f"Owned Xvfb :100-:199 1280x800x24; launcher window {options.width}x{options.height}; LIBGL_ALWAYS_SOFTWARE=1",
        "scope": "Main launcher and full launcher processes/session are both reported; Xvfb is measured separately before and during the app; controller, Java and Minecraft are excluded",
        "conditions": {"warmup_seconds": options.warmup, "samples_per_trial": options.samples,
                       "interval_seconds": options.interval, "os_caches_flushed": False,
                       "cpu_clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                       "software_driver_environment": {key: os.environ.get(key) for key in
                           ["LP_NUM_THREADS", "GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE", "MESA_GL_VERSION_OVERRIDE"]},
                       "background_workload": "Shared development machine; no system-wide isolation",
                       "display_allocation": "Only 100-199; skip existing lock/socket; require lock PID to match owned Xvfb",
                       "xauthority_source": "Fresh empty per-trial file; no existing authority file read",
                       "session_environment": {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "unset",
                                               "SDL_VIDEO_DRIVER": "x11"}},
        "limitations": [
            "Default home pages differ: XMCL displays news and services.",
            "Applications share Xvfb/environment settings but may use different internal rendering stacks.",
            "CPU totals are reported only when sampled PID/starttime sets stay stable and reads succeed.",
            "Processes born and exited wholly between sample points are not observable with /proc snapshots.",
            "Software renderer environment is not a hardware GPU benchmark.",
            "Visible window time is not first interactive frame time.",
            "RSS sums double-count shared pages; use aggregate PSS for memory comparison.",
            "System library/runtime installation space is not in the ELF byte count.",
        ],
        "trials": [],
    }
    try:
        os_info = platform.freedesktop_os_release()
        report["system"]["distribution"] = os_info.get("PRETTY_NAME", os_info.get("NAME", ""))
    except OSError:
        pass
    report["system"]["logical_cpus"] = os.cpu_count()
    report["system"]["memory_at_start_kib"] = {
        line.split(":")[0]: int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"))
    }
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name") or line.startswith("Hardware"):
            report["system"]["cpu_model"] = line.split(":", 1)[1].strip()
            break
    for index in range(1, options.trials + 1):
        if digest(options.binary) != report["binary_sha256"]:
            raise RuntimeError("Measured binary changed before a trial")
        result = trial(options, index)
        if digest(options.binary) != report["binary_sha256"]:
            raise RuntimeError("Measured binary changed during a trial")
        report["trials"].append(result)
        print(json.dumps({key: value for key, value in result.items() if key != "samples"}), flush=True)
        (options.output / f"{options.app}-measurements.json").write_text(json.dumps(report, indent=2))
    stable_trials = [x for x in report["trials"] if x["cpu_process_set_stable_at_sample_points"]]
    report["summary"] = {
        "median_trial_pss_kib": statistics.median(x["pss_median_kib"] for x in report["trials"]),
        "median_trial_rss_kib": statistics.median(x["rss_median_kib"] for x in report["trials"]),
        "median_trial_swap_kib": statistics.median(x["swap_median_kib"] for x in report["trials"]),
        "median_trial_swap_pss_kib": statistics.median(x["swap_pss_median_kib"] for x in report["trials"]),
        "median_window_visible_ms": statistics.median(x["window_visible_ms"] for x in report["trials"]),
        "cpu_stable_trials": len(stable_trials),
        "median_idle_cpu_seconds": statistics.median(x["idle_cpu_seconds"] for x in stable_trials) if stable_trials else None,
        "median_one_core_cpu_percent": statistics.median(x["one_core_cpu_percent"] for x in stable_trials) if stable_trials else None,
    }
    (options.output / f"{options.app}-measurements.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"]), flush=True)

if __name__ == "__main__":
    main()
