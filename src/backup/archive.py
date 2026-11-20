"""Local, bounded Minecraft data snapshots. Invoked by Rust with Python 3 -I.

Each published ZIP contains a complete SHA-256 manifest. Credentials stored by
the launcher and managed/runtime game roots are excluded. No network or shell.
"""
import errno
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import time
import unicodedata
import zipfile

MAX_FILES = 50000
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024 * 1024
MAX_INDEX = 8 * 1024 * 1024
MAX_DEPTH = 32
MAX_SECONDS = 300
POLICY = "mutable-game-v1"
MANIFEST = "zmcl-backup.json"
JOURNAL = ".backup-restore.json"
EXCLUDED = {
    "mods", "logs", "crash-reports", "cache", "caches", "libraries", "assets",
    "resources", "versions", "natives", "runtime", "runtimes", "accounts",
    "backups", "downloads", "bin", "launcher_profiles.json",
    "launcher_profiles_microsoft_store.json", "launcher_accounts.json",
    "accounts.json", "usercache.json", "usernamecache.json",
}
BINARY_SUFFIXES = {".jar", ".class", ".exe", ".dll", ".so", ".dylib", ".log"}
PRIVATE_NAME = re.compile(r"(?:^|[._-])(?:accounts?|tokens?|credentials?|secrets?|sessions?|auth)(?:[._-]|$)", re.I)


def fail(message):
    raise ValueError(message)


def path_parts(value, max_depth=MAX_DEPTH):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096
            or value.startswith("/") or "\\" in value or ":" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("Unsafe archive path")
    parts = value.rstrip("/").split("/")
    if len(parts) > max_depth:
        fail("Directory depth exceeds 32")
    for part in parts:
        stem = part.split(".")[0].upper()
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or stem in ("CON", "PRN", "AUX", "NUL")
                or re.fullmatch(r"(COM|LPT)[0-9]", stem)):
            fail("Unsafe portable archive path")
    return parts


def portable_key(parts):
    return "/".join(unicodedata.normalize("NFC", p).casefold() for p in parts)


def confined(root, parts):
    current = root
    if current.is_symlink():
        fail("Symbolic links are forbidden")
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail("Symbolic links are forbidden")
    return current


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("Duplicate JSON object key")
        result[key] = value
    return result


def read_json(path, limit=MAX_INDEX):
    if path.is_symlink():
        fail("Symbolic links are forbidden")
    with path.open("rb") as source:
        body = source.read(limit + 1)
    if len(body) > limit:
        fail("JSON exceeds its memory budget")
    return json.loads(body, object_pairs_hook=unique_pairs)


def identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        fail("Transaction directory is not a real directory")
    return [info.st_dev, info.st_ino]


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def selected(name):
    lower = name.casefold()
    return (not name.startswith(".") and lower not in EXCLUDED
            and Path(lower).suffix not in BINARY_SUFFIXES and not PRIVATE_NAME.search(lower))


def safe_token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        fail("Invalid backup identifier")
    return value


def fsync_dir(path):
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.count = 0
        self.total = 0

    def tick(self):
        if time.monotonic() - self.started > MAX_SECONDS:
            fail("Backup operation exceeded 300 seconds")

    def entry(self, relative, size=0):
        self.tick()
        path_parts(relative)
        self.count += 1
        self.total += size
        if self.count > MAX_FILES:
            fail("Snapshot exceeds 50,000 entries")
        if size < 0 or size > MAX_FILE:
            fail("Snapshot file exceeds 512 MiB")
        if self.total > MAX_TOTAL:
            fail("Snapshot data exceeds 4 GiB")


