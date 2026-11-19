"""Bounded, on-demand Java save management. No network, shell, chunk editing,
or NBT rewriting. Invoked by Rust under the instance operation lock.

NBT reference: Voxelum/minecraft-launcher-core-node packages/game-data/level.ts.
World roots contain gzip-compressed Java NBT level.dat with a Data compound.
"""
import ctypes
import gzip
import hashlib
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

MAX_WORLDS = 2000
MAX_ENTRIES = 50000
MAX_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024 * 1024
MAX_CENTRAL = 16 * 1024 * 1024
MAX_NBT_COMPRESSED = 8 * 1024 * 1024
MAX_NBT_RAW = 16 * 1024 * 1024
MAX_NBT_TAGS = 250000
MAX_SECONDS = 300
MAX_DEPTH = 32
CHUNK = 64 * 1024


def fail(message):
    raise ValueError(message)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("Duplicate JSON object key")
        result[key] = value
    return result


def parts_for(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096
            or value.startswith("/") or "\\" in value or ":" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("Unsafe world file path")
    parts = (value[:-1] if value.endswith("/") else value).split("/")
    if len(parts) > MAX_DEPTH:
        fail("World directory depth exceeds 32")
    for part in parts:
        stem = part.split(".")[0].upper()
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or stem in ("CON", "PRN", "AUX", "NUL")
                or re.fullmatch(r"(COM|LPT)[0-9]", stem)):
            fail("Unsafe portable world file path")
    return tuple(parts)


def world_name(value):
    parts = parts_for(value)
    if len(parts) != 1 or value.startswith(".") or len(value.encode("utf-8")) > 240:
        fail("World folder name must be a visible single path component of at most 240 UTF-8 bytes")
    return value


def portable(parts):
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def real_dir(path):
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
        fail("Expected a real directory, not a symbolic link")
    return path.resolve(strict=True)


def confined(root, parts):
    current = root
    if current.is_symlink():
        fail("Symbolic links are forbidden in world paths")
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail("Symbolic links are forbidden in world paths")
    return current


def open_regular(path, expected=None, writable=False, max_size=None):
    flags = (os.O_RDWR if writable else os.O_RDONLY)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        fail("Symbolic links are forbidden")
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            fail("World data must be a regular file")
        if max_size is not None and info.st_size > max_size:
            fail("File exceeds its size budget")
        if expected is not None and signature(info) != signature(expected):
            fail("World data changed during the operation")
        stream = os.fdopen(descriptor, "r+b" if writable else "rb")
    except BaseException:
        os.close(descriptor)
        raise
    return stream


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.count = 0
        self.total = 0

    def tick(self):
        if time.monotonic() - self.started > MAX_SECONDS:
            fail("World operation exceeded 300 seconds")

    def add(self, size=0):
        self.tick()
        self.count += 1
        self.total += size
        if self.count > MAX_ENTRIES:
            fail("World exceeds 50,000 entries")
        if size < 0 or size > MAX_FILE:
            fail("World file exceeds 1 GiB")
        if self.total > MAX_TOTAL:
            fail("World data exceeds 16 GiB")


