#!/usr/bin/env python3
"""Exercise real CLI planning with a trusted local Mojang profile containing spaces.

Usage: python3 scripts/test_version_names_cli.py ZMCL_BINARY VERSION_JSON
Only a temporary home is changed. Installed metadata is synthetic; no game JAR,
network download, account access or game launch is needed. The owned POSIX Java
fixture accepts only -version and records any unexpected invocation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


class CheckFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cli(binary, root, arguments, phase, report):
    report["stage"] = phase
    try:
        result = subprocess.run(
            [str(binary), "--root", str(root), *arguments],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise CheckFailure(phase + " CLI exceeded 20 seconds") from None
    report[phase + "_exit_code"] = result.returncode
    require(result.returncode == 0, phase + " CLI returned a nonzero exit code")
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise CheckFailure(phase + " CLI did not return valid JSON") from None
    require(isinstance(value, dict), phase + " CLI did not return a JSON object")
    return value


def check(binary, profile_path, report):
    require(os.name == "posix", "The owned Java fixture requires a POSIX host")
    binary = binary.resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK), "Binary is not executable")
    report["binary_sha256"] = file_digest(binary)
    with profile_path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    require(len(data) <= 16 * 1024 * 1024, "Profile exceeds the manifest size limit")
    manifest = json.loads(data)
    require(isinstance(manifest, dict), "Profile must be a JSON object")
    version = manifest.get("id")
    require(isinstance(version, str) and " " in version,
            "Profile ID must contain spaces to exercise this regression")
    require(re.fullmatch(r"[A-Za-z0-9._+ -]{1,128}", version) is not None
            and not version.startswith(" ") and not version.endswith((" ", ".")),
            "Profile ID is not a safe single path component")
    require(not manifest.get("inheritsFrom"), "Use a standalone Mojang profile")
    require(not manifest.get("jar") or manifest["jar"] == version,
            "Use a profile whose client JAR has the same version ID")
    major = manifest.get("javaVersion", {}).get("majorVersion", 8)
    require(type(major) is int and 8 <= major <= 255, "Invalid profile Java major")
    report.update(version_id=version, profile_sha1=hashlib.sha1(data).hexdigest(),
                  java_major=major, platform=sys.platform)

    with tempfile.TemporaryDirectory(prefix="zmcl-version-name-cli-") as temporary:
        temporary = Path(temporary).resolve()
        root = temporary / "launcher home with spaces"
        probe_log = temporary / "java-invocations.txt"
        java = temporary / "owned fake java"
        java_version = "1.8.0" if major == 8 else str(major) + ".0.0"
        java.write_text(
            "#!/bin/sh\n"
            'if [ "$#" -eq 1 ] && [ "$1" = "-version" ]; then\n'
            "  printf '%s\\n' version >> " + shlex.quote(str(probe_log)) + "\n"
            "  printf '%s\\n' " + shlex.quote('openjdk version "' + java_version + '"') + " >&2\n"
            "  exit 0\nfi\n"
            "printf '%s\\n' unexpected >> " + shlex.quote(str(probe_log)) + "\n"
            "exit 97\n", encoding="utf-8",
        )
        java.chmod(0o700)
        created = cli(binary, root, ["instance", "create", "preview", "--version", version],
                      "create", report)
        require(created.get("id") == "preview" and created.get("version") == version,
                "Created instance did not preserve the complete version ID")
        require(created.get("installed") is False and created.get("loader") == "vanilla",
                "Creation unexpectedly installed or changed the instance loader")
        instance_dir = root / "instances" / "preview"
        metadata_path = instance_dir / "instance.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        require(metadata == created, "CLI output differs from persisted instance metadata")
        metadata.update(installed=True, java=str(java))
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        version_dir = root / "shared" / "versions" / version
        version_dir.mkdir(parents=True)
        cached_profile = version_dir / (version + ".json")
        cached_profile.write_bytes(data)
        expected_jar = str(version_dir / (version + ".jar"))
        plan = cli(binary, root, ["launch", "preview", "--offline", "Fixture", "--dry-run"],
                   "launch", report)
        report["stage"] = "assertions"
        argv = plan.get("args")
        require(isinstance(argv, list) and all(isinstance(arg, str) for arg in argv),
                "Launch plan did not contain a string argv vector")
        require(plan.get("offline") is True and plan.get("java_major") == major,
                "Launch plan did not use the requested offline profile and Java major")
        require(plan.get("java") == str(java), "Launch plan ignored the owned Java fixture")
        require(plan.get("working_directory") == str(instance_dir / "game"),
                "Launch plan escaped the temporary instance")
        version_flags = [i for i, arg in enumerate(argv) if arg == "--version"]
        require(len(version_flags) == 1 and version_flags[0] + 1 < len(argv)
                and argv[version_flags[0] + 1] == version,
                "The --version argument lost or split the official version ID")
        classpath_flags = [i for i, arg in enumerate(argv)
                           if arg in ("-cp", "-classpath", "--class-path")]
        require(len(classpath_flags) == 1 and classpath_flags[0] + 1 < len(argv),
                "Launch plan did not contain one complete classpath argument")
        classpath = argv[classpath_flags[0] + 1].split(os.pathsep)
        require(classpath.count(expected_jar) == 1,
                "Client JAR with spaces is missing from the single classpath argument")
        invocations = probe_log.read_text(encoding="utf-8").splitlines() if probe_log.exists() else []
        require(invocations == ["version"], "Java received an unexpected game launch invocation")
        require(not (instance_dir / "last-launch.json").exists()
                and not (instance_dir / "logs").exists(), "Dry-run created game launch records")
        require(not Path(expected_jar).exists(), "Dry-run unexpectedly materialized a game JAR")
        require(cached_profile.read_bytes() == data, "Dry-run modified the supplied profile")
        report.update(ok=True, stage="complete", classpath_entries=len(classpath),
                      java_version_probes=len(invocations), java_game_invocations=0,
                      installed_metadata="synthetic", game_downloaded=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("profile", type=Path, help="Trusted local standalone Mojang JSON profile")
    arguments = parser.parse_args()
    report = {"ok": False, "stage": "inputs"}
    try:
        check(arguments.binary, arguments.profile, report)
    except CheckFailure as error:
        report["error"] = str(error)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        # Never print raw exception messages, subprocess output or private paths.
        report["error"] = "Input or fixture failure: " + type(error).__name__
    print(json.dumps(report, ensure_ascii=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
