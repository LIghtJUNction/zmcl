#!/usr/bin/env python3
"""Measure zmcl idle and repaint memory on an owned X11 display.

Uses bench_linux's process-family, /proc and private-Xvfb helpers. Expose requests
exercise the real window at its normal size; they are not a rendered-FPS count.
Run sequentially without compilation or other benchmarks on the same machine.
The JSON retains raw swap, process identities and the separate display cost.
"""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import threading
import time

SPEC = importlib.util.spec_from_file_location("bench_linux", Path(__file__).with_name("bench_linux.py"))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)
METRICS = ("rss_kib", "pss_kib", "swap_kib", "swap_pss_kib")
LIMITS = [
    "Sampled maxima are observed lower bounds, not a guarantee for all application states.",
    "Processes born and exited wholly between /proc reads can be missed; sequential reads are not atomic.",
    "RSS sums can count shared pages more than once; PSS and SwapPss remain separate raw measurements.",
    "VmHWM is the kernel main-process lifetime RSS counter, not peak PSS or a process-tree counter; its accounting can differ from smaps_rollup.",
    "Xvfb is measured before and during the app and reported separately; it remains real system cost.",
    "The Python observer and X11 driver are test-process work, excluded from launcher and display totals.",
    "CPU totals require stable sampled identities; common-process CPU during churn is only a lower bound.",
    "This is a shared development host with no system-wide cache flush or CPU isolation.",
]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def vm_status(pid):
    try:
        return {line.split(":")[0]: int(line.split()[1])
                for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                if line.startswith(("VmHWM:", "VmRSS:", "VmSize:", "VmSwap:"))}
    except (OSError, ValueError):
        return None


