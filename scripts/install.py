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
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "bin/zmcl",
    "share/icons/hicolor/scalable/apps/zmcl.svg",
    "share/applications/zmcl.desktop",
)
MANIFEST = "share/zmcl/install.json"


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
    if not isinstance(entries, list) or not 1 <= len(entries) <= len(FILES):
        raise ValueError("Invalid installation manifest")
    validated = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "sha256"}
            or not isinstance(entry["path"], str)
            or entry["path"] not in FILES
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
    resources = (
        (FILES[0], source.read_bytes(), 0o755),
        (FILES[1], (ROOT / "assets/zmcl.svg").read_bytes(), 0o644),
        (FILES[2], desktop.encode("utf-8"), 0o644),
    )
    with installation_lock(prefix):
        record = prefix / MANIFEST
        previous = read_manifest(record)
        for relative, _data, _mode in resources:
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
        changes.append((record, json.dumps(entries, indent=2).encode("utf-8"), 0o600))
        replace_files(changes)
    print(executable)


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
    default_binary = ROOT / binary_name
    if not default_binary.is_file():
        default_binary = ROOT / "target/release" / binary_name
    parser.add_argument("--binary", type=Path, default=default_binary)
    args = parser.parse_args(argv)
    if args.action == "install":
        install(args.prefix, args.binary)
    else:
        uninstall(args.prefix)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("zmcl installer:", str(error), file=sys.stderr)
        sys.exit(1)