class Nbt:
    """Streaming parser retaining only selected metadata, never arrays/chunks."""
    KEYS = {
        ("Data", "LevelName"): (8, "name"),
        ("Data", "LastPlayed"): (4, "last_played_ms"),
        ("Data", "GameType"): (3, "game_type"),
        ("Data", "hardcore"): (1, "hardcore"),
        ("Data", "Difficulty"): (1, "difficulty"),
        ("Data", "DataVersion"): (3, "data_version"),
        ("Data", "Version", "Name"): (8, "version"),
        ("Data", "Version", "Id"): (3, "version_id"),
        ("Data", "Version", "Snapshot"): (1, "snapshot"),
    }

    def __init__(self, stream):
        self.stream = stream
        self.bytes = 0
        self.tags = 0
        self.result = {}
        self.has_data = False

    def read(self, count):
        if count < 0 or self.bytes + count > MAX_NBT_RAW:
            fail("level.dat exceeds the 16 MiB decompressed budget")
        data = self.stream.read(count)
        self.bytes += len(data)
        if len(data) != count:
            fail("Truncated level.dat NBT")
        return data

    def skip(self, count):
        if count < 0 or self.bytes + count > MAX_NBT_RAW:
            fail("level.dat array exceeds its memory budget")
        while count:
            take = min(CHUNK, count)
            self.read(take)
            count -= take

    def string(self):
        size = struct.unpack(">H", self.read(2))[0]
        raw = self.read(size)
        # Java DataInput modified UTF-8 uses C0 80 for NUL and CESU-8 pairs.
        value = raw.replace(b"\xc0\x80", b"\0").decode("utf-8", "surrogatepass")
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le")

    def payload(self, kind, depth, path):
        self.tags += 1
        if self.tags > MAX_NBT_TAGS or depth > 64:
            fail("level.dat NBT structure exceeds its budget")
        selected = self.KEYS.get(path)
        if selected and kind != selected[0]:
            fail("level.dat metadata has an unexpected NBT type")
        scalar = None
        sizes = {1: (1, ">b"), 2: (2, ">h"), 3: (4, ">i"), 4: (8, ">q"),
                 5: (4, ">f"), 6: (8, ">d")}
        if kind in sizes:
            size, fmt = sizes[kind]
            scalar = struct.unpack(fmt, self.read(size))[0]
        elif kind == 8:
            scalar = self.string()
        elif kind in (7, 11, 12):
            length = struct.unpack(">i", self.read(4))[0]
            if length < 0:
                fail("Negative NBT array length")
            self.skip(length * {7: 1, 11: 4, 12: 8}[kind])
        elif kind == 9:
            member_kind = self.read(1)[0]
            count = struct.unpack(">i", self.read(4))[0]
            if count < 0 or count > MAX_NBT_TAGS or member_kind > 12:
                fail("Invalid NBT list length or type")
            if member_kind == 0 and count:
                fail("Nonempty NBT list cannot use TAG_End")
            for _ in range(count):
                self.payload(member_kind, depth + 1, path + ("[]",))
        elif kind == 10:
            if path == ("Data",):
                self.has_data = True
            keys = set()
            while True:
                member_kind = self.read(1)[0]
                if member_kind == 0:
                    break
                if member_kind > 12:
                    fail("Unknown NBT tag")
                key = self.string()
                if key in keys:
                    fail("Duplicate NBT compound key")
                keys.add(key)
                self.payload(member_kind, depth + 1, path + (key,))
        else:
            fail("Unknown NBT tag")
        if selected:
            if isinstance(scalar, str) and len(scalar.encode("utf-8")) > 4096:
                fail("World metadata string exceeds 4096 bytes")
            self.result[selected[1]] = bool(scalar) if selected[1] in ("hardcore", "snapshot") else scalar

    def parse(self):
        if self.read(1) != b"\x0a":
            fail("level.dat must have a Java NBT compound root")
        self.string()  # Java root name, normally empty.
        self.payload(10, 0, ())
        if self.stream.read(1):
            fail("Trailing NBT data in level.dat")
        if not self.has_data or not self.result:
            fail("level.dat lacks recognizable Java world metadata")
        return self.result


def metadata(root):
    root = real_dir(root)
    path = confined(root, ("level.dat",))
    before = path.lstat()
    if before.st_nlink != 1:
        fail("Hard-linked world data is not supported")
    with open_regular(path, before, max_size=MAX_NBT_COMPRESSED) as source:
        if source.read(2) != b"\x1f\x8b":
            fail("level.dat is not a gzip-compressed Java world")
        source.seek(0)
        with gzip.GzipFile(fileobj=source) as decoded:
            result = Nbt(decoded).parse()
        if signature(os.fstat(source.fileno())) != signature(before):
            fail("level.dat changed while reading metadata")
    if signature(path.lstat()) != signature(before):
        fail("level.dat changed while reading metadata")
    result.setdefault("name", root.name)
    return dict(id=root.name, path=str(root), format="java", valid=True,
                metadata_file="level.dat", **result)


def world_path(instance, name):
    instance = real_dir(instance)
    return confined(instance, ("game", "saves", world_name(name)))


def save_root(instance, exists=True):
    path = confined(real_dir(instance), ("game", "saves"))
    if exists:
        return real_dir(path)
    return path


def scan_tree(root, budget):
    root = real_dir(root)
    result = []
    stability = [(root, root.lstat())]
    seen = set()

    def walk(directory, parents):
        with os.scandir(directory) as entries:
            for entry in entries:
                budget.tick()
                relative = parents + (entry.name,)
                parts_for("/".join(relative))
                if relative == ("session.lock",):
                    continue
                key = portable(relative)
                if key in seen:
                    fail("World paths collide under portable Unicode/case rules")
                seen.add(key)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    fail("World contains a symbolic link")
                path = directory / entry.name
                if stat.S_ISDIR(info.st_mode):
                    budget.add()
                    result.append((relative, info, True))
                    stability.append((path, info))
                    walk(path, relative)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        fail("World contains a hard-linked file")
                    budget.add(info.st_size)
                    result.append((relative, info, False))
                    stability.append((path, info))
                else:
                    fail("World contains a non-regular file")
    walk(root, ())
    return result, stability


