"""Bounded portable Horizon installation, with the upstream Ed25519 release key.

Trust anchor: horizon-rust/horizon at
41e53103f721966f902f9c793ba89ac51904e5ad,
crates/horizon-release-sign/release-keys.txt and src/lib.rs.
No network, downloaded scripts, or third-party Python modules are executed.
"""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import zipfile

PUBLIC_KEYS = ("MU4oiIQcCxkZvbwSjjXs7SySCuM6ywRQ3nGaJYZG0gw=",)
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_FILES = 8192
MAX_INDEX = 8 * 1024 * 1024
MAX_DEPTH = 32
MAX_SECONDS = 300
ASSETS = {
    "linux-x86_64": "horizon-launcher-linux-x86_64.tar.gz",
    "windows-x64": "horizon-launcher-windows-x64.zip",
    "windows-arm64": "horizon-launcher-windows-arm64.zip",
    "macos-x64": "horizon-launcher-macos-x64.zip",
    "macos-arm64": "horizon-launcher-macos-arm64.zip",
}


def fail(message):
    raise ValueError(message)


def path_parts(name):
    if (not isinstance(name, str) or not name or len(name.encode("utf-8")) > 4096
            or name.startswith("/") or "\\" in name or ":" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        fail("Unsafe Horizon archive path")
    parts = name.rstrip("/").split("/")
    if len(parts) > MAX_DEPTH:
        fail("Horizon archive exceeds 32 directory levels")
    for part in parts:
        stem = part.split(".")[0].upper()
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or stem in ("CON", "PRN", "AUX", "NUL")
                or re.fullmatch(r"(COM|LPT)[0-9]", stem)):
            fail("Unsafe portable Horizon archive path")
    return parts


def portable_key(parts):
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)


def confined(root, parts):
    current = root
    if current.is_symlink():
        fail("Horizon destination is a symbolic link")
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail("Horizon destination contains a symbolic link")
    return current


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail("Duplicate JSON object key")
        value[key] = item
    return value


def limited(path, maximum):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail("Expected a regular file")
    with path.open("rb") as source:
        value = source.read(maximum + 1)
    if len(value) > maximum:
        fail("File exceeds its metadata budget")
    return value


class Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.total = 0
        self.count = 0

    def tick(self):
        if time.monotonic() - self.started > MAX_SECONDS:
            fail("Horizon helper exceeded 300 seconds")

    def entry(self, size):
        self.tick()
        self.count += 1
        self.total += size
        if self.count > MAX_FILES or size < 0 or size > MAX_FILE or self.total > MAX_TOTAL:
            fail("Horizon archive exceeds entry or byte limits")


