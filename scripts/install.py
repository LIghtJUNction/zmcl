#!/usr/bin/env python3
"""Per-user installation with checked upgrades and rollback on write errors."""

import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "zmcl.exe" if os.name == "nt" else "zmcl"
FILES = (
    "bin/" + EXECUTABLE_NAME,
    "share/icons/hicolor/scalable/apps/zmcl.svg",
    "share/applications/zmcl.desktop",
)
MANIFEST = "share/zmcl/install.json"

DISTRIBUTION = "share/zmcl/distribution.json"
INSTALLATION = "share/zmcl/installation.json"
MANAGED_FILES = FILES + (DISTRIBUTION, INSTALLATION)
if os.name == "nt":
    # Older Windows installers used an extensionless destination.
    MANAGED_FILES += ("bin/zmcl",)
RELEASES_URL = "https://github.com/LIghtJUNction/zmcl/releases"
VERSION_PATTERN = r"[0-9][A-Za-z0-9.+-]{0,63}"


def metadata_json(path, limit=262144):
    info = checked_path(path)
    if info is None:
        return None
    if info.st_size > limit:
        raise ValueError("Release metadata is too large: " + path.name)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid release metadata: " + path.name)
    return value


def release_metadata(source_data, icon_data):
    """Check packaged bytes without executing the launcher or trusting its name."""
    release = metadata_json(ROOT / "release.json")
    marker_path = ROOT / DISTRIBUTION
    marker_info = checked_path(marker_path)
    if marker_info is not None and marker_info.st_size > 16384:
        raise ValueError("Distribution metadata is too large")
    unknown = {"version": "unknown", "release_url": None}
    if release is None:
        if marker_info is not None:
            raise ValueError("Distribution metadata requires a checked release.json")
        return unknown, None
    if type(release.get("schema")) is not int or release["schema"] != 1 or release.get("application") != "zmcl":
        raise ValueError("Invalid zmcl release metadata")
    # Source trees and early archives may lack a file manifest. They remain
    # installable, but a directory name or an unbound version is not evidence.
    if "files" not in release or "binary" not in release:
        if marker_info is not None:
            raise ValueError("Distribution metadata is not covered by the release manifest")
        return unknown, None
    entries = release["files"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise ValueError("Invalid release file manifest")
    files = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError("Invalid release file entry")
        name = entry["path"]
        if (
            not isinstance(name, str) or not name or len(name) > 512
            or "\\" in name or ":" in name or any(c in name for c in "\r\n\0")
            or any(part in ("", ".", "..") for part in name.split("/"))
            or name in files or name == "release.json"
            or type(entry["bytes"]) is not int or entry["bytes"] < 0
            or not isinstance(entry["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
        ):
            raise ValueError("Invalid release file entry")
        path = ROOT / name
        info = checked_path(path)
        if info is None or info.st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("Release file checksum mismatch: " + name)
        files[name] = entry
    binary = release["binary"]
    if (
        not isinstance(binary, dict) or binary.get("path") not in ("zmcl", "zmcl.exe")
        or binary != files.get(binary.get("path"))
        or binary["bytes"] != len(source_data)
        or binary["sha256"] != hashlib.sha256(source_data).hexdigest()
    ):
        raise ValueError("Selected binary does not match the release manifest")
    icon = files.get("assets/zmcl.svg")
    if icon is None or icon["bytes"] != len(icon_data) or icon["sha256"] != hashlib.sha256(icon_data).hexdigest():
        raise ValueError("Installed icon does not match the release manifest")
    version = release.get("version")
    if not isinstance(version, str) or re.fullmatch(VERSION_PATTERN, version) is None:
        if marker_info is not None:
            raise ValueError("Distribution metadata has no valid release version")
        return unknown, None
    release_url = RELEASES_URL + "/tag/v" + version
    metadata = {"version": version, "release_url": release_url}
    if marker_info is None:
        return metadata, None
    if DISTRIBUTION not in files:
        raise ValueError("Distribution metadata is not covered by the release manifest")
    marker_data = marker_path.read_bytes()
    if hashlib.sha256(marker_data).hexdigest() != files[DISTRIBUTION]["sha256"]:
        raise ValueError("Distribution metadata changed during validation")
    marker = json.loads(marker_data.decode("utf-8"))
    if (
        not isinstance(marker, dict) or type(marker.get("schema")) is not int or marker["schema"] != 1
        or marker.get("application") != "zmcl" or marker.get("version") != version
        or marker.get("format") != "portable" or marker.get("packager") != "zdistro"
        or not isinstance(marker.get("packager_version"), str)
        or re.fullmatch(VERSION_PATTERN, marker["packager_version"]) is None
        or not isinstance(marker.get("packager_commit"), str)
        or re.fullmatch(r"[0-9a-f]{40}", marker["packager_commit"]) is None
        or marker.get("release_url") != release_url
    ):
        raise ValueError("Invalid portable distribution metadata")
    return metadata, marker_data


def display_command(arguments):
    """Format a command to copy; never invoke a shell or alter the user's PATH."""
    if os.name == "nt":
        return "& " + " ".join("'" + str(value).replace("'", "''") + "'" for value in arguments)
    return " ".join(shlex.quote(str(value)) for value in arguments)


def installation_summary(prefix, executable, metadata):
    print("Installed zmcl " + metadata["version"])
    print("Installation: " + str(prefix))
    print("Executable: " + str(executable))
    print("Launch: " + display_command([executable, "gui"]))
    selected = shutil.which("zmcl")
    if selected:
        selected = Path(os.path.abspath(selected))
        try:
            same = os.path.samefile(selected, executable)
        except OSError:
            same = os.path.normcase(str(selected)) == os.path.normcase(str(executable))
        if same:
            print("PATH selects this installation. You can also run: zmcl gui")
        else:
            print("PATH selects another zmcl: " + str(selected))
            print("This installation: " + str(executable))
            print("Use the Launch command above to start this installation.")
    else:
        print("PATH does not contain zmcl. Use the Launch command above.")
    if metadata["release_url"]:
        print("Release: " + metadata["release_url"])
    print("Update: Download a newer release from " + RELEASES_URL)
    print("From its extracted directory, run the installer with this same prefix:")
    print("  " + display_command([sys.executable, "scripts/install.py", "install", "--prefix", prefix]))
    print("Installation information: " + str(prefix / INSTALLATION))


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def atomic_bytes(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".zmcl-install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checked_path(path, directory=False):
    """Check existing components before following any installation path."""
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Refusing a non-directory or symbolic-link parent: " + str(parent))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise ValueError("Refusing a non-regular installation destination: " + str(path))
    return info


def installation_prefix(path):
    prefix = Path(os.path.abspath(Path(path).expanduser()))
    if any(character in str(prefix) for character in "\r\n\0"):
        raise ValueError("Invalid installation path")
    checked_path(prefix, directory=True)
    return prefix


@contextmanager
def installation_lock(prefix):
    """Serialize cooperating installers; the OS releases the lock on exit."""
    prefix.mkdir(parents=True, exist_ok=True)
    checked_path(prefix, directory=True)
    if os.name == "nt":
        import msvcrt

        lock = prefix / ".zmcl-install.lock"
        checked_path(lock)
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except BaseException:
            os.close(descriptor)
            raise
    else:
        import fcntl

        descriptor = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeError("Another zmcl installer is using this prefix") from error
            raise
    try:
        yield
    finally:
        os.close(descriptor)


def read_manifest(record):
    info = checked_path(record)
    if info is None:
        return None
    if info.st_size > 16384:
        raise ValueError("Invalid installation manifest")
    entries = json.loads(record.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(MANAGED_FILES):
        raise ValueError("Invalid installation manifest")
    validated = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry["path"], str)
            or entry["path"] not in MANAGED_FILES
            or entry["path"] in validated
            or not isinstance(entry["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
        ):
            raise ValueError("Invalid installation manifest")
        validated[entry["path"]] = entry["sha256"]
    return validated


def replace_files(changes):
    """Back up every old file before publishing; restore with rename on error."""
    backups = {}
    started = []
    preserved = set()
    try:
        for path, _data, _mode in changes:
            info = checked_path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if info is None:
                backups[path] = None
                continue
            descriptor, name = tempfile.mkstemp(prefix=".zmcl-backup-" + path.name + "-", dir=path.parent)
            backup = Path(name)
            backups[path] = backup
            with os.fdopen(descriptor, "wb") as output, path.open("rb") as source:
                for block in iter(lambda: source.read(65536), b""):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            backup.chmod(stat.S_IMODE(info.st_mode))
        try:
            for path, data, mode in changes:
                checked_path(path)
                started.append(path)
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_bytes(path, data, mode)
        except BaseException as error:
            failed = []
            # Keep every unfinished restore recoverable, even if rollback is interrupted.
            preserved.update(backups[path] for path in started if backups[path] is not None)
            for path in reversed(started):
                backup = backups[path]
                try:
                    if backup is None:
                        path.unlink(missing_ok=True)
                    else:
                        os.replace(backup, path)
                except BaseException:
                    failed.append(str(path))
                else:
                    preserved.discard(backup)
            if failed:
                raise RuntimeError(
                    "Installation failed; rollback needs attention for "
                    + ", ".join(failed)
                    + ". Existing .zmcl-backup-* files were preserved."
                ) from error
            raise
    finally:
        for backup in backups.values():
            if backup is not None and backup not in preserved:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    print("zmcl installer: unused backup retained: " + str(backup), file=sys.stderr)


def install(prefix, source):
    prefix = installation_prefix(prefix)
    source = Path(os.path.abspath(Path(source).expanduser()))
    if checked_path(source) is None:
        raise ValueError("Binary must be an existing regular file")
    executable = prefix / FILES[0]
    quoted = str(executable).replace("\\", "\\\\").replace('"', '\\"')
    quoted = quoted.replace("$", "\\$").replace("`", "\\`").replace("%", "%%")
    desktop = (
        "[Desktop Entry]\nType=Application\nName=zmcl\n"
        "Comment=Minecraft, with less overhead\nExec=\"" + quoted
        + "\" gui\nIcon=zmcl\nTerminal=false\nCategories=Game;\nStartupWMClass=zmcl\n"
    )
    source_data = source.read_bytes()
    icon_data = (ROOT / "assets/zmcl.svg").read_bytes()
    metadata, marker = release_metadata(source_data, icon_data)
    receipt = {
        "schema": 1, "application": "zmcl", "version": metadata["version"],
        "channel": "portable", "executable": str(executable), "prefix": str(prefix),
        "release_url": metadata["release_url"],
    }
    resources = [
        (FILES[0], source_data, 0o755),
        (FILES[1], icon_data, 0o644),
        (FILES[2], desktop.encode("utf-8"), 0o644),
        (INSTALLATION, (json.dumps(receipt, indent=2) + "\n").encode("utf-8"), 0o644),
    ]
    if marker is not None:
        resources.append((DISTRIBUTION, marker, 0o644))
    with installation_lock(prefix):
        record = prefix / MANIFEST
        previous = read_manifest(record)
        # Preflight old sidecars too, including an old marker omitted by an
        # earlier archive. Never overwrite, remove, or adopt an unmanaged leaf.
        for relative in MANAGED_FILES:
            path = prefix / relative
            if checked_path(path) is not None:
                if previous is None or relative not in previous:
                    raise RuntimeError("Unmanaged files preserved: " + relative)
                if digest(path) != previous[relative]:
                    raise RuntimeError("Modified files preserved: " + relative)
        entries = [
            {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
            for relative, data, _mode in resources
        ]
        changes = [(prefix / relative, data, mode) for relative, data, mode in resources]
        current = {relative for relative, _data, _mode in resources}
        for relative in previous or {}:
            if relative not in current:
                changes.append((prefix / relative, None, 0))
        changes.append((record, json.dumps(entries, indent=2).encode("utf-8"), 0o600))
        replace_files(changes)
    installation_summary(prefix, executable, metadata)


def uninstall(prefix):
    prefix = installation_prefix(prefix)
    if not prefix.exists():
        print("zmcl is not installed at this prefix")
        return
    with installation_lock(prefix):
        record = prefix / MANIFEST
        entries = read_manifest(record)
        if entries is None:
            print("zmcl is not installed at this prefix")
            return
        retained = []
        changes = []
        for relative, expected in entries.items():
            path = prefix / relative
            # A modified leaf is retained; unsafe parents stop the whole preflight.
            checked_path(path.parent, directory=True)
            if path.is_symlink():
                retained.append(relative)
            elif checked_path(path) is not None:
                if digest(path) != expected:
                    retained.append(relative)
                else:
                    changes.append((path, None, 0))
        if not retained:
            changes.append((record, None, 0))
        replace_files(changes)
        if retained:
            raise RuntimeError("Modified files preserved: " + ", ".join(retained))
    print("Removed zmcl installation. Minecraft instances, saves and credentials are preserved.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    binary_name = "zmcl.exe" if os.name == "nt" else "zmcl"
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args(argv)
    if args.action == "install":
        binary = args.binary
        if binary is None:
            binary = ROOT / binary_name
            if not binary.is_file():
                metadata = json.loads(subprocess.check_output(
                    ["cargo", "metadata", "--no-deps", "--format-version", "1", "--locked", "--offline"],
                    cwd=ROOT, text=True))
                binary = Path(metadata["target_directory"]) / "release" / binary_name
        install(args.prefix, binary)
    else:
        uninstall(args.prefix)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("zmcl installer:", str(error), file=sys.stderr)
        sys.exit(1)