def stable(stability):
    for path, before in stability:
        if signature(path.lstat()) != signature(before) or path.is_symlink():
            fail("World changed while being copied; no result was published")


class SessionLock:
    """Holds the Java FileChannel lock on existing worlds while copying.
    A missing session.lock is created only for an explicitly selected source.
    """
    def __init__(self, root, create):
        self.path = confined(root, ("session.lock",))
        self.create = create
        self.stream = None

    def __enter__(self):
        if not self.path.exists() and not self.path.is_symlink() and not self.create:
            return self
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if self.create:
            flags |= os.O_CREAT
        descriptor = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                fail("Invalid Minecraft session.lock")
            if self.path.is_symlink():
                fail("Symbolic session.lock is forbidden")
            self.stream = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
        try:
            if os.name == "posix":
                import fcntl
                fcntl.lockf(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 0)
            elif os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fail("Minecraft session locking is unavailable on this platform")
        except BaseException:
            self.stream.close()
            self.stream = None
            fail("World is in use or its session lock cannot be verified; stop Minecraft first")
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()
        return False


def guard_process(instance):
    record = instance / "last-launch.json"
    if not record.exists() and not record.is_symlink():
        return
    with open_regular(record, max_size=65536) as stream:
        value = json.loads(stream.read(65537), object_pairs_hook=unique_pairs)
    if not isinstance(value, dict):
        fail("Invalid launch record; cannot prove Minecraft is stopped")
    if value.get("exited") is True:
        return
    pid, started = value.get("pid"), value.get("started")
    if type(pid) is not int or not 0 < pid < 2**31 or type(started) is not int:
        fail("Invalid launch record; cannot prove Minecraft is stopped")
    if not sys.platform.startswith("linux"):
        fail("Cannot verify the recorded Minecraft process on this platform; its launch record must confirm exit")
    if not Path("/proc/self/stat").is_file():
        fail("Process identity cannot be verified without mounted procfs")
    process = Path("/proc") / str(pid)
    if not process.exists():
        return
    try:
        with (process / "stat").open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            fail("Process identity exceeds its budget")
        fields = raw.decode().rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X"):
            return
        ticks = int(fields[19])
        with (process / "cmdline").open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            fail("Process command line exceeds its identity budget")
        args = raw.split(b"\0")
        current_dir = os.path.realpath(os.readlink(process / "cwd"))
        game = os.path.realpath(instance / "game")
        matches = current_dir == game
        for index, arg in enumerate(args):
            if arg == b"--gameDir" and index + 1 < len(args):
                matches |= os.path.realpath(os.fsdecode(args[index + 1])) == game
            elif arg.startswith(b"--gameDir="):
                matches |= os.path.realpath(os.fsdecode(arg[10:])) == game
        if matches:
            fail("Minecraft is still running for this instance; stop it before changing world data")
        recorded_ticks = value.get("process_start_ticks")
        if type(recorded_ticks) is int:
            if recorded_ticks != ticks:
                return
            fail("Recorded game process is still alive")
        boot = None
        with Path("/proc/stat").open() as stream:
            for line in stream:
                if line.startswith("btime "):
                    boot = int(line.split()[1])
                    break
        if boot is not None and abs(boot + ticks / os.sysconf("SC_CLK_TCK") - started) > 10:
            return
        fail("Recorded process is alive but its identity is uncertain; refusing to change game data")
    except FileNotFoundError:
        if not process.exists():
            return
        raise
    except (PermissionError, IndexError, OSError) as error:
        fail("Cannot verify the recorded game process: " + str(error))


def world_directories(instance):
    root = save_root(instance, exists=False)
    if not root.exists():
        return []
    root = real_dir(root)
    result = []
    count = 0
    with os.scandir(root) as entries:
        for entry in entries:
            count += 1
            if count > MAX_WORLDS:
                fail("Instance saves directory exceeds 2,000 entries")
            if entry.name.startswith("."):
                continue
            world_name(entry.name)
            if entry.is_symlink():
                fail("A world directory is a symbolic link")
            if entry.is_dir(follow_symlinks=False):
                result.append(root / entry.name)
    return sorted(result, key=lambda path: path.name.casefold())