def digest_file(path, budget=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail("Expected a regular file for SHA-256 verification")
    before = path.stat()
    if before.st_size > MAX_FILE:
        fail("Horizon file exceeds 512 MiB")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while True:
            if budget:
                budget.tick()
            chunk = stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE:
                fail("Horizon file exceeded its byte limit")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        fail("Horizon file changed while being verified")
    return digest.hexdigest(), size


def signed_hash(tag, sums, signatures, asset, expected, scratch, keys=PUBLIC_KEYS):
    if not re.fullmatch(r"launcher@[0-9]+\.[0-9]+\.[0-9]+", tag):
        fail("Only official stable launcher tags are accepted")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        fail("Invalid GitHub asset SHA-256")
    rows = sums.decode("ascii").splitlines()
    hashes = {}
    for row in rows:
        if not row.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]([A-Za-z0-9_.-]+)", row)
        if not match or match[2].casefold() in hashes:
            fail("Invalid or duplicate SHA256SUMS entry")
        hashes[match[2].casefold()] = match[1].lower()
    if hashes.get(asset.casefold()) != expected:
        fail("GitHub asset digest disagrees with the signed SHA256SUMS")
    encoded = [line.strip() for line in signatures.decode("ascii").splitlines() if line.strip()]
    if not 1 <= len(encoded) <= 4:
        fail("Invalid release signature count")
    signature_bytes = []
    for line in encoded:
        raw = base64.b64decode(line, validate=True)
        if len(raw) != 64:
            fail("Invalid Ed25519 signature length")
        signature_bytes.append(raw)
    message = ("horizon release " + tag + "\n").encode("ascii") + sums
    with tempfile.TemporaryDirectory(prefix="horizon-sign-", dir=scratch) as temporary:
        temporary = Path(temporary)
        message_path = temporary / "message"
        message_path.write_bytes(message)
        key_path, signature_path = temporary / "public.der", temporary / "signature"
        for key in keys:
            raw_key = base64.b64decode(key, validate=True)
            if len(raw_key) != 32:
                fail("Invalid embedded Horizon release key")
            key_path.write_bytes(bytes.fromhex("302a300506032b6570032100") + raw_key)
            for signature in signature_bytes:
                signature_path.write_bytes(signature)
                try:
                    checked = subprocess.run(
                        ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key_path),
                         "-keyform", "DER", "-rawin", "-in", str(message_path), "-sigfile", str(signature_path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=10, check=False)
                except FileNotFoundError:
                    fail("Horizon installation requires system OpenSSL with Ed25519 support")
                if checked.returncode == 0:
                    return expected
    fail("Horizon Ed25519 signature verification failed; release tag or checksums are not trusted")


def check_zip_directory(source):
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
        # Portable launchers are far below ZIP64 limits; refusing ZIP64 keeps the
        # preallocation boundary simple and explicit.
        if count == 65535 or disk_count == 65535 or length == 0xffffffff or offset == 0xffffffff:
            fail("ZIP64 launcher archives are unsupported")
        if count != disk_count or count > MAX_FILES or length > MAX_INDEX or offset + length > size:
            fail("Horizon ZIP central directory exceeds its memory budget")


class Extraction:
    def __init__(self, destination, budget):
        self.destination = destination
        self.budget = budget
        self.seen = set()
        self.types = {}
        self.spellings = {}
        self.files = []
        self.directories = set()

    def path(self, name, directory, size):
        parts = path_parts(name)
        relative = "/".join(parts)
        key = portable_key(parts)
        if key in self.seen:
            fail("Duplicate normalized Horizon archive path")
        self.seen.add(key)
        self.budget.entry(size)
        for length in range(1, len(parts) + 1):
            spelling = "/".join(parts[:length])
            normalized = portable_key(parts[:length])
            if normalized in self.spellings and self.spellings[normalized] != spelling:
                fail("Conflicting normalized Horizon directory spelling")
            self.spellings[normalized] = spelling
        if self.types.get(key) == "dir" and not directory:
            fail("Horizon archive file collides with a directory")
        for length in range(1, len(parts)):
            parent_parts = parts[:length]
            parent_key = portable_key(parent_parts)
            if self.types.get(parent_key) == "file":
                fail("Horizon archive traverses another file")
            self.types[parent_key] = "dir"
            parent_name = "/".join(parent_parts)
            parent = confined(self.destination, parent_parts)
            parent.mkdir(mode=0o700, exist_ok=True)
            self.directories.add(parent_name)
        path = confined(self.destination, parts)
        self.types[key] = "dir" if directory else "file"
        if directory:
            path.mkdir(mode=0o700, exist_ok=True)
            self.directories.add(relative)
        if len(self.directories) + len(self.files) > MAX_FILES:
            fail("Horizon archive creates too many filesystem entries")
        return path, relative

    def file(self, reader, name, size, executable):
        path, relative = self.path(name, False, size)
        digest, count = hashlib.sha256(), 0
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            remaining = size
            while remaining:
                self.budget.tick()
                chunk = reader.read(min(65536, remaining))
                if not chunk:
                    fail("Truncated Horizon archive file")
                remaining -= len(chunk)
                count += len(chunk)
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(path, 0o755 if executable else 0o644)
        self.files.append({"path": relative, "size": count, "sha256": digest.hexdigest()})
        if len(self.directories) + len(self.files) > MAX_FILES:
            fail("Horizon archive creates too many filesystem entries")