def check_directory_budget(source):
    size = source.stat().st_size
    with source.open("rb") as stream:
        start = max(0, size - 65557)
        stream.seek(start)
        tail = stream.read(65557)
        position = tail.rfind(b"PK\x05\x06")
        while position >= 0:
            if position + 22 <= len(tail):
                record = struct.unpack_from("<4s4H2IH", tail, position)
                if position + 22 + record[-1] == len(tail):
                    break
            position = tail.rfind(b"PK\x05\x06", 0, position)
        if position < 0:
            fail("Missing ZIP end-of-directory record")
        _, disk, directory_disk, disk_count, count, length, offset, _ = record
        if disk or directory_disk:
            fail("Multi-disk ZIP files are unsupported")
        if count == 65535 or disk_count == 65535 or length == 0xffffffff or offset == 0xffffffff:
            end = start + position
            if end < 20:
                fail("Missing ZIP64 locator")
            stream.seek(end - 20)
            locator = stream.read(20)
            if len(locator) != 20:
                fail("Truncated ZIP64 locator")
            marker, disk, record_offset, disks = struct.unpack("<4sIQI", locator)
            if marker != b"PK\x06\x07" or disk or disks != 1 or record_offset > size - 56:
                fail("Invalid ZIP64 locator")
            stream.seek(record_offset)
            header = stream.read(56)
            if len(header) != 56:
                fail("Truncated ZIP64 header")
            marker, record_length, _, _, disk, directory_disk, disk_count, count, length, offset = struct.unpack("<4sQHHIIQQQQ", header)
            if marker != b"PK\x06\x06" or record_length < 44 or disk or directory_disk:
                fail("Invalid ZIP64 directory")
        if count != disk_count or count > MAX_FILES + 1 or length > MAX_INDEX or offset + length > size:
            fail("ZIP central directory exceeds its memory budget")


def children(path):
    result = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            result.append(entry.name)
            if len(result) > MAX_FILES:
                fail("Directory contains too many entries")
    return sorted(result)


def scan_tree(root, relative, budget, seen, files, directories, stability=None):
    parts = path_parts(relative)
    key = portable_key(parts)
    if key in seen:
        fail("Duplicate normalized source path")
    seen.add(key)
    path = confined(root, parts)
    info = path.lstat()
    if stability is not None:
        stability[relative] = signature(info)
    if stat.S_ISLNK(info.st_mode):
        fail("Symbolic links are forbidden")
    if stat.S_ISDIR(info.st_mode):
        budget.entry(relative)
        directories.append(relative)
        for name in children(path):
            scan_tree(root, relative + "/" + name, budget, seen, files, directories, stability)
    elif stat.S_ISREG(info.st_mode):
        budget.entry(relative, info.st_size)
        files.append((relative, info))
    else:
        fail("Special files are forbidden")


def open_source(path, expected, check_ctime=True):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    source = os.fdopen(descriptor, "rb")
    actual = os.fstat(source.fileno())
    actual_key, expected_key = signature(actual), signature(expected)
    if not check_ctime:
        actual_key, expected_key = actual_key[:-1], expected_key[:-1]
    if not stat.S_ISREG(actual.st_mode) or actual_key != expected_key:
        source.close()
        fail("Source file changed while creating snapshot")
    return source


def zip_info(name, directory=False):
    info = zipfile.ZipInfo(name + ("/" if directory else ""))
    info.create_system = 3
    info.external_attr = ((stat.S_IFDIR | 0o700) if directory else (stat.S_IFREG | 0o600)) << 16
    info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
    info._compresslevel = 1
    return info


def validate_state(game, stability, check_ctime=True):
    for relative, expected in stability.items():
        path = game if not relative else confined(game, path_parts(relative))
        current = signature(path.lstat())
        if (current if check_ctime else current[:-1]) != (expected if check_ctime else expected[:-1]):
            fail("Game data changed during snapshot or restore")


def snapshot(game, backup_dir, backup_id, instance_id, label, created, budget=None, stability=None):
    import hashlib
    budget = budget or Budget()
    safe_token(backup_id)
    safe_token(instance_id)
    if not isinstance(label, str) or len(label) > 128 or any(ord(c) < 32 for c in label):
        fail("Backup label must contain at most 128 visible characters")
    identity(game)
    identity(backup_dir)
    target = backup_dir / (backup_id + ".zip")
    temporary = backup_dir / (".partial-" + backup_id)
    if target.exists() or target.is_symlink():
        fail("Backup already exists")
    files, directories, seen, roots = [], [], set(), []
    stability = {} if stability is None else stability
    stability[""] = signature(game.lstat())
    for name in children(game):
        path_parts(name)
        if selected(name):
            roots.append(name)
            scan_tree(game, name, budget, seen, files, directories, stability)
    metadata = {"format_version": 1, "policy": POLICY, "instance": instance_id,
                "created": created, "label": label, "roots": roots,
                "directories": directories, "files": []}
    owns_temporary = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owns_temporary = True
        with os.fdopen(descriptor, "wb") as sink:
            with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
                for relative in directories:
                    archive.writestr(zip_info("game/" + relative, True), b"")
                for relative, info in files:
                    path = confined(game, path_parts(relative))
                    digest, count = hashlib.sha256(), 0
                    with open_source(path, info) as source:
                        with archive.open(zip_info("game/" + relative), "w", force_zip64=True) as output:
                            while True:
                                budget.tick()
                                chunk = source.read(65536)
                                if not chunk:
                                    break
                                count += len(chunk)
                                if count > info.st_size:
                                    fail("Source file grew during snapshot")
                                digest.update(chunk)
                                output.write(chunk)
                        if count != info.st_size or signature(os.fstat(source.fileno())) != signature(info):
                            fail("Source file changed during snapshot")
                    metadata["files"].append({"path": relative, "size": count, "sha256": digest.hexdigest()})
                validate_state(game, stability)
                body = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > MAX_INDEX:
                    fail("Backup manifest exceeds 8 MiB")
                archive.writestr(zip_info(MANIFEST), body)
            sink.flush()
            os.fsync(sink.fileno())
        check_directory_budget(temporary)
        if temporary.stat().st_size > MAX_TOTAL + MAX_INDEX * 4:
            fail("Compressed backup exceeds the archive byte budget")
        os.link(temporary, target)
        fsync_dir(backup_dir)
        return summary(target, metadata)
    finally:
        if owns_temporary and temporary.exists():
            temporary.unlink()
        # A published, fsynced archive is never removed on a later error.