def guard_stopped(instance):
    instance = real_dir(instance)
    guard_process(instance)
    for root in world_directories(instance):
        with SessionLock(root, create=False):
            pass


def list_worlds(instance):
    result = []
    budget = Budget()
    for root in world_directories(instance):
        budget.tick()
        level = root / "level.dat"
        if not level.exists() and not level.is_symlink():
            continue
        try:
            result.append(metadata(root))
        except (ValueError, OSError, EOFError, UnicodeError, struct.error) as error:
            result.append(dict(id=root.name, path=str(root), format="java",
                               valid=False, error=str(error)[:512]))
    return result


def info(instance, name):
    root = real_dir(world_path(instance, name))
    try:
        result = metadata(root)
    except (ValueError, OSError, EOFError, UnicodeError, struct.error) as error:
        result = dict(id=name, path=str(root), format="java", valid=False, error=str(error)[:512])
    budget = Budget()
    files, stability = scan_tree(root, budget)
    stable(stability)
    result["file_count"] = sum(not directory for _, _, directory in files)
    result["bytes"] = budget.total
    return result


def check_zip_directory(stream):
    """Bound the central directory before ZipFile allocates its entry table."""
    size = os.fstat(stream.fileno()).st_size
    if size > MAX_TOTAL or size < 22:
        fail("ZIP is empty, truncated, or exceeds 16 GiB")
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    position = tail.rfind(b"PK\x05\x06")
    while position >= 0:
        if position + 22 <= len(tail):
            values = struct.unpack_from("<4s4H2LH", tail, position)
            if position + 22 + values[7] == len(tail):
                break
        position = tail.rfind(b"PK\x05\x06", 0, position)
    if position < 0:
        fail("ZIP end record is invalid")
    _, disk, start_disk, disk_count, count, directory_size, offset, _ = values
    end_offset = size - len(tail) + position
    if disk or start_disk or disk_count != count:
        fail("Multi-volume ZIP files are not supported")
    if count == 65535 or directory_size == 0xffffffff or offset == 0xffffffff:
        if end_offset < 20:
            fail("Invalid ZIP64 locator")
        stream.seek(end_offset - 20)
        locator = stream.read(20)
        signature_, disk, record_offset, disks = struct.unpack("<4sLQL", locator)
        if signature_ != b"PK\x06\x07" or disk or disks != 1 or record_offset + 56 > end_offset - 20:
            fail("Invalid ZIP64 locator")
        stream.seek(record_offset)
        record = stream.read(56)
        values = struct.unpack("<4sQ2H2L4Q", record)
        if values[0] != b"PK\x06\x06" or not 44 <= values[1] <= 4096:
            fail("Invalid ZIP64 end record")
        if values[4] or values[5] or values[6] != values[7]:
            fail("Multi-volume ZIP64 files are not supported")
        count, directory_size, offset = values[7], values[8], values[9]
        end_offset = record_offset
    if (count > MAX_ENTRIES or directory_size > MAX_CENTRAL
            or directory_size < count * 46 or offset + directory_size > end_offset):
        fail("ZIP central directory exceeds its structural budget")
    stream.seek(0)


