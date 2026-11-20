#!/usr/bin/env python3
"""Exercise launcher update states through real X11 input and a PATH curl fixture.

No production endpoint override or GUI test hook is used. The supplied executable
is run in place: this also supports acceptance of an actually installed package.
Screenshots must be visually reviewed; no real network update or installation is
performed by this test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time


API = "https://api.github.com/repos/LIghtJUNction/zmcl/releases?per_page=10"
PAGE = "https://github.com/LIghtJUNction/zmcl/releases"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    binary = options.binary.resolve(strict=True)
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("Output must be empty; existing evidence is preserved")
    for tool in ("Xvfb", "xdotool", "import"):
        if not shutil.which(tool):
            parser.error("Missing test utility: " + tool)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    version_output = subprocess.check_output([str(binary), "--version"], text=True, timeout=10).strip()
    assert version_output.startswith("zmcl "), version_output
    version = version_output[len("zmcl "):]
    numbers = [int(part) for part in version.split("-")[0].split("+")[0].split(".")]
    newer = ".".join(map(str, [numbers[0], numbers[1], numbers[2] + 1]))
    older = "0.0.0"
    cases = []
    gui = display = None
    with tempfile.TemporaryDirectory(prefix="private-update-", dir=output) as temporary:
        base = Path(temporary)
        helpers = base / "helpers"
        helpers.mkdir()
        state = helpers / "state.json"
        calls = helpers / "calls.jsonl"
        opened = helpers / "opened.txt"
        curl = helpers / "curl"
        curl.write_text("#!" + sys.executable + "\n" + '''import json,sys,time
from pathlib import Path
base=Path(__file__).parent
value=json.loads((base/'state.json').read_text())
with (base/'calls.jsonl').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')
time.sleep(value.get('delay',0))
if value.get('exit',0): raise SystemExit(value['exit'])
sys.stdout.write(value['body']+'\\n'+str(value.get('status',200)))
''')
        curl.chmod(0o755)
        opener = helpers / "xdg-open"
        opener.write_text("#!" + sys.executable + "\n" + '''import sys
from pathlib import Path
(Path(__file__).parent/'opened.txt').write_text(sys.argv[1])
status=Path(__file__).parent/'browser-exit.txt'
raise SystemExit(int(status.read_text()) if status.exists() else 0)
''')
        opener.chmod(0o755)
        env = dict(os.environ, PATH=str(helpers) + os.pathsep + os.environ.get("PATH", ""),
                   LIBGL_ALWAYS_SOFTWARE="1", XDG_SESSION_TYPE="x11")
        env.pop("WAYLAND_DISPLAY", None)
        env["XAUTHORITY"] = str(base / "Xauthority")
        Path(env["XAUTHORITY"]).write_bytes(b"")

        def fixture(release_version=None, package=True, **kwargs):
            assets = []
            if package:
                name = f"zmcl-{release_version}-linux-x86_64.tar.gz"
                assets.append({"name": name, "size": 1234,
                               "browser_download_url": f"{PAGE}/download/v{release_version}/{name}"})
            body = [{"tag_name": "v" + release_version, "draft": False, "prerelease": True,
                     "html_url": f"{PAGE}/tag/v{release_version}", "assets": assets}] if release_version else []
            state.write_text(json.dumps({"body": json.dumps(body), **kwargs}))

        def requests():
            return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

        fixture(newer)
        # Validate the actual executable's CLI response and the helper invocation.
        response = json.loads(subprocess.check_output([str(binary), "update", "check", "--json"], env=env, text=True, timeout=25))
        assert response["status"] == "available" and response["available_version"] == newer, response
        assert response["release_url"] == f"{PAGE}/tag/v{newer}", response
        for request in requests():
            assert request[-1] == API and request[request.index("--max-time") + 1] == "20", request
        calls.unlink()
        read_fd, write_fd = os.pipe()
        try:
            with (output / "display.log").open("wb") as display_log, (output / "launcher.log").open("wb") as gui_log:
                display = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x900x24",
                                            "-nolisten", "tcp", "-noreset", "-auth", env["XAUTHORITY"]],
                                           pass_fds=(write_fd,), env=env, stdout=subprocess.DEVNULL, stderr=display_log)
                os.close(write_fd)
                write_fd = -1
                assert select.select([read_fd], [], [], 10)[0], "Private display did not start"
                number = os.read(read_fd, 64).decode().strip()
                assert number.isdigit()
                env["DISPLAY"] = ":" + number

                def xd(*arguments):
                    return subprocess.check_output(["xdotool", *map(str, arguments)], env=env, text=True, timeout=10).strip()

                gui = subprocess.Popen([str(binary), "--root", str(base / "launcher data"), "gui"], env=env,
                                       stdin=subprocess.DEVNULL, stdout=gui_log, stderr=gui_log)
                window = xd("search", "--sync", "--onlyvisible", "--name", "^zmcl").splitlines()[0]
                xd("windowfocus", "--sync", window)

                def idle(timeout=25):
                    quiet = None
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        assert gui.poll() is None, "GUI exited during acceptance"
                        children = Path(f"/proc/{gui.pid}/task/{gui.pid}/children").read_text().strip()
                        if children:
                            quiet = None
                        elif quiet is None:
                            quiet = time.monotonic()
                        elif time.monotonic() - quiet >= .6:
                            return
                        time.sleep(.1)
                    raise AssertionError("GUI task did not become idle")

                def click(x, y):
                    xd("mousemove", "--window", window, round(x), round(y))
                    xd("click", 1)
                    time.sleep(.15)

                def capture(name):
                    time.sleep(.2)
                    subprocess.run(["import", "-display", env["DISPLAY"], "-window", window, str(output / name)],
                                   env=env, check=True, timeout=10)

                idle()
                click(95, 421)
                capture("01-settings-1120.png")
                assert not requests(), "Settings must not check in the background"
                fixture(newer, delay=2)
                click(334, 392)
                capture("02-checking-1120.png")
                idle()
                assert len(requests()) == 1
                capture("03-available-1120.png")
                cases.append({"case": "Explicit update check and available preview", "expected_state": "available", "passed": True})

                xd("windowsize", "--sync", window, 800, 600)
                time.sleep(.3)
                capture("04-available-800.png")
                click(430, 392)
                idle()
                assert opened.read_text() == f"{PAGE}/tag/v{newer}"
                cases.append({"case": "Release action opens only the official version page", "passed": True})
                (helpers / "browser-exit.txt").write_text("3")
                click(430, 392)
                idle()
                capture("11-browser-failure-800.png")
                cases.append({"case": "Browser exit 3 reports a copy-link fallback", "expected_state": "browser_error", "passed": True})
                (helpers / "browser-exit.txt").unlink()
                tests = [("05-current-800.png", version, True, {}, "current"),
                         ("06-ahead-800.png", older, True, {}, "ahead"),
                         ("07-offline-800.png", newer, True, {"exit": 6}, "error"),
                         ("08-retry-800.png", newer, True, {}, "available"),
                         ("09-invalid-800.png", newer, True, {"body": "not JSON"}, "error"),
                         ("10-no-package-800.png", newer, False, {}, "unavailable")]
                for screenshot, target, package, extras, expected in tests:
                    fixture(target, package, **extras)
                    before = len(requests())
                    click(234, 392)
                    idle()
                    assert len(requests()) == before + 1, "One click must start exactly one request"
                    capture(screenshot)
                    cases.append({"case": screenshot[:-len(".png")], "expected_state": expected, "passed": True})
                for request in requests():
                    assert request[-1] == API, request
                assert hashlib.sha256(binary.read_bytes()).hexdigest() == digest, "Update check changed the executable"
                click(45, 562)
                assert gui.wait(timeout=10) == 0
                report = {"schema_version": 1, "binary_sha256": digest, "binary_version": version_output,
                          "scope": "Actual supplied Linux X11 executable; isolated PATH curl/open fixtures and native pointer input",
                          "cases": cases, "request_count": len(requests()), "binary_unchanged": True,
                          "limits": ["No real release download or package installation", "No real browser launch",
                                     "Screenshots require visual review", "Linux native UI only"]}
                (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(report, indent=2))
        finally:
            for process in (gui, display):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)


if __name__ == "__main__":
    main()