def extract_tar(source, output):
    # Parse individual TAR headers rather than TarFile.extract: no PAX/GNU
    # long-name allocation, filesystem links, ownership or special file writes.
    with gzip.open(source, "rb") as archive:
        zero_blocks = 0
        while True:
            output.budget.tick()
            block = archive.read(512)
            if len(block) != 512:
                fail("Truncated TAR header")
            if block == bytes(512):
                zero_blocks += 1
                if zero_blocks == 2:
                    trailing = 0
                    while True:
                        chunk = archive.read(65536)
                        if not chunk:
                            return
                        trailing += len(chunk)
                        if any(chunk) or trailing > 1024 * 1024:
                            fail("Unexpected trailing TAR data")
                        output.budget.tick()
                continue
            if zero_blocks:
                fail("Unexpected TAR data after end marker")
            member = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
            if member.issym() or member.islnk() or member.sparse is not None:
                fail("TAR links and sparse files are forbidden")
            if member.isdir():
                if member.size != 0:
                    fail("TAR directories must contain no data")
                output.path(member.name, True, 0)
            elif member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                output.file(archive, member.name, member.size, bool(member.mode & 0o111))
                padding = (-member.size) % 512
                if padding and archive.read(padding) != bytes(padding):
                    fail("Invalid TAR padding")
            else:
                fail("TAR special files and extended metadata are unsupported")


def extract_zip(source, output):
    check_zip_directory(source)
    with zipfile.ZipFile(source) as archive:
        for item in archive.infolist():
            mode = stat.S_IFMT(item.external_attr >> 16)
            if mode not in (0, stat.S_IFDIR, stat.S_IFREG):
                fail("ZIP links and special files are forbidden")
            if mode and (mode == stat.S_IFDIR) != item.is_dir():
                fail("Inconsistent ZIP directory type")
            if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail("Encrypted or unsupported ZIP entry")
            if item.file_size > max(item.compress_size, 1) * 500:
                fail("Horizon ZIP compression ratio exceeds its safety budget")
            if item.is_dir():
                if item.file_size != 0:
                    fail("ZIP directories must contain no data")
                output.path(item.filename, True, 0)
            else:
                with archive.open(item) as stream:
                    output.file(stream, item.filename, item.file_size, bool((item.external_attr >> 16) & 0o111))
                    if stream.read(1):
                        fail("ZIP file exceeded its declared size")


def entrypoint(destination, files, platform):
    name = "horizon-launcher.exe" if platform.startswith("windows-") else "horizon-launcher"
    matches = [item for item in files if Path(item["path"]).name == name]
    if platform.startswith("macos-"):
        matches = [item for item in matches if ".app/Contents/MacOS/" in item["path"]]
    if len(matches) != 1:
        fail("Archive must contain exactly one expected Horizon launcher executable")
    path = confined(destination, path_parts(matches[0]["path"]))
    with path.open("rb") as stream:
        header = stream.read(64)
    if platform.startswith("linux-") and (not header.startswith(b"\x7fELF") or header[4:6] != b"\x02\x01" or header[18:20] != b"\x3e\x00"):
        fail("Expected a Linux x86-64 ELF launcher")
    if platform.startswith("windows-") and not header.startswith(b"MZ"):
        fail("Expected a Windows PE launcher")
    if platform.startswith("macos-") and header[:4] not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        fail("Expected a macOS Mach-O launcher")
    if os.name != "nt":
        os.chmod(path, 0o755)
    return matches[0]["path"], matches[0]["sha256"]


