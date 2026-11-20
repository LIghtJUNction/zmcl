"""Read one bounded JSON resource from a verified loader installer JAR."""
import os
import stat
import struct
import sys
import zipfile

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_CENTRAL = 16 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_METADATA = 16 * 1024 * 1024


def validate_member_name(name):
    if (not name or len(name.encode("utf-8")) > 4096
            or any(ord(c) < 32 or 127 <= ord(c) < 160 or c in '\\:*?"<>|' for c in name)):
        raise ValueError("Unsafe loader metadata resource path")
    for part in name.split("/"):
        stem = part.split(".")[0].upper()
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or stem in ("CON", "PRN", "AUX", "NUL", "CLOCK$")
                or (len(stem) == 4 and stem[:3] in ("COM", "LPT") and stem[3] in "123456789")):
            raise ValueError("Unsafe loader metadata resource path")


def check_directory(stream):
    """Check the entry-table budget before ZipFile allocates its members."""
    size = os.fstat(stream.fileno()).st_size
    if not 22 <= size <= MAX_ARCHIVE:
        raise ValueError("Loader installer JAR is truncated or exceeds 512 MiB")
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    position = tail.rfind(b"PK\x05\x06")
    while position >= 0:
        if position + 22 <= len(tail):
            fields = struct.unpack_from("<4s4H2LH", tail, position)
            if position + 22 + fields[7] == len(tail):
                break
        position = tail.rfind(b"PK\x05\x06", 0, position)
    if position < 0:
        raise ValueError("Invalid loader installer ZIP end record")
    _, disk, start_disk, disk_count, count, directory_size, offset, _ = fields
    end = size - len(tail) + position
    if end >= 20:
        stream.seek(end - 20)
        # ZipFile prefers ZIP64 values even when the classic end record fits
        # its fields. Reject that override before allocating the entry table.
        if stream.read(4) == b"PK\x06\x07":
            raise ValueError("ZIP64 loader installer archives are not supported")
    if (disk or start_disk or disk_count != count or count > MAX_ENTRIES
            or directory_size > MAX_CENTRAL or directory_size < count * 46
            or offset + directory_size > end):
        raise ValueError("Loader installer ZIP central directory exceeds its limits")
    stream.seek(0)


def read_metadata(source, name):
    validate_member_name(name)
    with open(source, "rb") as stream:
        check_directory(stream)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError("Loader installer has too many ZIP entries")
            matches = [entry for entry in entries if entry.filename == name]
            if len(matches) != 1:
                raise ValueError("Loader installer metadata is missing or has duplicate ZIP entries")
            entry = matches[0]
            if (entry.orig_filename != entry.filename or entry.is_dir()
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or entry.flag_bits & 1
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ValueError("Loader installer metadata must be an unencrypted regular ZIP file")
            if not 0 < entry.file_size <= MAX_METADATA:
                raise ValueError("Loader installer metadata exceeds the 16 MiB limit or is empty")
            with archive.open(entry) as content:
                data = content.read(MAX_METADATA + 1)
            if len(data) != entry.file_size or len(data) > MAX_METADATA:
                raise ValueError("Loader installer metadata size mismatch")
            return data


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("Expected an installer JAR and metadata resource path")
        sys.stdout.buffer.write(read_metadata(sys.argv[1], sys.argv[2]))
    except (ValueError, OSError, EOFError, UnicodeError, struct.error,
            zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        print(str(error)[:1500], file=sys.stderr)
        sys.exit(1)
