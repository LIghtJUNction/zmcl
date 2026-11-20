#!/usr/bin/env python3
"""Install an official server JAR and verify refusal while eula=false.

This acceptance never accepts the EULA or requests a live server. It uses a new
server ID, verifies the official download's SHA-1/size and checks Linux procfs
before and after the refused start. Downloaded bytes and a sanitized JSON report
remain in the selected test root. No Cargo build is performed.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path, algorithm):
    value = hashlib.new(algorithm)
    with path.open("rb") as source:
        while chunk := source.read(65536):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", default=str(REPO / "target/release/zmcl"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--id", default="server-cli-check")
    parser.add_argument("--version", default="1.21.1")
    args = parser.parse_args()
    require(platform.system() == "Linux", "This process-absence acceptance requires Linux")
    binary = Path(args.binary).resolve()
    require(binary.is_file(), "Build the binary before running this acceptance")
    case = REPO / ".test-data" / ("server-e2e-" + str(time.time_ns()))
    case.mkdir(parents=True, mode=0o700)
    root = args.root.resolve() if args.root else case / "home"
    root.mkdir(parents=True, exist_ok=True)
    server = root / "servers" / args.id
    require(not server.exists(), "Choose a new server ID; the script never overwrites one")
    report = {
        "schema": 1,
        "scope": "official server install and EULA refusal; no live server",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": platform.system() + "-" + platform.machine(),
        "binary_sha256": digest(binary, "sha256"),
        "minecraft": args.version,
        "eula_accepted": False,
        "server_started": False,
        "commands": [],
        "passed": False,
    }

    def sanitize(value):
        if isinstance(value, str):
            return value.replace(str(root), "<test-root>").replace(
                str(case), "<test-case>").replace(str(REPO), "<repo>")
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    def cli(*arguments, expect_ok=True, timeout=45):
        command = [str(binary), "--root", str(root), *arguments]
        result = subprocess.run(command, cwd=REPO, capture_output=True, text=True,
                                check=False, timeout=timeout)
        item = {"arguments": sanitize(command[1:]), "exit_code": result.returncode,
                "expected_success": expect_ok}
        if result.stderr:
            item["stderr"] = sanitize(result.stderr[:8192])
        report["commands"].append(item)
        require((result.returncode == 0) == expect_ok, "Unexpected CLI exit: " + arguments[1])
        if not expect_ok:
            require("eula=true" in result.stderr and "EULA" in result.stderr,
                    "Start refusal was not the explicit EULA guard")
            return None
        body = json.loads(result.stdout)
        if arguments[:2] == ("server", "list"):
            item["result"] = [sanitize(entry) for entry in body
                              if entry.get("server", {}).get("id") == args.id]
        else:
            item["result"] = sanitize(body)
        return body

    def matching_processes():
        matches = []
        expected_cwd = str(server.resolve())
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if os.readlink(entry / "cwd") != expected_cwd:
                    continue
                command = (entry / "cmdline").read_bytes().split(b"\0")
                executable = Path(os.readlink(entry / "exe")).name
                if executable in ("java", "java.exe") or b"server.jar" in command:
                    matches.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        return matches

    try:
        created = cli("server", "create", args.id, "--version", args.version)
        require(created["id"] == args.id and not created["installed"],
                "New server response is inconsistent")
        eula_before = (server / "eula.txt").read_bytes()
        require(b"\neula=false\n" in eula_before, "Creation did not preserve explicit EULA refusal")
        require(not matching_processes(), "A server process exists before this acceptance")
        installed = cli("server", "install", args.id, timeout=360)
        require(installed["installed"] and installed["id"] == args.id,
                "Server install was not recorded")
        metadata = json.loads((root / "shared/versions" / args.version /
                               (args.version + ".json")).read_bytes())
        expected = metadata["downloads"]["server"]
        jar = server / "server.jar"
        actual_sha1 = digest(jar, "sha1")
        require(jar.stat().st_size == expected["size"] == installed["jar_size"],
                "Server JAR size differs from official metadata")
        require(actual_sha1 == expected["sha1"] == installed["server_sha1"],
                "Server JAR SHA-1 differs from official metadata")
        require(installed["java_major"] == metadata["javaVersion"]["majorVersion"],
                "Server Java requirement differs from official metadata")
        state = cli("server", "status", args.id)
        require(state["process"]["running"] is False
                and state["process"]["state"] == "stopped",
                "Freshly installed server did not report stopped")
        listing = cli("server", "list")
        require(any(item["server"]["id"] == args.id and item["server"]["installed"]
                    for item in listing), "Installed server missing from server list")
        cli("server", "start", args.id, expect_ok=False)
        final_state = cli("server", "status", args.id)
        require(final_state["process"]["running"] is False
                and final_state["process"]["state"] == "stopped",
                "Refused server start left an active or uncertain record")
        require((server / "eula.txt").read_bytes() == eula_before,
                "Acceptance changed the EULA file")
        require(not matching_processes(), "Server Java unexpectedly started")
        report["checks"] = {
            "jar_sha1": actual_sha1, "jar_bytes": jar.stat().st_size,
            "official_download_url": expected["url"],
            "java_major": installed["java_major"],
            "official_digest_and_size_match": True,
            "status_and_list_match": True, "explicit_eula_refusal": True,
            "eula_file_unchanged": True, "matching_server_processes_before": 0,
            "matching_server_processes_after": 0,
        }
        report["passed"] = True
    except Exception as error:
        report["error"] = sanitize(type(error).__name__ + ": " + str(error))
    finally:
        report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        report["command_count"] = len(report["commands"])
        report["expected_protection_failures"] = sum(
            not item["expected_success"] for item in report["commands"])
        (case / "acceptance.json").write_text(
            json.dumps(sanitize(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(json.dumps({
            "passed": report["passed"],
            "report": str((case / "acceptance.json").relative_to(REPO)),
            "binary_sha256": report["binary_sha256"],
            "command_count": report["command_count"],
            "expected_protection_failures": report["expected_protection_failures"],
            "checks": report.get("checks"),
            **({"error": report["error"]} if "error" in report else {}),
        }, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