def inspect_zip(archive, budget):
    entries = []
    nodes = {}
    explicit = set()
    candidates = []
    for member in archive.infolist():
        if member.orig_filename != member.filename:
            fail("ZIP file name contains a NUL byte")
        parts = parts_for(member.filename)
        directory = member.is_dir()
        mode = (member.external_attr >> 16) & 0xffff
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not directory):
            fail("ZIP contains a symbolic link or special file")
        if member.flag_bits & 1:
            fail("Encrypted world ZIP files are not supported")
        if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            fail("World ZIP uses an unsupported compression method")
        if directory and member.file_size:
            fail("ZIP directory contains file data")
        if member.compress_size < 0 or member.compress_size > MAX_TOTAL:
            fail("Invalid ZIP compressed size")
        if member.file_size > 16 * 1024 * 1024 and member.file_size > max(1, member.compress_size) * 500:
            fail("ZIP compression ratio exceeds its safety budget")
        budget.add(0 if directory else member.file_size)
        key = portable(parts)
        if key in explicit:
            fail("ZIP has duplicate or colliding entries")
        explicit.add(key)
        for length in range(1, len(parts) + 1):
            prefix = parts[:length]
            node_key = portable(prefix)
            node_is_dir = directory or length < len(parts)
            if node_key in nodes:
                original, was_dir = nodes[node_key]
                if original != prefix or was_dir != node_is_dir:
                    fail("ZIP has ambiguous paths or file/directory collisions")
            else:
                nodes[node_key] = (prefix, node_is_dir)
        if not directory and parts[-1] == "level.dat" and len(parts) <= 2:
            candidates.append(parts)
        entries.append((member, parts, directory))
    if ("level.dat",) in candidates:
        prefix = ()
    elif len(candidates) == 1 and len(candidates[0]) == 2:
        prefix = candidates[0][:1]
    else:
        fail("ZIP must contain one Java world at its root or under one wrapper folder")
    if prefix and any(parts[:1] != prefix for _, parts, _ in entries):
        fail("ZIP contains files outside the selected world")
    return [(member, parts[len(prefix):], directory) for member, parts, directory in entries
            if parts[len(prefix):] and parts[len(prefix):] != ("session.lock",)]


def empty_stage(path, parent=None):
    path = real_dir(path)
    if not path.name.startswith(".zmcl-world-"):
        fail("Unexpected world staging name")
    if parent is not None and path.parent != real_dir(parent):
        fail("World staging is on an unexpected path")
    if any(path.iterdir()):
        fail("World staging directory is not empty")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        fail("World staging has an unexpected owner")
    return path