def extract_archive(source, destination, platform, budget=None):
    source, destination = Path(source), Path(destination)
    budget = budget or Budget()
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_ARCHIVE:
        fail("Invalid or oversized Horizon archive")
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        fail("Horizon extraction requires an empty private staging directory")
    if platform not in ASSETS:
        fail("Unsupported Horizon platform")
    output = Extraction(destination, budget)
    if source.name.endswith(".tar.gz"):
        extract_tar(source, output)
    elif source.name.endswith(".zip"):
        extract_zip(source, output)
    else:
        fail("Unsupported Horizon portable archive format")
    executable, executable_hash = entrypoint(destination, output.files, platform)
    return {"entrypoint": executable, "entry_sha256": executable_hash,
            "files": output.files, "directories": sorted(output.directories),
            "unpacked_bytes": budget.total}


def install(source, sums_path, signature_path, tag, asset, expected, platform, destination):
    source, destination = Path(source), Path(destination)
    if ASSETS.get(platform) != asset or source.name != asset:
        fail("Archive asset does not match the official platform mapping")
    budget = Budget()
    signed_hash(tag, limited(sums_path, 1024 * 1024), limited(signature_path, 16384),
                asset, expected, destination.parent)
    actual, _ = digest_file(source, budget)
    if actual != expected:
        fail("Downloaded Horizon archive failed SHA-256 verification")
    result = extract_archive(source, destination, platform, budget)
    result["signature_verified"] = True
    return result


def check_install(manifest_path, destination):
    value = json.loads(limited(manifest_path, MAX_INDEX), object_pairs_hook=unique_pairs)
    if value.get("provider") != "horizon-rust/horizon" or value.get("component") != "launcher":
        fail("Installed manifest is not an official Horizon launcher")
    platform = value.get("platform")
    if platform not in ASSETS or value.get("asset") != ASSETS[platform]:
        fail("Installed Horizon platform or asset is invalid")
    files, directories = value.get("files"), value.get("directories")
    if not isinstance(files, list) or not isinstance(directories, list) or len(files) + len(directories) > MAX_FILES:
        fail("Installed Horizon file manifest is invalid")
    budget, expected, seen = Budget(), {}, set()
    for directory in directories:
        parts = path_parts(directory)
        key = portable_key(parts)
        if key in seen:
            fail("Duplicate installed Horizon path")
        seen.add(key)
        path = confined(destination, parts)
        if not path.is_dir():
            fail("Installed Horizon directory is missing")
        expected[directory] = "dir"
    for item in files:
        if not isinstance(item, dict):
            fail("Invalid installed Horizon file")
        parts = path_parts(item.get("path"))
        key = portable_key(parts)
        size, digest = item.get("size"), item.get("sha256")
        if key in seen or type(size) is not int or size < 0 or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            fail("Invalid installed Horizon digest or path")
        seen.add(key)
        budget.entry(size)
        actual, actual_size = digest_file(confined(destination, parts), budget)
        if actual != digest or actual_size != size:
            fail("Installed Horizon files changed; preserve this installation and install a fresh signed release")
        expected[item["path"]] = "file"
    actual_paths = {}
    for root, dirs, names in os.walk(destination, followlinks=False):
        budget.tick()
        for name in dirs + names:
            path = Path(root) / name
            if path.is_symlink():
                fail("Installed Horizon contains a symbolic link")
            relative = path.relative_to(destination).as_posix()
            kind = "dir" if path.is_dir() else "file"
            actual_paths[relative] = kind
            if len(actual_paths) > MAX_FILES:
                fail("Installed Horizon contains too many paths")
    if actual_paths != expected:
        fail("Installed Horizon contains unmanifested or missing files")
    executable, digest = entrypoint(destination, files, platform)
    if executable != value.get("entrypoint") or digest != value.get("entry_sha256"):
        fail("Installed Horizon entrypoint differs from its manifest")
    return {"verified": True, "files": len(files), "entrypoint": executable}


def main():
    if sys.argv[1] == "install":
        result = install(*sys.argv[2:10])
    elif sys.argv[1] == "check":
        result = check_install(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        fail("Unknown Horizon helper operation")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Horizon: " + str(error)[:2048], file=sys.stderr)
        sys.exit(1)