def process_starttime(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        return None


def memory_summary(rows, complete):
    valid = [row for row, ok in zip(rows, complete) if ok]
    all_complete = bool(rows) and len(valid) == len(rows)
    result = {"all_samples_complete": all_complete, "complete_samples": len(valid)}
    for metric in (*METRICS, "pss_plus_swap_pss_kib"):
        def value(row):
            return row[metric] if metric in METRICS else row["pss_kib"] + row["swap_pss_kib"]
        stem = metric.removesuffix("_kib")
        # A failed read must not quietly lower a median. Partial observed sums
        # still provide a lower bound and stay in the raw samples for review.
        result[stem + "_median_kib"] = statistics.median(value(row) for row in valid) if all_complete else None
        result[stem + "_sampled_peak_kib"] = max((value(row) for row in rows if row is not None), default=None)
    return result


def cpu_summary(rows):
    if len(rows) < 2:
        return {"cpu_process_set_stable_at_sample_points": False, "cpu_seconds": None,
                "one_core_cpu_percent": None, "observed_common_cpu_seconds": None,
                "cpu_intervals": []}
    result = bench.summarize_cpu(rows, os.sysconf("SC_CLK_TCK"))
    result["cpu_seconds"] = result.pop("idle_cpu_seconds")
    return result


def summarize(samples, display_before):
    main = [row["main_process"] for row in samples]
    display = [row["display_server"] for row in samples]
    tree_complete = [row["main_process"] is not None and row["processes"] > 0
                     and not row["unread_processes"] for row in samples]
    display_complete = [row["processes"] == 1 and not row["unread_processes"]
                        and row["cpu_ticks"].keys() == display_before["cpu_ticks"].keys()
                        for row in display]
    result = {
        "samples": len(samples),
        "main": memory_summary(main, [row is not None for row in main]),
        "tree": memory_summary(samples, tree_complete),
        "display_server": memory_summary(display, display_complete),
        "display_server_median_delta_kib": bench.display_change(samples, display_before),
        "tree_cpu": cpu_summary(samples),
        "processes_min": min((row["processes"] for row in samples), default=None),
        "processes_max": max((row["processes"] for row in samples), default=None),
        "main_vm_hwm_max_kib": max((row["main_status"]["VmHWM"] for row in samples
                                     if row["main_status"] and "VmHWM" in row["main_status"]), default=None),
        "main_status_reads_complete": bool(samples) and all(row["main_status"] for row in samples),
    }
    for name, rows in (("main", main), ("display_server", display)):
        cpu_rows = []
        for raw, row in zip(samples, rows):
            ticks = (row["cpu_ticks"] if name == "display_server" else {
                identity: raw["cpu_ticks"][identity] for identity, process in raw["process_memory"].items()
                if row is not None and process["pid"] == row["pid"]})
            cpu_rows.append({"cpu_ticks": ticks, "elapsed_seconds": raw["elapsed_seconds"],
                             "unread_processes": row.get("unread_processes", []) if row else ["unread"]})
        result[name + "_cpu"] = cpu_summary(cpu_rows)
    if samples:
        result["sample_span_seconds"] = samples[-1]["elapsed_seconds"] - samples[0]["elapsed_seconds"]
        result["read_duration_ms_median"] = statistics.median(row["read_duration_seconds"] * 1000 for row in samples)
    gaps = [(b["elapsed_seconds"] - a["elapsed_seconds"]) * 1000 for a, b in zip(samples, samples[1:])]
    result["observed_interval_ms"] = {"median": statistics.median(gaps) if gaps else None,
                                      "max": max(gaps, default=None)}
    result["all_memory_samples_complete"] = all(result[scope]["all_samples_complete"]
                                                  for scope in ("main", "tree", "display_server"))
    return result


class ProcessSampler:
    """Optional test-process thread; never installs a launcher-side monitor.

    The launched GUI must own a new session. family() includes descendants,
    session members and previously seen live descendants. Only this observer
    thread calls take() while start()/stop() surround an action.
    """

    def __init__(self, pid, display_pid, display_before, interval=.005):
        if interval <= 0:
            raise ValueError("Sampling sleep must be positive")
        if os.getsid(pid) != pid:
            raise ValueError("The measured GUI must own a new session")
        self.pid, self.display_pid = pid, display_pid
        self.display_before, self.interval = display_before, interval
        self.known = set()
        self._known_starttimes = {}
        self.samples = []
        self.events = []
        self.started = time.monotonic()
        self.sampling_cpu_seconds = 0.0
        self.error = None
        self._phase = "startup"
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def phase(self, name):
        with self._lock:
            self._phase = name
            self.events.append({"phase": name, "elapsed_seconds": time.monotonic() - self.started})

    def take(self, phase=None):
        cpu_started = time.thread_time()
        started = time.monotonic()
        if phase and phase != self._phase:
            self.phase(phase)
        with self._lock:
            label = phase or self._phase
        # Reuse family traversal, but do not retain an unrelated recycled PID.
        retained = {pid for pid in self.known if pid in self._known_starttimes
                    and process_starttime(pid) == self._known_starttimes[pid]}
        self.known = bench.family(self.pid, retained)
        row = bench.sample(self.known)
        self._known_starttimes = {process["pid"]: identity.split(":", 1)[1]
                                 for identity, process in row["process_memory"].items()}
        row.update(main_process=bench.main_process_memory(row, self.pid),
                   main_status=vm_status(self.pid), display_server=bench.sample({self.display_pid}),
                   phase=label, elapsed_seconds=time.monotonic() - self.started,
                   read_duration_seconds=time.monotonic() - started)
        self.samples.append(row)
        self.sampling_cpu_seconds += time.thread_time() - cpu_started
        return row

    def start(self, phase="startup"):
        if self._thread is not None:
            raise RuntimeError("Sampler already started")
        self.phase(phase)
        self._stop.clear()

        def worker():
            try:
                while not self._stop.is_set():
                    self.take()
                    self._stop.wait(self.interval)
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"

        self._thread = threading.Thread(target=worker, name="gui-memory-observer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise RuntimeError("Memory observer did not stop")
            self._thread = None

    def report(self):
        if self._thread is not None:
            raise RuntimeError("Stop the memory observer before reporting")
        observed = {}
        for row in self.samples:
            for identity, process in row["process_memory"].items():
                item = observed.setdefault(identity, {
                    "identity": identity, "pid": process["pid"], "name": process["name"],
                    "role": "main" if process["pid"] == self.pid else "observed descendant/session member",
                    "first_seen_seconds": row["elapsed_seconds"], "samples_seen": 0,
                    "phases": [], **{metric: 0 for metric in METRICS}})
                item["last_seen_seconds"] = row["elapsed_seconds"]
                item["samples_seen"] += 1
                if row["phase"] not in item["phases"]:
                    item["phases"].append(row["phase"])
                for metric in METRICS:
                    item[metric] = max(item[metric], process[metric])
        return {
            "sampling_error": self.error, "requested_sleep_seconds": self.interval,
            "sampling_cpu_seconds": self.sampling_cpu_seconds,
            "display_server_before_app": self.display_before,
            "summary": summarize(self.samples, self.display_before),
            "phases": {name: summarize([row for row in self.samples if row["phase"] == name], self.display_before)
                       for name in dict.fromkeys(row["phase"] for row in self.samples)},
            "phase_events": self.events,
            "observed_processes": list(observed.values()),
            "observed_process_metrics": "Per-identity observed maxima in KiB; these maxima need not be simultaneous",
            "samples": self.samples, "limitations": LIMITS,
        }


def expose(display, window, authority, seconds, hz):
    lib = ctypes.CDLL("libX11.so.6")
    lib.XOpenDisplay.argtypes, lib.XOpenDisplay.restype = [ctypes.c_char_p], ctypes.c_void_p
    lib.XClearArea.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    previous = os.environ.get("XAUTHORITY")
    os.environ["XAUTHORITY"] = str(authority)
    try:
        connection = lib.XOpenDisplay(display.encode())
    finally:
        if previous is None:
            os.environ.pop("XAUTHORITY", None)
        else:
            os.environ["XAUTHORITY"] = previous
    if not connection:
        raise RuntimeError("Cannot open the owned X11 display")
    started, count = time.monotonic(), 0
    try:
        while time.monotonic() - started < seconds:
            lib.XClearArea(connection, int(window), 0, 0, 0, 0, 1)
            lib.XFlush(connection)
            count += 1
            time.sleep(max(0, started + count / hz - time.monotonic()))
        return {"requests": count, "elapsed_seconds": time.monotonic() - started,
                "requested_hz": hz, "method": "XClearArea(exposures=True) + XFlush; events may coalesce"}
    finally:
        lib.XCloseDisplay(connection)


def trial(options, number):
    folder = options.output / f"trial-{number}"
    folder.mkdir(parents=True, exist_ok=False)
    profile = folder / "profile"
    profile.mkdir()
    authority = profile / "Xauthority"
    authority.write_bytes(b"")
    process = display_process = sampler = None
    result = {"trial": number, "status": "failed", "window": [options.width, options.height]}
    with (folder / "launcher.log").open("w") as log, (folder / "display.log").open("w") as display_log:
        try:
            display_process, display = bench.start_private_xvfb(display_log, authority)
            env = dict(os.environ, DISPLAY=display, XAUTHORITY=str(authority),
                       LIBGL_ALWAYS_SOFTWARE="1", XDG_SESSION_TYPE="x11",
                       SDL_VIDEO_DRIVER="x11", ZMCL_GUI_METRICS="1")
            env.pop("WAYLAND_DISPLAY", None)
            for key, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
                path = profile / name
                path.mkdir()
                env[key] = str(path)
            before = bench.sample({display_process.pid})
            started = time.monotonic()
            process = subprocess.Popen([str(options.binary), "--root", str(profile / "zmcl"), "gui"],
                                       cwd=options.binary.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            sampler = ProcessSampler(process.pid, display_process.pid, before, options.sample_interval)

            def xd(*args):
                return subprocess.run(["xdotool", *map(str, args)], env=env, capture_output=True,
                                      text=True, timeout=10)

            window = None
            while time.monotonic() - started < 90:
                if process.poll() is not None:
                    raise RuntimeError("GUI exited before its window became visible")
                found = xd("search", "--onlyvisible", "--name", "^zmcl")
                if found.returncode == 0 and found.stdout.strip():
                    window = found.stdout.splitlines()[0]
                    break
                time.sleep(.1)
            if window is None:
                raise RuntimeError("No visible zmcl window within 90 seconds")
            result["window_visible_ms"] = (time.monotonic() - started) * 1000
            xd("windowmove", window, 0, 0).check_returncode()
            xd("windowsize", window, options.width, options.height).check_returncode()
            time.sleep(options.warmup)

            def take(phase):
                if process.poll() is not None:
                    raise RuntimeError("GUI exited during measurement")
                sampler.take(phase)

            for index in range(options.samples):
                take("idle")
                if index + 1 < options.samples:
                    time.sleep(options.idle_interval)
            result["vm_before_drawing"] = vm_status(process.pid)
            observer_cpu = time.process_time()
            sampler.start("drawing")
            try:
                result["expose"] = expose(display, window, authority, options.drawing_seconds, options.expose_hz)
            finally:
                sampler.stop()
            take("drawing")
            result["controller_cpu_during_drawing_seconds"] = time.process_time() - observer_cpu
            result["vm_after_drawing"] = vm_status(process.pid)
            time.sleep(options.settle)
            for index in range(options.samples):
                take("settled")
                if index + 1 < options.samples:
                    time.sleep(.1)
            geometry = xd("getwindowgeometry", "--shell", window)
            geometry.check_returncode()
            fields = dict(line.split("=", 1) for line in geometry.stdout.splitlines() if "=" in line)
            if [int(fields["WIDTH"]), int(fields["HEIGHT"])] != [options.width, options.height]:
                raise RuntimeError("Measured window did not keep the requested dimensions")
            subprocess.run(["import", "-window", window, str(folder / "window.png")],
                           env=env, check=True, timeout=20)
            result["status"] = "completed"
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            try:
                if sampler is not None:
                    sampler.stop()
                    result["memory"] = sampler.report()
                    if sampler.error or not result["memory"]["summary"]["all_memory_samples_complete"]:
                        result["status"] = "needs_review" if result["status"] == "completed" else result["status"]
                write_json(folder / "result.json", result)
            finally:
                if process is not None:
                    bench.terminate_owned(process, sampler.known if sampler else set())
                if display_process is not None:
                    display_process.terminate()
                    try:
                        display_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        display_process.kill()
                        display_process.wait()
    return result


def aggregate(trials):
    result = {}
    for phase in ("idle", "drawing", "settled"):
        rows = [trial["phases"][phase] for trial in trials]
        result[phase] = {}
        for scope in ("main", "tree", "display_server"):
            result[phase][scope] = {}
            for metric in (*METRICS, "pss_plus_swap_pss_kib"):
                stem = metric.removesuffix("_kib")
                medians = [row[scope][stem + "_median_kib"] for row in rows]
                peaks = [row[scope][stem + "_sampled_peak_kib"] for row in rows]
                result[phase][scope][stem + "_median_of_trial_medians_kib"] = (
                    statistics.median(medians) if medians and all(value is not None for value in medians) else None)
                result[phase][scope][stem + "_sampled_peak_kib"] = max((value for value in peaks if value is not None), default=None)
        cpu = [row["tree_cpu"]["cpu_seconds"] for row in rows]
        result[phase]["tree_cpu_seconds_median"] = statistics.median(cpu) if cpu and all(value is not None for value in cpu) else None
        result[phase]["processes_max"] = max((row["processes_max"] for row in rows), default=None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True, help="Exact source revision and any explicit local patches")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--warmup", type=float, default=20)
    parser.add_argument("--samples", type=int, default=11)
    parser.add_argument("--idle-interval", type=float, default=1)
    parser.add_argument("--sample-interval", type=float, default=.005, help="Sleep after each active /proc scan")
    parser.add_argument("--drawing-seconds", type=float, default=6)
    parser.add_argument("--expose-hz", type=float, default=60)
    parser.add_argument("--settle", type=float, default=3)
    parser.add_argument("--width", type=int, default=1120)
    parser.add_argument("--height", type=int, default=760)
    options = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("This script requires Linux /proc")
    if (not 1 <= options.trials <= 10 or not 2 <= options.samples <= 120 or
            not 0 <= options.warmup <= 600 or not 0 <= options.settle <= 60 or
            not 0 < options.idle_interval <= 60 or not 0 < options.sample_interval <= 1 or
            not 0 < options.drawing_seconds <= 120 or not 0 < options.expose_hz <= 240):
        parser.error("Invalid measurement bounds")
    if not 640 <= options.width <= 1280 or not 480 <= options.height <= 800:
        parser.error("Window must fit the fixed 1280x800 display")
    for program in ("Xvfb", "xdotool", "import"):
        if shutil.which(program) is None:
            parser.error("Missing test utility: " + program)
    options.binary = options.binary.resolve(strict=True)
    options.output = options.output.resolve()
    options.output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": 1, "status": "running", "app": "zmcl", "version": options.version,
        "revision": options.revision, "binary_sha256": bench.digest(options.binary),
        "binary_bytes": options.binary.stat().st_size,
        "benchmark_script_sha256": bench.digest(Path(__file__)),
        "bench_linux_sha256": bench.digest(Path(bench.__file__)),
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "system": {"os": platform.system(), "arch": platform.machine(), "kernel": platform.release(),
                   "logical_cpus": os.cpu_count(), "page_bytes": os.sysconf("SC_PAGE_SIZE"),
                   "memory_at_start_kib": {line.split(":")[0]: int(line.split()[1])
                       for line in Path("/proc/meminfo").read_text().splitlines()
                       if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"))}},
        "scenario": "Fresh empty launcher root; visible library, idle, forced full-window Expose and settled idle",
        "scope": "Main GUI and full related process family/session; Xvfb measured separately before/during",
        "conditions": {key: value for key, value in vars(options).items() if key not in ("binary", "output", "version", "revision")},
        "environment": {"display": "Owned Xvfb 1280x800x24; window moved to 0,0", "data": "Fresh root and XDG directories each trial",
                        "XAUTHORITY": "Fresh empty file; no existing authority file read", "WAYLAND_DISPLAY": "unset",
                        "XDG_SESSION_TYPE": "x11", "LIBGL_ALWAYS_SOFTWARE": "1", "SDL_VIDEO_DRIVER": "x11",
                        "inherited_driver_options": {key: os.environ.get(key) for key in
                            ("LP_NUM_THREADS", "GALLIUM_DRIVER", "MESA_LOADER_DRIVER_OVERRIDE", "MESA_GL_VERSION_OVERRIDE")}},
        "limitations": LIMITS + ["Expose request frequency does not prove rendered FPS.",
                                  "Empty-library activity does not cover file operations, login, downloads or Java/game memory."],
        "trials": [],
    }
    report["conditions"]["cpu_clock_ticks_per_second"] = os.sysconf("SC_CLK_TCK")
    report["conditions"]["os_caches_flushed"] = False
    output = options.output / "activity-measurements.json"
    write_json(output, report)
    try:
        for number in range(1, options.trials + 1):
            if bench.digest(options.binary) != report["binary_sha256"]:
                raise RuntimeError("Measured binary changed before a trial")
            result = trial(options, number)
            if bench.digest(options.binary) != report["binary_sha256"]:
                raise RuntimeError("Measured binary changed during a trial")
            # Raw samples live in each trial, avoiding duplicate multi-MiB data.
            report["trials"].append({"trial": number, "status": result["status"],
                                     "result_file": f"trial-{number}/result.json",
                                     "phases": result["memory"]["phases"],
                                     "main_vm_hwm_max_kib": result["memory"]["summary"]["main_vm_hwm_max_kib"],
                                     "expose": result["expose"],
                                     "sampling_cpu_seconds": result["memory"]["sampling_cpu_seconds"],
                                     "controller_cpu_during_drawing_seconds": result["controller_cpu_during_drawing_seconds"]})
            write_json(output, report)
            print(json.dumps({"trial": number, "status": result["status"], "result_file": str(output.parent / f"trial-{number}/result.json")}), flush=True)
        report["status"] = "completed" if all(row["status"] == "completed" for row in report["trials"]) else "needs_review"
        report["summary"] = aggregate(report["trials"])
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        write_json(output, report)
    print(json.dumps({"status": report["status"], "report": str(output)}), flush=True)


if __name__ == "__main__":
    main()