def write_file(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def transfer(source, target, expected, budget):
    remaining = expected
    while remaining:
        budget.tick()
        chunk = source.read(min(CHUNK, remaining))
        if not chunk:
            fail("World file is truncated")
        target.write(chunk)
        remaining -= len(chunk)
    if source.read(1):
        fail("World file grew beyond its declared size")


def copy_world(source, stage, budget):
    source = real_dir(source)
    metadata(source)
    entries, stability = scan_tree(source, budget)
    for parts, before, directory in entries:
        budget.tick()
        destination = confined(stage, parts)
        if directory:
            destination.mkdir(mode=0o700)
        else:
            with open_regular(confined(source, parts), before) as original:
                with write_file(destination) as output:
                    transfer(original, output, before.st_size, budget)
                    output.flush()
                    os.fsync(output.fileno())
                if signature(os.fstat(original.fileno())) != signature(before):
                    fail("World file changed during the copy")
    stable(stability)
    return metadata(stage), sum(not directory for _, _, directory in entries), budget.total


def unpack(source, stage, budget):
    source = Path(os.path.abspath(source))
    before = source.lstat()
    with open_regular(source, before, max_size=MAX_TOTAL) as original:
        check_zip_directory(original)
        with zipfile.ZipFile(original) as archive:
            entries = inspect_zip(archive, budget)
            for member, parts, directory in entries:
                budget.tick()
                destination = confined(stage, parts)
                if directory:
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with archive.open(member) as content, write_file(destination) as output:
                        transfer(content, output, member.file_size, budget)
                        output.flush()
                        os.fsync(output.fileno())
        if signature(os.fstat(original.fileno())) != signature(before):
            fail("World ZIP changed during import")
    if signature(source.lstat()) != signature(before):
        fail("World ZIP changed during import")
    return metadata(stage), sum(not directory for _, _, directory in entries), sum(
        member.file_size for member, _, directory in entries if not directory)


def fsync_directory(path):
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_directory(stage, target):
    """Atomic publication that cannot replace an existing file or empty dir."""
    if stage.is_dir():
        fsync_directory(stage)
    if os.name == "nt":
        os.rename(stage, target)  # Windows rename always refuses an existing target.
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            fail("Atomic no-replace directory publication needs renameat2 on Linux")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    elif sys.platform == "darwin":
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            fail("Atomic no-replace directory publication is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(stage), os.fsencode(target), 0x4) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
    else:
        fail("Atomic world directory publication is unsupported on this platform")
    fsync_directory(target.parent)


def target_world(instance, name, stage):
    saves = save_root(instance)
    target = confined(saves, (world_name(name),))
    if target.exists() or target.is_symlink():
        fail("A world with this folder name already exists")
    stage = empty_stage(stage, saves)
    return target, stage


def import_world(instance, source, name, stage):
    instance = real_dir(instance)
    guard_stopped(instance)
    target, stage = target_world(instance, name, stage)
    source = Path(os.path.abspath(source))
    budget = Budget()
    if source.is_symlink():
        fail("Symbolic world imports are forbidden")
    if source.is_dir():
        source = real_dir(source)
        if source == stage or source in stage.parents or stage in source.parents:
            fail("World import source and staging overlap")
        with SessionLock(source, create=True):
            result, count, total = copy_world(source, stage, budget)
    else:
        result, count, total = unpack(source, stage, budget)
    guard_stopped(instance)
    publish_directory(stage, target)
    result.update(id=name, path=str(target), file_count=count, bytes=total)
    return result


def clone_world(source_instance, name, target_instance, new_name, stage):
    source_instance, target_instance = real_dir(source_instance), real_dir(target_instance)
    guard_stopped(source_instance)
    guard_stopped(target_instance)
    source = real_dir(world_path(source_instance, name))
    target, stage = target_world(target_instance, new_name, stage)
    budget = Budget()
    with SessionLock(source, create=True):
        result, count, total = copy_world(source, stage, budget)
        guard_stopped(target_instance)
        publish_directory(stage, target)
    result.update(id=new_name, path=str(target), file_count=count, bytes=total)
    return result


def export_world(instance, name, destination, stage):
    instance = real_dir(instance)
    guard_stopped(instance)
    source = real_dir(world_path(instance, name))
    target, stage = Path(os.path.abspath(destination)), Path(os.path.abspath(stage))
    parent = real_dir(target.parent)
    if parent == source or source in parent.parents:
        fail("World export must be outside the source world")
    if stage.parent != parent or not stage.name.startswith(".zmcl-world-"):
        fail("Unexpected world export staging path")
    if target.exists() or target.is_symlink():
        fail("Export destination already exists")
    before_stage = stage.lstat()
    if before_stage.st_size or before_stage.st_nlink != 1:
        fail("World export staging must be an empty private file")
    budget = Budget()
    with SessionLock(source, create=True):
        result = metadata(source)
        entries, stability = scan_tree(source, budget)
        with open_regular(stage, before_stage, writable=True) as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                for parts, before, directory in entries:
                    budget.tick()
                    member = zipfile.ZipInfo("/".join(parts) + ("/" if directory else ""))
                    member.create_system = 3
                    member.compress_type = zipfile.ZIP_DEFLATED
                    member.external_attr = ((stat.S_IFDIR | 0o700) if directory else
                                            (stat.S_IFREG | 0o600)) << 16
                    if directory:
                        member.external_attr |= 0x10
                        archive.writestr(member, b"")
                    else:
                        member.file_size = before.st_size
                        with open_regular(confined(source, parts), before) as content:
                            with archive.open(member, "w", force_zip64=before.st_size >= 2**31) as archived:
                                transfer(content, archived, before.st_size, budget)
                            if signature(os.fstat(content.fileno())) != signature(before):
                                fail("World file changed during export")
            output.flush()
            os.fsync(output.fileno())
        stable(stability)
        digest = hashlib.sha256()
        with open_regular(stage, max_size=MAX_TOTAL) as written:
            for chunk in iter(lambda: written.read(CHUNK), b""):
                budget.tick()
                digest.update(chunk)
        size = stage.stat().st_size
        publish_directory(stage, target)
    return dict(path=str(target), world=result,
                file_count=sum(not directory for _, _, directory in entries),
                bytes=budget.total, archive_bytes=size, sha256=digest.hexdigest())


def main():
    operation, *args = sys.argv[1:]
    if operation == "list" and len(args) == 1:
        result = list_worlds(Path(args[0]))
    elif operation == "info" and len(args) == 2:
        result = info(Path(args[0]), args[1])
    elif operation == "guard" and len(args) == 1:
        guard_stopped(Path(args[0]))
        result = {"stopped": True}
    elif operation == "import" and len(args) == 4:
        result = import_world(Path(args[0]), Path(args[1]), args[2], Path(args[3]))
    elif operation == "export" and len(args) == 4:
        result = export_world(Path(args[0]), args[1], Path(args[2]), Path(args[3]))
    elif operation == "clone" and len(args) == 5:
        result = clone_world(Path(args[0]), args[1], Path(args[2]), args[3], Path(args[4]))
    else:
        fail("Invalid world helper operation")
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError, UnicodeError, struct.error, zipfile.BadZipFile,
            RuntimeError, NotImplementedError) as error:
        print(str(error)[:4096], file=sys.stderr)
        sys.exit(1)