def summary(path, metadata):
    return {"id": path.stem, "bytes": path.stat().st_size, "created": metadata["created"],
            "label": metadata["label"], "files": len(metadata["files"]),
            "file_bytes": sum(item["size"] for item in metadata["files"]),
            "policy": POLICY, "roots": metadata["roots"][:16],
            "root_count": len(metadata["roots"]), "roots_truncated": len(metadata["roots"]) > 16}


def inspect_archive(source, expected_instance, budget):
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_TOTAL + MAX_INDEX * 4:
        fail("Invalid or oversized backup archive")
    check_directory_budget(source)
    archive = zipfile.ZipFile(source)
    try:
        by_name, seen, total = {}, set(), 0
        for info in archive.infolist():
            budget.tick()
            parts = path_parts(info.filename, MAX_DEPTH + 1)
            key = portable_key(parts)
            if key in seen:
                fail("Duplicate normalized archive path")
            seen.add(key)
            mode = stat.S_IFMT(info.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                fail("Archive links and special files are forbidden")
            if mode and (mode == stat.S_IFDIR) != info.is_dir():
                fail("Inconsistent archive directory type")
            if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail("Encrypted or unsupported ZIP entry")
            if info.file_size < 0 or info.file_size > MAX_FILE:
                fail("Archive member exceeds 512 MiB")
            if info.file_size > max(info.compress_size, 1) * 500:
                fail("ZIP compression ratio exceeds its safety budget")
            total += info.file_size
            if total > MAX_TOTAL + MAX_INDEX:
                fail("Uncompressed backup exceeds 4 GiB")
            by_name[info.filename] = info
        manifest = by_name.get(MANIFEST)
        if manifest is None or manifest.is_dir() or manifest.file_size > MAX_INDEX:
            fail("Backup requires a bounded SHA-256 manifest")
        with archive.open(manifest) as stream:
            body = stream.read(MAX_INDEX + 1)
        if len(body) > MAX_INDEX:
            fail("Backup manifest exceeds 8 MiB")
        metadata = json.loads(body, object_pairs_hook=unique_pairs)
        del body  # Validation now needs the parsed manifest, not a second raw copy.
        if not isinstance(metadata, dict) or metadata.get("format_version") != 1 or metadata.get("policy") != POLICY:
            fail("Unsupported backup format or data policy")
        if metadata.get("instance") != expected_instance:
            fail("Backup belongs to another instance")
        created = metadata.get("created")
        if type(created) is not int or created < 0 or created > int(time.time()) + 86400:
            fail("Invalid backup creation time")
        label = metadata.get("label")
        if not isinstance(label, str) or len(label) > 128 or any(ord(c) < 32 for c in label):
            fail("Invalid backup label")
        roots, directories, files = metadata.get("roots"), metadata.get("directories"), metadata.get("files")
        if not all(isinstance(value, list) for value in (roots, directories, files)):
            fail("Invalid backup manifest collections")
        if len(roots) + len(directories) + len(files) > MAX_FILES * 2:
            fail("Backup manifest contains too many entries")
        root_keys = set()
        for root in roots:
            parts = path_parts(root)
            key = portable_key(parts)
            if len(parts) != 1 or not selected(root) or key in root_keys:
                fail("Invalid or duplicate mutable data root")
            root_keys.add(key)
        expected = {MANIFEST}
        seen, actual_roots = set(), set()
        directory_set = set()
        file_set = set()
        for relative in directories:
            parts = path_parts(relative)
            key = portable_key(parts)
            if key in seen or parts[0] not in roots or not selected(parts[0]):
                fail("Duplicate or excluded snapshot path")
            seen.add(key)
            actual_roots.add(parts[0])
            directory_set.add(relative)
            budget.entry(relative)
            name = "game/" + relative + "/"
            item = by_name.get(name)
            if item is None or not item.is_dir() or item.file_size != 0:
                fail("Missing snapshot directory")
            expected.add(name)
        for item in files:
            if not isinstance(item, dict):
                fail("Invalid file manifest")
            relative, size, digest = item.get("path"), item.get("size"), item.get("sha256")
            parts = path_parts(relative)
            key = portable_key(parts)
            if key in seen or parts[0] not in roots or not selected(parts[0]):
                fail("Duplicate or excluded snapshot path")
            if type(size) is not int or size < 0 or size > MAX_FILE or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                fail("Invalid file size or SHA-256")
            seen.add(key)
            actual_roots.add(parts[0])
            file_set.add(relative)
            budget.entry(relative, size)
            name = "game/" + relative
            entry = by_name.get(name)
            if entry is None or entry.is_dir() or entry.file_size != size:
                fail("Snapshot file does not match its manifest")
            expected.add(name)
        if set(roots) != actual_roots or expected != set(by_name):
            fail("Unmanifested or missing backup entries")
        for relative in list(directory_set) + list(file_set):
            parts = path_parts(relative)
            for depth in range(1, len(parts)):
                parent = "/".join(parts[:depth])
                if parent not in directory_set:
                    fail("Missing parent directory in backup manifest")
        return archive, metadata, by_name
    except BaseException:
        archive.close()
        raise


def unpack(source, destination, expected_instance, budget=None):
    import hashlib
    budget = budget or Budget()
    identity(destination)
    if any(destination.iterdir()):
        fail("Backup staging directory must be empty")
    archive, metadata, by_name = inspect_archive(source, expected_instance, budget)
    with archive:
        for relative in sorted(metadata["directories"], key=lambda p: (p.count("/"), p)):
            confined(destination, path_parts(relative)).mkdir(mode=0o700)
        for item in metadata["files"]:
            path = confined(destination, path_parts(item["path"]))
            digest, count = hashlib.sha256(), 0
            with archive.open(by_name["game/" + item["path"]]) as source_stream:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    while True:
                        budget.tick()
                        chunk = source_stream.read(65536)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > item["size"]:
                            fail("Archive file exceeds its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if count != item["size"] or digest.hexdigest() != item["sha256"]:
                fail("Backup SHA-256 verification failed")
    fsync_dir(destination)
    return metadata


def guard_stopped(instance):
    record = instance / "last-launch.json"
    if not record.exists():
        if record.is_symlink():
            fail("Invalid launch record")
        return
    value = read_json(record, 65536)
    if not isinstance(value, dict):
        fail("Invalid launch record; cannot prove Minecraft is stopped")
    if value.get("exited") is True:
        return
    pid, started = value.get("pid"), value.get("started")
    if type(pid) is not int or not 0 < pid < 2**31 or type(started) is not int:
        fail("Invalid launch record; cannot prove Minecraft is stopped")
    if not sys.platform.startswith("linux"):
        fail("Cannot verify the recorded Minecraft process on this platform; stop Minecraft and verify or clear its stale last-launch record")
    if not Path("/proc/self/stat").is_file():
        fail("Process identity cannot be verified without mounted procfs")
    process = Path("/proc") / str(pid)
    if not process.exists():
        return
    try:
        fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X"):
            return
        ticks = int(fields[19])
        with (process / "cmdline").open("rb") as command:
            raw = command.read(65536)
        args = raw.split(b"\0")
        current_dir = os.path.realpath(os.readlink(process / "cwd"))
        game = os.path.realpath(instance / "game")
        matches = current_dir == game
        for index, argument in enumerate(args):
            if argument == b"--gameDir" and index + 1 < len(args):
                matches |= os.path.realpath(os.fsdecode(args[index + 1])) == game
            elif argument.startswith(b"--gameDir="):
                matches |= os.path.realpath(os.fsdecode(argument[10:])) == game
        if matches:
            fail("Minecraft is still running for this instance; stop it before backing up or restoring")
        recorded_ticks = value.get("process_start_ticks")
        if type(recorded_ticks) is int:
            if recorded_ticks != ticks:
                return
            fail("Recorded game process is still alive; refusing to change its data")
        boot = None
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                boot = int(line.split()[1])
                break
        if boot is not None:
            process_started = boot + ticks / os.sysconf("SC_CLK_TCK")
            if abs(process_started - started) > 10:
                return
        fail("Recorded process is alive but its identity is uncertain; refusing to change game data")
    except FileNotFoundError:
        if not process.exists():
            return
        raise
    except (PermissionError, IndexError, OSError) as error:
        fail("Cannot verify the recorded game process: " + str(error))


def preserve_tree(source, destination, relative, budget, seen, stability):
    files, directories = [], []
    scan_tree(source, relative, budget, seen, files, directories, stability)
    for name in directories:
        confined(destination, path_parts(name)).mkdir(mode=0o700)
    for name, info in files:
        original = confined(source, path_parts(name))
        target = confined(destination, path_parts(name))
        with open_source(original, info, check_ctime=False):
            try:
                os.link(original, target, follow_symlinks=False)
            except OSError as error:
                if error.errno not in (errno.EPERM, errno.EACCES, errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP):
                    raise
                with open_source(original, info, check_ctime=False) as stream:
                    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(info.st_mode))
                    with os.fdopen(descriptor, "wb") as output:
                        while True:
                            budget.tick()
                            chunk = stream.read(65536)
                            if not chunk:
                                break
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
            if signature(original.lstat()) != signature(info):
                # Hard-link creation changes ctime. Confirm byte/mtime identity
                # separately; the backup scope itself was independently copied.
                current = original.lstat()
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                    fail("Preserved game data changed during restore")


def write_journal(instance, data):
    path = instance / JOURNAL
    temporary = instance / (".backup-journal-" + str(time.time_ns()) + "-" + str(os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(json.dumps(data, separators=(",", ":")).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
        fsync_dir(instance)
    finally:
        temporary.unlink()


def journal_paths(instance, value):
    if not isinstance(value, dict) or value.get("format_version") != 1:
        fail("Invalid restore journal; keep all recovery directories intact")
    paths = []
    for key, prefix in (("stage", ".backup-stage-"), ("old", ".backup-old-")):
        name = value.get(key)
        if not isinstance(name, str) or not name.startswith(prefix):
            fail("Invalid restore recovery path")
        safe_token(name[len(prefix):])
        paths.append(confined(instance, [name]))
    for key in ("stage_identity", "old_identity"):
        ident = value.get(key)
        if not isinstance(ident, list) or len(ident) != 2 or any(type(v) is not int or v < 0 for v in ident):
            fail("Invalid restore directory identity")
    safe_token(value.get("pre_snapshot"))
    return paths


def check_identity(path, expected):
    if identity(path) != expected:
        fail("Restore directory identity changed; preserve all recovery data")


def remove_stage(path, expected):
    import shutil
    check_identity(path, expected)
    shutil.rmtree(path)


def recover(instance, backup_dir):
    journal = instance / JOURNAL
    if not journal.exists():
        if journal.is_symlink():
            fail("Invalid restore journal")
        return
    value = read_json(journal, 65536)
    stage, old = journal_paths(instance, value)
    game = confined(instance, ["game"])
    has_game, has_old, has_stage = game.exists(), old.exists(), stage.exists()
    if has_old:
        check_identity(old, value["old_identity"])
    if has_stage:
        check_identity(stage, value["stage_identity"])
    if has_game and has_stage and not has_old:
        check_identity(game, value["old_identity"])
        remove_stage(stage, value["stage_identity"])
    elif not has_game and has_old:
        os.rename(old, game)
        fsync_dir(instance)
        if has_stage:
            remove_stage(stage, value["stage_identity"])
    elif has_game and not has_stage:
        check_identity(game, value["stage_identity"])
        if has_old:
            pre = confined(backup_dir, [value["pre_snapshot"] + ".zip"])
            if not pre.is_file() or pre.is_symlink() or pre.stat().st_size == 0:
                fail("Pre-restore snapshot is missing; original game data has been retained")
            remove_stage(old, value["old_identity"])
    else:
        fail("Ambiguous interrupted restore; preserve journal and recovery directories")
    journal.unlink()
    fsync_dir(instance)


class SimulatedInterruption(BaseException):
    pass


def restore(instance, backup_dir, backup_id, instance_id, fail_at=None):
    import shutil
    safe_token(backup_id)
    guard_stopped(instance)
    recover(instance, backup_dir)
    game = confined(instance, ["game"])
    identity(game)
    token = str(time.time_ns()) + "-" + str(os.getpid())
    stage = instance / (".backup-stage-" + token)
    old = instance / (".backup-old-" + token)
    stage.mkdir(mode=0o700)
    journal_written = False
    committed = False
    budget = Budget()
    try:
        unpack(confined(backup_dir, [backup_id + ".zip"]), stage, instance_id, budget)
        preserve_budget, seen, preserved_state = Budget(), set(), {}
        for name in children(game):
            path_parts(name)
            if not selected(name):
                preserve_tree(game, stage, name, preserve_budget, seen, preserved_state)
        pre_id = "pre-" + token
        live_state = {}
        snapshot(game, backup_dir, pre_id, instance_id, "Before restoring " + backup_id[:100], int(time.time()), budget, live_state)
        guard_stopped(instance)
        validate_state(game, live_state)
        validate_state(game, preserved_state, check_ctime=False)
        record = {"format_version": 1, "stage": stage.name, "old": old.name,
                  "stage_identity": identity(stage), "old_identity": identity(game),
                  "pre_snapshot": pre_id}
        write_journal(instance, record)
        journal_written = True
        try:
            os.rename(game, old)
            fsync_dir(instance)
            if fail_at == "after_move_old":
                raise SimulatedInterruption()
            os.rename(stage, game)
            fsync_dir(instance)
            if fail_at == "after_move_new":
                raise SimulatedInterruption()
        except Exception:
            if old.exists():
                if game.exists():
                    check_identity(game, record["stage_identity"])
                    os.rename(game, stage)
                os.rename(old, game)
                fsync_dir(instance)
            raise
        committed = True
        recover(instance, backup_dir)
        return {"restored": backup_id, "pre_restore": pre_id}
    except Exception:
        if journal_written and not committed:
            # Recover the already-rolled-back or half-moved transaction.
            if game.exists() and stage.exists() and not old.exists():
                check_identity(game, record["old_identity"])
                remove_stage(stage, record["stage_identity"])
                (instance / JOURNAL).unlink()
                fsync_dir(instance)
            else:
                recover(instance, backup_dir)
        raise
    finally:
        if not journal_written and stage.exists():
            shutil.rmtree(stage)


def archive_summary(path, instance_id):
    # Keep the full manifest and ZIP indexes scoped to one archive. Otherwise
    # the next inspection overlaps with the previous loop iteration's objects.
    archive, metadata, _ = inspect_archive(path, instance_id, Budget())
    try:
        return summary(path, metadata)
    finally:
        archive.close()


def list_snapshots(backup_dir, instance_id):
    if not backup_dir.exists():
        return []
    identity(backup_dir)
    result, budget, encoded_bytes = [], Budget(), 2
    names = children(backup_dir)
    for name in reversed(names):
        budget.tick()
        path = backup_dir / name
        if not name.endswith(".zip"):
            continue
        if len(result) >= 10000:
            fail("Backup listing exceeds 10,000 snapshots; no data was removed")
        try:
            safe_token(path.stem)
            item = archive_summary(path, instance_id)
            item["manifest_valid"] = True
            item["data_verified"] = False
            result.append(item)
        except Exception as error:
            result.append({"id": path.stem, "manifest_valid": False,
                           "data_verified": False, "error": str(error)[:256]})
        encoded_bytes += len(json.dumps(result[-1], ensure_ascii=False).encode("utf-8")) + 1
        if encoded_bytes > MAX_INDEX:
            fail("Backup listing exceeds 8 MiB; no data was removed")
    return result


def main():
    operation = sys.argv[1]
    instance, backup_dir, instance_id = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    safe_token(instance_id)
    if operation == "list":
        result = list_snapshots(backup_dir, instance_id)
    elif operation == "create":
        guard_stopped(instance)
        recover(instance, backup_dir)
        result = snapshot(instance / "game", backup_dir, sys.argv[5], instance_id, sys.argv[6], int(sys.argv[7]))
    elif operation == "restore":
        result = restore(instance, backup_dir, sys.argv[5], instance_id)
    else:
        fail("Unknown backup operation")
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Backup: " + str(error)[:2048], file=sys.stderr)
        sys.exit(1)
