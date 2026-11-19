"""Bounded .mrpack ZIP codec. Invoked using an isolated system Python 3.

No networking, account access, shell execution, or third-party Python modules.
Rust owns downloads, instance locks, content hashes, and publication.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import unicodedata
import zipfile

MAX_FILES = 50000
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024 * 1024
MAX_INDEX = 8 * 1024 * 1024
SECRET = re.compile(r'(?:access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|password|authorization|private[_-]?key)', re.I)
CONFIG_ROOTS = {"config", "defaultconfigs", "kubejs", "scripts"}


def fail(message):
    raise ValueError(message)


def path_parts(value):
    if (not value or len(value) > 4096 or value.startswith('/') or '\\' in value
            or ':' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail('Unsafe archive path')
    value = value.rstrip('/')
    parts = value.split('/')
    for part in parts:
        stem = part.split('.')[0].upper()
        if (part in ('', '.', '..') or part.endswith((' ', '.'))
                or stem in ('CON', 'PRN', 'AUX', 'NUL')
                or re.fullmatch(r'(COM|LPT)[0-9]', stem)):
            fail('Unsafe portable archive path')
    return parts


def portable_key(parts):
    return '/'.join(unicodedata.normalize('NFC', p).casefold() for p in parts)


def confined(root, parts):
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            fail('Archive destination contains a symbolic link')
    return current


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('Duplicate JSON object key')
        result[key] = value
    return result


def read_json(path):
    with open(path, 'rb') as source:
        body = source.read(MAX_INDEX + 1)
    if len(body) > MAX_INDEX:
        fail('JSON manifest exceeds 8 MiB')
    return json.loads(body, object_pairs_hook=unique_pairs)



def check_directory_budget(source):
    # Check EOCD/ZIP64 sizes before ZipFile allocates its central-directory index.
    size = source.stat().st_size
    with source.open('rb') as stream:
        start = max(0, size - 65557)
        stream.seek(start)
        tail = stream.read(65557)
        position = tail.rfind(b'PK\x05\x06')
        while position >= 0:
            if position + 22 <= len(tail):
                record = struct.unpack_from('<4s4H2IH', tail, position)
                if position + 22 + record[-1] == len(tail):
                    break
            position = tail.rfind(b'PK\x05\x06', 0, position)
        if position < 0:
            fail('Missing ZIP end-of-directory record')
        _, disk, directory_disk, disk_count, count, length, offset, _ = record
        if disk or directory_disk:
            fail('Multi-disk ZIP files are unsupported')
        if count == 65535 or disk_count == 65535 or length == 0xffffffff or offset == 0xffffffff:
            end = start + position
            if end < 20:
                fail('Missing ZIP64 locator')
            stream.seek(end - 20)
            locator = stream.read(20)
            if len(locator) != 20:
                fail('Truncated ZIP64 locator')
            marker, disk, record_offset, disks = struct.unpack('<4sIQI', locator)
            if marker != b'PK\x06\x07' or disk or disks != 1 or record_offset > size - 56:
                fail('Invalid ZIP64 locator')
            stream.seek(record_offset)
            header = stream.read(56)
            if len(header) != 56:
                fail('Truncated ZIP64 header')
            marker, record_length, _, _, disk, directory_disk, disk_count, count, length, offset = struct.unpack('<4sQHHIIQQQQ', header)
            if marker != b'PK\x06\x06' or record_length < 44 or disk or directory_disk:
                fail('Invalid ZIP64 directory')
        if count != disk_count or count > MAX_FILES or length > MAX_INDEX or offset + length > size:
            fail('ZIP central directory exceeds its memory budget')


def unpack(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_TOTAL:
        fail('Invalid or oversized pack archive')
    if destination.is_symlink() or not destination.is_dir():
        fail('Missing private staging directory')
    if any(destination.iterdir()):
        fail('Archive staging directory must be empty')
    check_directory_budget(source)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            fail('Archive has too many files')
        seen, by_name, total = set(), {}, 0
        for item in infos:
            parts = path_parts(item.filename)
            key = portable_key(parts)
            if key in seen:
                fail('Duplicate normalized archive path')
            seen.add(key)
            mode = item.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                fail('Archive links and special files are forbidden')
            if (file_type == stat.S_IFDIR) != item.is_dir() and file_type:
                fail('Inconsistent archive directory type')
            if item.flag_bits & 1:
                fail('Encrypted archives are unsupported')
            if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail('Only stored and deflated ZIP entries are supported')
            if item.file_size < 0 or item.file_size > MAX_FILE:
                fail('Archive member exceeds 512 MiB')
            if item.file_size > max(item.compress_size, 1) * 500:
                fail('Archive compression ratio exceeds the safety budget')
            total += item.file_size
            if total > MAX_TOTAL:
                fail('Uncompressed archive exceeds 4 GiB')
            by_name[item.filename] = (item, parts)
        index = by_name.get('modrinth.index.json')
        if index is None or index[0].is_dir() or index[0].file_size > MAX_INDEX:
            fail('Pack requires a bounded modrinth.index.json')
        with archive.open(index[0]) as stream:
            metadata = stream.read(MAX_INDEX + 1)
        if len(metadata) > MAX_INDEX:
            fail('Pack index exceeds 8 MiB')
        parsed = json.loads(metadata, object_pairs_hook=unique_pairs)
        if not isinstance(parsed, dict):
            fail('Pack index must be an object')
        destinations = {}
        for entry in parsed.get('files', []):
            parts = path_parts(entry['path'])
            key = portable_key(parts)
            if key in destinations:
                fail('Duplicate normalized pack destination')
            destinations[key] = '/'.join(parts)
        for item, parts in by_name.values():
            if parts[0] not in ('overrides', 'client-overrides') or len(parts) < 2:
                continue
            key = portable_key(parts[1:])
            spelling = '/'.join(parts[1:])
            if key in destinations and destinations[key] != spelling:
                fail('Inconsistent portable spelling between pack layers')
            destinations[key] = spelling
        (destination / 'modrinth.index.json').write_bytes(metadata)
        game = destination / 'game'
        game.mkdir()
        # Explicit order implements the specification's client overrides layer.
        for layer in ('overrides', 'client-overrides'):
            for item, parts in by_name.values():
                if parts[0] != layer or len(parts) < 2:
                    continue
                relative = parts[1:]
                if any(p.startswith('.zmcl-') for p in relative):
                    fail('Reserved launcher path in pack overrides')
                target = confined(game, relative)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                remaining = item.file_size
                with archive.open(item) as stream, open(target, 'wb') as output:
                    while True:
                        block = stream.read(min(65536, remaining + 1))
                        if not block:
                            break
                        if len(block) > remaining:
                            fail('ZIP entry exceeds declared size')
                        output.write(block)
                        remaining -= len(block)
                if remaining:
                    fail('Truncated ZIP entry')
                os.chmod(target, 0o600)


def config_files(game):
    """Only export curated configuration paths; never scan accounts or logs."""
    count = total = 0
    for name in sorted(CONFIG_ROOTS | {'options.txt'}):
        start = game / name
        if not start.exists():
            continue
        if start.is_symlink():
            fail('Refusing to export a symbolic link')
        candidates = [start] if start.is_file() else sorted(start.rglob('*'))
        for path in candidates:
            if path.is_symlink():
                fail('Refusing to export a symbolic link')
            if not path.is_file():
                continue
            relative = path.relative_to(game).as_posix()
            parts = path_parts(relative)
            if any(p.startswith('.') or SECRET.search(p) for p in parts):
                continue
            if path.suffix.lower() in {'.jar', '.class', '.so', '.dll', '.exe', '.dylib', '.pem', '.key', '.p12', '.pfx'}:
                continue
            size = path.stat().st_size
            if size > 8 * 1024 * 1024:
                fail('Export configuration file exceeds 8 MiB')
            # Text configuration can contain provider credentials even with an
            # innocent filename. Skip it conservatively rather than export it.
            data = path.read_bytes()
            if SECRET.search(data.decode('utf-8', 'ignore')):
                continue
            total += len(data)
            count += 1
            if count > MAX_FILES or total > 128 * 1024 * 1024:
                fail('Configuration export exceeds budget')
            yield relative, data


def export(game, plan, target):
    game, plan, target = Path(game), Path(plan), Path(target)
    index = read_json(plan)
    paths = set()
    for entry in index['files']:
        parts = path_parts(entry['path'])
        key = portable_key(parts)
        if key in paths:
            fail('Duplicate pack file destination')
        paths.add(key)
        local = confined(game, parts)
        if local.is_symlink() or not local.is_file() or local.stat().st_size > MAX_FILE:
            fail('Missing or invalid managed content file')
        sha1, sha512, length = hashlib.sha1(), hashlib.sha512(), 0
        with local.open('rb') as source:
            while block := source.read(65536):
                sha1.update(block)
                sha512.update(block)
                length += len(block)
                if length > MAX_FILE:
                    fail('Managed file exceeds export budget')
        if sha1.hexdigest() != entry['hashes']['sha1'] or length != entry['fileSize']:
            fail('Managed content changed; refusing to export an invalid pack')
        entry['hashes']['sha512'] = sha512.hexdigest()
    # Exclusive creation: callers publish this temporary file only on success.
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('modrinth.index.json', json.dumps(index, ensure_ascii=False, separators=(',', ':')))
        for relative, data in config_files(game):
            if portable_key(path_parts(relative)) in paths:
                continue
            archive.writestr('overrides/' + relative, data)
    with target.open('rb') as result:
        os.fsync(result.fileno())


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'unpack':
        unpack(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == 'export':
        export(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        fail('Unknown archive operation')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, KeyError, TypeError) as error:
        print('Pack archive error: ' + str(error), file=sys.stderr)
        sys.exit(1)
