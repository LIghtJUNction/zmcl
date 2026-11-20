#!/usr/bin/env python3
"""Exercise the real native management pages on a private X11 display.

Uses fresh data, synthetic Java NBT and an uninstalled server fixture. No provider
downloads, Minecraft launch or EULA acceptance. Optional --memory-output records
observed process-tree peaks during these flows; it is not an all-states memory
bound. Inspect the screenshots as well as the verified file effects.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("world_fixture", ROOT / "src/worlds/test_manage.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def wait_for(predicate, description, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError(description)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".test-evidence/gui-management"))
    parser.add_argument("--memory-output", type=Path, help="Optional JSON file for main/tree/display memory during the six flows")
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    for tool in ("Xvfb", "xdotool", "import"):
        if not shutil.which(tool):
            parser.error("Missing test utility: " + tool)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    display = gui = None
    sampler = memory = None
    memory_started_sha256 = None
    if args.memory_output is not None:
        memory_spec = importlib.util.spec_from_file_location("gui_activity", ROOT / "scripts/bench_gui_activity.py")
        memory = importlib.util.module_from_spec(memory_spec)
        memory_spec.loader.exec_module(memory)
        memory_started_sha256 = memory.bench.digest(binary)
    with tempfile.TemporaryDirectory(prefix="private-", dir=output) as temporary:
        base = Path(temporary)
        data = base / "launcher data"
        source = base / "source world"
        source.mkdir()
        level = gzip.compress(fixture.level_bytes("Native GUI world"), mtime=0)
        (source / "level.dat").write_bytes(level)
        (source / "region").mkdir()
        original_region = bytes(range(256)) * 4
        (source / "region/r.0.0.mca").write_bytes(original_region)

        def cli(*argv):
            result = subprocess.run([str(binary), "--root", str(data), *argv],
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr[-2000:]
            return json.loads(result.stdout)

        cli("instance", "create", "ui-demo", "--version", "1.21.1")
        game = data / "instances/ui-demo/game"
        (game / "resourcepacks").mkdir()
        (game / "resourcepacks/manual.zip").write_bytes(b"unmanaged fixture")
        env = dict(os.environ, LIBGL_ALWAYS_SOFTWARE="1")
        env.pop("WAYLAND_DISPLAY", None)
        env["XDG_SESSION_TYPE"] = "x11"
        env["XAUTHORITY"] = str(base / "Xauthority")
        Path(env["XAUTHORITY"]).write_bytes(b"")
        read_fd, write_fd = os.pipe()
        try:
            with (output / "display.log").open("wb") as display_log, (output / "launcher.log").open("wb") as gui_log:
                display = subprocess.Popen(
                    ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x900x24",
                     "-nolisten", "tcp", "-noreset", "-auth", env["XAUTHORITY"]],
                    pass_fds=(write_fd,), env=env, stdout=subprocess.DEVNULL, stderr=display_log)
                os.close(write_fd)
                write_fd = -1
                assert select.select([read_fd], [], [], 10)[0], "Private display did not start"
                number = os.read(read_fd, 64).decode().strip()
                assert number.isdigit()
                env["DISPLAY"] = ":" + number

                def xd(*argv):
                    return subprocess.check_output(["xdotool", *map(str, argv)], env=env,
                                                   text=True, timeout=10).strip()

                display_before = memory.bench.sample({display.pid}) if memory else None
                gui = subprocess.Popen([str(binary), "--root", str(data), "gui"], env=env,
                                       stdin=subprocess.DEVNULL, stdout=gui_log, stderr=gui_log,
                                       start_new_session=memory is not None)
                if memory is not None:
                    sampler = memory.ProcessSampler(gui.pid, display.pid, display_before)
                    sampler.start()

                def memory_phase(name):
                    if sampler is not None:
                        sampler.phase(name)

                window = xd("search", "--sync", "--onlyvisible", "--name", "^zmcl").splitlines()[0]
                geometry = dict(line.split("=", 1) for line in xd("getwindowgeometry", "--shell", window).splitlines() if "=" in line)
                width, height = int(geometry["WIDTH"]), int(geometry["HEIGHT"])
                assert width >= 1000 and height >= 700, geometry
                xd("windowfocus", "--sync", window)
                x, margin = 234, 42
                content_width = width - x - margin
                footer = height - 177
                form_width, form_height = min(width - 48, 560), min(height - 36, 370)
                fx, fy = (width - form_width) / 2, (height - form_height) / 2

                def idle():
                    quiet_since = None
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        assert gui.poll() is None, "GUI exited during acceptance"
                        children = Path(f"/proc/{gui.pid}/task/{gui.pid}/children").read_text().strip()
                        if children:
                            quiet_since = None
                        elif quiet_since is None:
                            quiet_since = time.monotonic()
                        elif time.monotonic() - quiet_since >= 1.2:
                            return
                        time.sleep(.1)
                    raise AssertionError("GUI operation did not become idle")

                def click(px, py):
                    xd("mousemove", "--window", window, round(px), round(py))
                    xd("click", 1)
                    time.sleep(.15)

                def field(px, py, value):
                    click(px, py)
                    xd("key", "--window", window, "ctrl+a")
                    xd("type", "--clearmodifiers", "--window", window, "--delay", 1, value)

                def capture(name):
                    subprocess.run(["import", "-display", env["DISPLAY"], "-window", window,
                                    str(output / name)], env=env, check=True, timeout=10)

                def submit():
                    click(fx + form_width / 2, fy + form_height - 45)

                idle()
                capture("01-library.png")
                memory_phase("world_import")
                click(x + 332, height - 109)
                idle()
                capture("02-worlds-empty.png")
                click(x + 76, 230)
                field(fx + form_width / 2, fy + 153, str(source))
                field(fx + form_width / 2, fy + 237, "Imported world")
                capture("03-world-import.png")
                submit()
                imported = game / "saves/Imported world"
                wait_for(lambda: (imported / "level.dat").is_file(), "GUI world import did not create the save")
                idle()
                assert (imported / "level.dat").read_bytes() == level
                results.append({"case": "world import through native form", "passed": True})
                capture("04-worlds-imported.png")

                memory_phase("world_export_roundtrip")
                click(x + 76, footer + 20)
                exported = base / "exported world.zip"
                field(fx + form_width / 2, fy + 153, str(exported))
                submit()
                wait_for(exported.is_file, "GUI world export did not create ZIP")
                idle()
                assert zipfile.is_zipfile(exported)
                cli("world", "import", "--", "ui-demo", str(exported), "Roundtrip")
                assert (game / "saves/Roundtrip/level.dat").read_bytes() == level
                results.append({"case": "GUI export and CLI ZIP import roundtrip", "passed": True})

                memory_phase("backup_restore")
                click(x + 218, 172)
                idle()
                click(x + 76, 230)
                field(fx + form_width / 2, fy + 153, "Before changes")
                submit()
                backups = data / "backups/ui-demo"
                wait_for(lambda: backups.is_dir() and len(list(backups.glob("*.zip"))) >= 1, "GUI backup was not created")
                idle()
                (imported / "region/r.0.0.mca").write_bytes(b"changed before restore")
                click(x + 76, footer + 20)
                capture("05-restore-confirmation.png")
                submit()
                wait_for(lambda: (imported / "region/r.0.0.mca").read_bytes() == original_region,
                         "GUI restore did not restore original bytes")
                idle()
                assert len(list(backups.glob("*.zip"))) >= 2
                results.append({"case": "GUI backup and confirmed restore with retained pre-restore snapshot", "passed": True})
                capture("06-backups-restored.png")

                memory_phase("resources")
                click(x + 366, 172)
                idle()
                assert (game / "resourcepacks/manual.zip").read_bytes() == b"unmanaged fixture"
                capture("07-resources-unmanaged.png")
                results.append({"case": "resources page with retained unmanaged fixture", "passed": True})

                memory_phase("server_create")
                click(96, 367)
                idle()
                click(x + 76, 175)
                field(fx + form_width / 2, fy + 153, "local-gui")
                field(fx + form_width / 2, fy + 237, "1.21.1")
                submit()
                server = data / "servers/local-gui"
                wait_for(lambda: (server / "server.json").is_file(), "GUI server create failed")
                idle()
                eula = (server / "eula.txt").read_bytes()
                assert b"eula=false" in eula
                capture("08-server-created.png")
                results.append({"case": "server created in GUI without EULA acceptance", "passed": True})
                memory_phase("server_eula_rejection")
                # Synthetic installed state reaches the existing EULA rejection path.
                config_path = server / "server.json"
                config = json.loads(config_path.read_text())
                config.update(installed=True, java_major=21, server_sha1="0" * 40, jar_size=1)
                config_path.write_text(json.dumps(config))
                click(x + 207, 175)
                idle()
                button_width = min((content_width - 20) / 3, 152)
                click(x + button_width + 10 + button_width / 2, footer + 20)
                idle()
                assert (server / "eula.txt").read_bytes() == eula
                assert not (server / "process.json").exists()
                capture("09-server-eula-rejection.png")
                results.append({"case": "synthetic installed server remains stopped with EULA false; inspect screenshot for visible error", "passed": True})

                if sampler is not None:
                    sampler.stop()
                click(96, height - 38)
                assert gui.wait(timeout=10) == 0
                result = {"scope": "Native X11 clicks plus isolated file effects; no memory benchmark",
                          "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                          "window": [width, height], "cases": results,
                          "limits": ["Synthetic Java save", "No actual Minecraft server installed or started",
                                     "No online resource download", "Screenshots require visual inspection"]}
                if args.memory_output is not None:
                    result["scope"] = "Native X11 clicks plus isolated file effects; optional memory observations saved separately"
                    result["memory_output"] = str(args.memory_output.resolve())
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(result, indent=2))
        finally:
            try:
                if sampler is not None:
                    sampler.stop()
                    report = sampler.report()
                    report.update(schema=1, scenario="Six native GUI management flows with synthetic local fixtures",
                                  binary_sha256=memory_started_sha256, binary_sha256_after=memory.bench.digest(binary),
                                  benchmark_script_sha256=memory.bench.digest(Path(__file__)),
                                  activity_sampler_sha256=memory.bench.digest(Path(memory.__file__)),
                                  bench_linux_sha256=memory.bench.digest(Path(memory.bench.__file__)),
                                  recorded_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  functional_cases=results, functional_complete=len(results) == 6 and gui.returncode == 0,
                                  conditions={"sample_sleep_seconds": sampler.interval, "display": "Owned Xvfb 1280x900x24",
                                              "window": [width, height] if "width" in locals() else None,
                                              "warmup": "Existing idle waits; startup and all six flows sampled",
                                              "cpu_clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                                              "os_caches_flushed": False, "LIBGL_ALWAYS_SOFTWARE": "1"})
                    report["limitations"] = report["limitations"] + [
                        "Observed GUI-owned CLI/Python descendants are included; fixture and ZIP roundtrip CLI calls made by the test controller are excluded.",
                        "No Minecraft/Java process, account login, online download or real server launch is exercised.",
                        "Small synthetic files do not represent a large world or modpack workload.",
                    ]
                    report["status"] = ("completed" if report["functional_complete"] and not sampler.error
                                        and report["binary_sha256"] == report["binary_sha256_after"]
                                        and report["summary"]["all_memory_samples_complete"] else "needs_review")
                    memory.write_json(args.memory_output.resolve(), report)
            finally:
                if sampler is not None:
                    memory.bench.terminate_owned(gui, sampler.known)
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)
                for process in (gui, display):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()



if __name__ == "__main__":
    main()
