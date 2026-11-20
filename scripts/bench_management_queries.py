#!/usr/bin/env python3
"""Compare real Linux management queries, including their complete process family.

Example (run on an otherwise quiet machine):
  python3 scripts/bench_management_queries.py --before /path/before/zmcl \
      --after /path/after/zmcl --python /usr/bin/python3 --output /tmp/query-report

Uses the sibling bench_linux.py sampler, including executable-image attribution.
The output directory must be new or empty. A single temporary launcher root is
removed afterwards; stdout, stderr, raw samples and fixture hashes are retained.
CLI/fixture formats were checked against zmcl e86b5841532ef04cc74cea5585ae396150452408.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

import bench_linux as bench


WORLD_COUNT = 2000
BACKUP_COUNT = 2
BACKUP_FILES = 10000
FILE_BYTES = 64
INTERVAL = 0.005
METRICS = ("rss_kib", "pss_kib", "swap_kib", "swap_pss_kib")
SOURCE = "e86b5841532ef04cc74cea5585ae396150452408"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def process_starttime(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rfind(")") + 2:].split()[19]
    except (OSError, IndexError):
        return None


def controller_memory():
    row = bench.sample({os.getpid()})
    return {"memory": bench.main_process_memory(row, os.getpid()),
            "unread_processes": row["unread_processes"]}


def level_bytes(name):
    # Same valid Java NBT shape as src/worlds/test_manage.py::level_bytes.
    def utf(value):
        raw = value.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw

    def tag(kind, key, payload):
        return bytes([kind]) + utf(key) + payload

    version = tag(8, "Name", utf("1.21.1")) + tag(3, "Id", struct.pack(">i", 3955))
    version += tag(1, "Snapshot", b"\0") + b"\0"
    data = tag(8, "LevelName", utf(name)) + tag(4, "LastPlayed", struct.pack(">q", 1730000000123))
    data += tag(3, "GameType", struct.pack(">i", 1)) + tag(1, "hardcore", b"\0")
    data += tag(1, "Difficulty", b"\2") + tag(3, "DataVersion", struct.pack(">i", 3955))
    data += tag(10, "Version", version) + b"\0"
    return b"\x0a\0\0" + tag(10, "Data", data) + b"\0"


def zip_info(name, directory=False):
    info = zipfile.ZipInfo(name + ("/" if directory else ""))
    info.create_system = 3
    mode = (stat.S_IFDIR | 0o700) if directory else (stat.S_IFREG | 0o600)
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
    return info


def make_backup(path, number, created):
    metadata = {"format_version": 1, "policy": "mutable-game-v1", "instance": "demo",
                "created": created, "label": f"Fixture {number}", "roots": ["config"],
                "directories": ["config"], "files": []}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr(zip_info("game/config", True), b"")
        for index in range(BACKUP_FILES):
            relative = f"config/file-{index:05d}.dat"
            payload = hashlib.sha256(f"{number}:{index}".encode("ascii")).digest() * 2
            metadata["files"].append({"path": relative, "size": len(payload),
                                      "sha256": hashlib.sha256(payload).hexdigest()})
            archive.writestr(zip_info("game/" + relative), payload, compresslevel=1)
        body = json_bytes(metadata)
        archive.writestr(zip_info("zmcl-backup.json"), body, compresslevel=1)
    path.chmod(0o600)
    # The real list command validates structure, not payload hashes. Verify all
    # generated payloads here without changing or bypassing the CLI's checks.
    with zipfile.ZipFile(path) as archive:
        saved = json.loads(archive.read("zmcl-backup.json"))
        if saved != metadata or len(archive.infolist()) != BACKUP_FILES + 2:
            raise ValueError("Generated backup manifest/entry mismatch")
        if archive.read("game/config/") != b"":
            raise ValueError("Generated backup directory is not empty")
        for entry in saved["files"]:
            payload = archive.read("game/" + entry["path"])
            if len(payload) != FILE_BYTES or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("Generated backup payload failed SHA-256 verification")
        ratio = max(info.file_size / max(1, info.compress_size) for info in archive.infolist())
    return {"id": path.stem, "bytes": path.stat().st_size, "sha256": sha256(path),
            "manifest_bytes": len(body), "manifest_sha256": hashlib.sha256(body).hexdigest(),
            "files": BACKUP_FILES, "file_bytes": BACKUP_FILES * FILE_BYTES,
            "maximum_compression_ratio": ratio, "all_payload_sha256_verified": True}


def make_fixtures(root):
    before, started = controller_memory(), time.monotonic()
    saves = root / "instances/demo/game/saves"
    saves.mkdir(parents=True, exist_ok=True)
    collection, total_bytes = hashlib.sha256(), 0
    for index in range(WORLD_COUNT):
        world_id = f"world-{index:04d}"
        target = saves / world_id
        target.mkdir()
        title = f"Fixture World {index:04d}".ljust(160, "x")
        payload = gzip.compress(level_bytes(title), mtime=0)
        (target / "level.dat").write_bytes(payload)
        collection.update(world_id.encode("ascii") + b"\0" + payload)
        total_bytes += len(payload)
    backups = root / "backups/demo"
    backups.mkdir(parents=True, mode=0o700)
    created = int(time.time())
    archives = [make_backup(backups / f"fixture-{number:02d}.zip", number, created)
                for number in range(1, BACKUP_COUNT + 1)]
    return {"worlds": WORLD_COUNT, "world_title_ascii_bytes": 160, "world_level_dat_bytes": total_bytes,
            "world_collection_sha256": collection.hexdigest(),
            "world_collection_hash_format": "sorted world id ASCII, NUL, compressed level.dat bytes",
            "backups": archives, "created": created, "wall_seconds": time.monotonic() - started,
            "controller_before": before, "controller_after": controller_memory()}


def take_sample(leader, leader_birth, known, started, leader_running):
    read_started = time.monotonic()
    retained = {pid for pid, birth in known.items() if process_starttime(pid) == birth}
    pids = bench.family(leader, retained)
    expected, mismatches = {}, []
    for pid in sorted(pids):
        birth = process_starttime(pid)
        previous = leader_birth if pid == leader else known.get(pid)
        if previous is not None and birth is not None and previous != birth:
            mismatches.append(pid)
            continue
        if birth is not None:
            expected[pid] = birth
    pids.difference_update(mismatches)
    if not leader_running and not pids:
        return None
    row = bench.sample(pids)
    # sample() brackets /proc reads; this additional comparison also excludes a
    # reused PID between family discovery and the sampler's first stat read.
    for identity, process in list(row["process_memory"].items()):
        if expected.get(process["pid"]) != identity.split(":", 1)[1]:
            mismatches.append(process["pid"])
            del row["process_memory"][identity]
            row["cpu_ticks"].pop(identity, None)
    observed = {process["pid"] for process in row["process_memory"].values()}
    unread = set(row["unread_processes"]) | (pids - observed) | set(mismatches)
    if leader_running and leader not in observed:
        unread.add(leader)
    row.update({metric: sum(p[metric] for p in row["process_memory"].values()) for metric in METRICS})
    row.update(processes=len(observed), unread_processes=sorted(unread), requested_pids=sorted(pids),
               process_names=sorted(p["name"] for p in row["process_memory"].values()),
               main_process=bench.main_process_memory(row, leader),
               identity_mismatches=sorted(set(mismatches)),
               leader_running_at_discovery=leader_running,
               elapsed_seconds=time.monotonic() - started,
               read_duration_seconds=time.monotonic() - read_started)
    known.clear()
    known.update(expected)
    return row


def terminate_session(process, leader_birth):
    """Signal only members of this Popen(start_new_session=True) session."""
    signals = []
    if leader_birth is None:
        # Popen retains ownership of its unreaped direct child, but without a
        # birth identity we cannot safely claim the wider session for cleanup.
        signals.append({"scope": "direct_child_only", "ownership_gap":
                        "Leader starttime unavailable; descendants were not signalled"})
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if process.poll() is not None:
                break
            try:
                if sig == signal.SIGTERM:
                    process.terminate()
                else:
                    process.kill()
                signals.append({"pid": process.pid, "starttime_ticks": None,
                                "scope": "direct_child_only", "signal": sig.name})
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            except ProcessLookupError:
                break
        return signals
    for sig, grace in ((signal.SIGTERM, 1.0), (signal.SIGKILL, 1.0)):
        # An exited leader can leave its helpers behind in the original session.
        # Never interpret a reused leader PID as ownership of a new session.
        now_birth = process_starttime(process.pid)
        if now_birth is not None and leader_birth is not None and now_birth != leader_birth:
            break
        for pid in sorted(bench.family(process.pid, set()), reverse=True):
            birth = process_starttime(pid)
            try:
                if birth is not None and os.getsid(pid) == process.pid and process_starttime(pid) == birth:
                    os.kill(pid, sig)
                    signals.append({"pid": pid, "starttime_ticks": birth, "signal": sig.name})
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.monotonic() + grace
        live = [process.pid]  # Unknown until scanned; do not assume exit after a scheduling delay.
        while time.monotonic() < deadline:
            process.poll()
            live = []
            for pid in bench.family(process.pid, set()):
                try:
                    if os.getsid(pid) == process.pid:
                        raw = Path(f"/proc/{pid}/stat").read_text()
                        if raw[raw.rfind(")") + 2:].split()[0] != "Z":
                            live.append(pid)
                except (OSError, IndexError):
                    pass
            if not live:
                break
            time.sleep(0.01)
        if not live:
            break
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    return signals


def summarize_samples(rows):
    complete = bool(rows) and all(not r["unread_processes"] and r["processes"] for r in rows)
    images, transitions, previous_images = {}, [], {}
    for row in rows:
        grouped = {}
        for identity, process in row["process_memory"].items():
            key = (process["executable"], process["executable_identity"])
            item = images.setdefault(key, {"executable": key[0], "executable_identity": key[1],
                                          "process_identities": set(), "process_names": set(),
                                          "sample_count": 0, "peak_processes": 0,
                                          "observed_peak_kib": dict.fromkeys(METRICS, 0)})
            item["process_identities"].add(identity)
            item["process_names"].add(process["name"])
            grouped.setdefault(key, []).append(process)
            previous = previous_images.get(identity)
            if previous is not None and previous[0] != key:
                transitions.append({"process_identity": identity, "from": list(previous[0]),
                                    "to": list(key), "between_elapsed_seconds": [previous[1], row["elapsed_seconds"]]})
            previous_images[identity] = (key, row["elapsed_seconds"])
        for key, processes in grouped.items():
            item = images[key]
            item["sample_count"] += 1
            item["peak_processes"] = max(item["peak_processes"], len(processes))
            for metric in METRICS:
                item["observed_peak_kib"][metric] = max(item["observed_peak_kib"][metric],
                                                      sum(p[metric] for p in processes))
    for item in images.values():
        item["process_identities"] = sorted(item["process_identities"])
        item["process_names"] = sorted(item["process_names"])
    main = [r["main_process"] for r in rows if r["main_process"] is not None]
    intervals = [b["elapsed_seconds"] - a["elapsed_seconds"] for a, b in zip(rows, rows[1:])]
    return {"sample_count": len(rows), "tree_reads_complete": complete,
            "samples_with_unread_processes": sum(bool(r["unread_processes"]) for r in rows),
            "unread_process_reads": sum(len(r["unread_processes"]) for r in rows),
            "tree_median_kib": {m: statistics.median(r[m] for r in rows) if complete else None for m in METRICS},
            "tree_observed_peak_kib": {m: max((r[m] for r in rows if r["processes"]), default=None) for m in METRICS},
            "main_median_kib": {m: bench.complete_main_median(rows, m) for m in METRICS},
            "main_observed_peak_kib": {m: max((r[m] for r in main), default=None) for m in METRICS},
            "observed_peak_processes": max((r["processes"] for r in rows), default=0),
            "observed_executables": sorted(images.values(), key=lambda image: (image["executable"], image["executable_identity"])),
            "observed_image_transitions": transitions,
            "actual_interval_seconds_median": statistics.median(intervals) if intervals else None,
            "actual_interval_seconds_max": max(intervals, default=None),
            "sample_read_seconds_max": max((r["read_duration_seconds"] for r in rows), default=None)}


def inspect_response(path, operation):
    result = {"bytes": path.stat().st_size, "sha256": sha256(path),
              "json_count": None, "canonical_json_sha256": None, "expected_response": False}
    try:
        if result["bytes"] > 16 * 1024 * 1024:
            raise ValueError("Response exceeds the benchmark's 16 MiB JSON inspection limit")
        value = json.loads(path.read_bytes())
        result["json_count"] = len(value) if isinstance(value, list) else None
        result["canonical_json_sha256"] = hashlib.sha256(json_bytes(value)).hexdigest()
        if operation == "instance-create":
            expected = isinstance(value, dict) and value.get("id") == "demo" and value.get("version") == "1.21.1"
        elif operation == "world-list":
            expected = (isinstance(value, list) and len(value) == WORLD_COUNT and
                        all(isinstance(row, dict) and row.get("valid") is True for row in value) and
                        {row.get("id") for row in value} == {f"world-{i:04d}" for i in range(WORLD_COUNT)})
        else:
            expected = (isinstance(value, list) and len(value) == BACKUP_COUNT and
                        all(isinstance(row, dict) and row.get("manifest_valid") is True and
                            row.get("data_verified") is False and row.get("files") == BACKUP_FILES and
                            row.get("file_bytes") == BACKUP_FILES * FILE_BYTES and
                            row.get("policy") == "mutable-game-v1" and row.get("roots") == ["config"]
                            for row in value) and
                        {row.get("id") for row in value} == {f"fixture-{i:02d}" for i in range(1, BACKUP_COUNT + 1)})
        result["expected_response"] = expected
    except (OSError, ValueError, TypeError) as error:
        result["error"] = str(error)
    return result


def run_query(binary, root, arguments, operation, label, output, environment, timeout):
    command = [str(binary), "--root", str(root), *arguments]
    stdout_path, stderr_path = output / f"{label}.stdout", output / f"{label}.stderr"
    samples_path = output / f"{label}.samples.jsonl"
    controller_before, started = controller_memory(), time.monotonic()
    rows, known, cleanup_signals = [], {}, []
    timed_out = False
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr, samples_path.open("x") as raw:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   env=environment, start_new_session=True)
        birth = process_starttime(process.pid)
        if birth is not None:
            known[process.pid] = birth
        due = started
        try:
            while True:
                row = take_sample(process.pid, birth, known, started, process.poll() is None)
                if row is None:
                    break
                rows.append(row)
                raw.write(json_bytes(row).decode("ascii") + "\n")
                now = time.monotonic()
                if now - started >= timeout:
                    timed_out = True
                    cleanup_signals = terminate_session(process, birth)
                    break
                due = max(due + INTERVAL, now)
                time.sleep(max(0, due - time.monotonic()))
        except BaseException:
            terminate_session(process, birth)
            raise
        returncode = process.poll()
    wall = time.monotonic() - started
    controller_after = controller_memory()
    response = inspect_response(stdout_path, operation)
    return {"label": label, "operation": operation, "command": command, "pid": process.pid,
            "starttime_ticks": birth, "exitcode": returncode, "timed_out": timed_out,
            "wall_seconds": wall, "cleanup_signals": cleanup_signals,
            "stdout": stdout_path.name, "stderr": stderr_path.name, "samples": samples_path.name,
            "stderr_bytes": stderr_path.stat().st_size, "stderr_sha256": sha256(stderr_path),
            "samples_sha256": sha256(samples_path), "response": response,
            "valid": returncode == 0 and not timed_out and response["expected_response"],
            "controller_before": controller_before, "controller_after": controller_after,
            "memory": summarize_samples(rows)}


def comparison(runs):
    result = {}
    for operation in ("world-list", "backup-list"):
        selected = [run for run in runs if run["operation"] == operation]
        hashes = [run["response"]["canonical_json_sha256"] for run in selected]
        entry = {"all_responses_equal": bool(hashes) and None not in hashes and len(set(hashes)) == 1}
        for variant in ("before", "after"):
            group = [run for run in selected if run["variant"] == variant]
            valid = bool(group) and all(run["valid"] for run in group)
            medians = {}
            for metric in METRICS:
                values = [run["memory"]["tree_median_kib"][metric] for run in group]
                medians[metric] = statistics.median(values) if values and None not in values else None
            entry[variant] = {"runs": len(group), "all_valid": valid,
                              "wall_seconds_median": statistics.median(r["wall_seconds"] for r in group) if valid else None,
                              "median_of_run_tree_medians_kib": medians,
                              "tree_reads_complete": bool(group) and all(r["memory"]["tree_reads_complete"] for r in group),
                              "tree_observed_peak_kib": {m: max((r["memory"]["tree_observed_peak_kib"][m] for r in group
                                                               if r["memory"]["tree_observed_peak_kib"][m] is not None),
                                                              default=None) for m in METRICS}}
        result[operation] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, help="Set ZMCL_PYTHON to this literal executable path; otherwise inherit the environment")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=360.0, help="Seconds per CLI invocation (default: 360)")
    options = parser.parse_args()
    if not sys.platform.startswith("linux") or not Path("/proc/self/smaps_rollup").exists():
        parser.error("Linux with readable /proc/*/smaps_rollup is required")
    if options.repeats < 1 or not 0 < options.timeout < float("inf"):
        parser.error("--repeats and --timeout must be positive finite values")
    binaries = {name: getattr(options, name).expanduser().resolve(strict=True) for name in ("before", "after")}
    for path in binaries.values():
        if not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f"Not an executable file: {path}")
    environment = os.environ.copy()
    changes = {}
    if options.python is not None:
        python = options.python.expanduser().absolute()
        if not python.is_file() or not os.access(python, os.X_OK):
            parser.error(f"Not a Python executable path: {python}")
        changes["ZMCL_PYTHON"] = {"before": environment.get("ZMCL_PYTHON"), "after": str(python)}
        environment["ZMCL_PYTHON"] = str(python)
    output = options.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be new or empty; existing evidence will not be overwritten")
    script, sampler = Path(__file__).resolve(), Path(bench.__file__).resolve()
    report = {"schema_version": 1, "source_format_reference": SOURCE,
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "platform": platform.platform(), "controller_python": sys.version,
              "controller_executable": sys.executable, "controller_pid": os.getpid(),
              "clock_ticks": os.sysconf("SC_CLK_TCK"), "page_bytes": os.sysconf("SC_PAGE_SIZE"),
              "script": {"path": str(script), "sha256": sha256(script)},
              "sampler": {"path": str(sampler), "sha256": sha256(sampler)},
              "binaries": {name: {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
                           for name, path in binaries.items()},
              "environment_changes": changes,
              "effective_environment": {key: environment.get(key) for key in ("PATH", "ZMCL_PYTHON", "PYTHONHOME", "PYTHONPATH")},
              "requested_sample_interval_seconds": INTERVAL, "repeats": options.repeats,
              "timeout_seconds": options.timeout, "order": "Adjacent before/after pairs; alternate AB/BA by repeat",
              "conditions": [
                  "Local CLI only: instance create, world list and backup list; no game, GUI or download is started.",
                  "The same temporary launcher root and fixtures serve both binaries and all repetitions; no caches are dropped.",
                  "CLI scope includes the main process, discovered descendants/session members and still-valid previously seen PID:starttime identities.",
                  "Controller, fixture construction and its payload verification are excluded from CLI totals; separate before/after controller snapshots are not controller peaks.",
                  "RSS sums can double-count shared pages; PSS apportions them. All units are KiB (1024 bytes). Swap and SwapPss remain visible.",
                  "Tree medians are null after any missing read; main medians require every main sample. Repetition summaries never discard null medians.",
                  "All sampled peaks are lower bounds. Per-executable peaks sum only processes present in the same sample, never different process maxima.",
                  "Sampling targets 5 ms, not a guaranteed cadence. Sampling/file-write overhead affects wall time; actual gaps and read durations are reported.",
                  "Executable identities are device:inode, separately from PID:starttime. Exec changes seen between samples are reported; short-lived processes, same-image re-exec or changes wholly between samples can be missed.",
                  "Backup listing validates manifests/ZIP structure but reports data_verified:false. Fixture payload SHA-256 verification happens before timing.",
              ], "runs": []}
    code = 1
    try:
        with tempfile.TemporaryDirectory(prefix="zmcl-management-", dir=output) as directory:
            root = Path(directory)
            report["temporary_root"] = str(root)
            setup = run_query(binaries["before"], root, ["instance", "create", "demo", "--version", "1.21.1"],
                              "instance-create", "setup", output, environment, options.timeout)
            report["setup"] = setup
            if not setup["valid"]:
                raise ValueError("Instance creation failed; see setup stdout/stderr")
            report["fixtures"] = make_fixtures(root)
            for repeat in range(1, options.repeats + 1):
                order = ("before", "after") if repeat % 2 else ("after", "before")
                for operation, args in (("world-list", ["world", "list", "demo"]),
                                        ("backup-list", ["backup", "list", "demo"])):
                    for variant in order:
                        run = run_query(binaries[variant], root, args, operation,
                                        f"{operation}-{repeat:02d}-{variant}", output, environment, options.timeout)
                        run.update(variant=variant, repeat=repeat)
                        report["runs"].append(run)
            report["comparison"] = comparison(report["runs"])
            for name, path in binaries.items():
                report["binaries"][name]["sha256_after"] = sha256(path)
            report["binaries_unchanged"] = all(value["sha256"] == value["sha256_after"] for value in report["binaries"].values())
            code = 0 if (report["binaries_unchanged"] and all(r["valid"] for r in report["runs"]) and
                         all(c["all_responses_equal"] for c in report["comparison"].values())) else 1
    except (OSError, ValueError, KeyboardInterrupt) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["temporary_root_removed"] = not Path(report.get("temporary_root", str(output))).exists()
        report["controller_final"] = controller_memory()
        report["exitcode"] = code
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"report": str(output / "report.json"), "exitcode": code}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
