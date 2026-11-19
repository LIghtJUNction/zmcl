"""Bounded Java archive extraction; no extractall on downloaded archives."""
import json, os, pathlib, shutil, stat, sys, tarfile, zipfile
MAX_ENTRIES = 30000
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024

def portable_segment(value):
    if not value or value in (".", "..") or value.endswith((" ", ".")):
        raise ValueError("Unsafe archive component")
    if any(ord(c) < 32 or c in '\\/:*?"<>|' for c in value):
        raise ValueError("Unsafe archive component")
    stem = value.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", "CLOCK$"} or (
        len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"):
        raise ValueError("Reserved archive component")

def member_parts(name, directory=False):
    if not name or len(name) > 4096 or name.startswith("/") or "\\" in name or ":" in name:
        raise ValueError("Unsafe archive path")
    raw = name.rstrip("/").split("/")
    while raw and raw[0] == ".": raw.pop(0)
    if not raw and directory: return ()
    for component in raw: portable_segment(component)
    return tuple(raw)

def link_target(name, origin, hard):
    if not name or len(name) > 4096 or name.startswith("/") or "\\" in name or ":" in name:
        raise ValueError("Unsafe Java archive link")
    parts = [] if hard else list(origin[:-1])
    for part in name.split("/"):
        if part in ("", "."): continue
        if part == "..":
            if not parts: raise ValueError("Archive link escapes root")
            parts.pop()
        else:
            portable_segment(part); parts.append(part)
    if not parts: raise ValueError("Archive link targets root")
    return tuple(parts)

def extract(source, destination):
    root = pathlib.Path(destination)
    root.mkdir(mode=0o700, exist_ok=False)
    records, archive, total = [], None, 0
    is_zip = zipfile.is_zipfile(source)
    try:
        if is_zip:
            archive = zipfile.ZipFile(source)
            for count, entry in enumerate(archive.infolist(), 1):
                if count > MAX_ENTRIES: raise ValueError("Too many Java archive entries")
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode): raise ValueError("ZIP symbolic links are unsupported")
                kind = "dir" if entry.is_dir() else "file"
                if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("Special archive file rejected")
                parts = member_parts(entry.filename, kind == "dir")
                if not parts: continue
                records.append((parts, kind, entry, None, mode))
                if entry.file_size < 0 or entry.file_size > MAX_FILE: raise ValueError("Java archive file exceeds size limit")
                total += entry.file_size
                if total > MAX_TOTAL: raise ValueError("Java archive exceeds extraction limit")
        else:
            archive = tarfile.open(source, "r:*")
            for count, entry in enumerate(archive, 1):
                if count > MAX_ENTRIES: raise ValueError("Too many Java archive entries")
                if entry.isdir(): kind = "dir"
                elif entry.isreg() and not entry.sparse: kind = "file"
                elif entry.issym(): kind = "sym"
                elif entry.islnk(): kind = "hard"
                else: raise ValueError("Special or sparse archive file rejected")
                parts = member_parts(entry.name, kind == "dir")
                if not parts: continue
                target = link_target(entry.linkname, parts, kind == "hard") if kind in ("sym", "hard") else None
                records.append((parts, kind, entry, target, entry.mode))
                if entry.size < 0 or entry.size > MAX_FILE: raise ValueError("Java archive file exceeds size limit")
                total += entry.size
                if total > MAX_TOTAL: raise ValueError("Java archive exceeds extraction limit")
        by_path, folded = {}, set()
        for record in records:
            parts = record[0]
            key = "/".join(parts).casefold()
            if key in folded: raise ValueError("Duplicate Java archive path")
            folded.add(key); by_path[parts] = record
        for parts in by_path:
            for i in range(1, len(parts)):
                parent = by_path.get(parts[:i])
                if parent is not None and parent[1] != "dir":
                    raise ValueError("Archive path traverses a non-directory")
        def resolve(parts, seen):
            if parts in seen or len(seen) >= 32: raise ValueError("Archive link cycle")
            record = by_path.get(parts)
            if record is None:
                if any(p[:len(parts)] == parts and len(p) > len(parts) for p in by_path):
                    return parts, "dir"
                raise ValueError("Archive link targets absent content")
            if record[1] in ("file", "dir"): return parts, record[1]
            return resolve(record[3], seen | {parts})
        resolved = {}
        for parts, kind, _, target, _ in records:
            if kind in ("sym", "hard"):
                destination_parts, target_kind = resolve(target, {parts})
                if kind == "hard" and target_kind != "file": raise ValueError("Hard link must target an archive file")
                resolved[parts] = (destination_parts, target_kind)
        for parts, kind, entry, _, mode in records:
            target = root.joinpath(*parts)
            if kind == "dir":
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif kind == "file":
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                expected = entry.file_size if is_zip else entry.size
                stream = archive.open(entry) if is_zip else archive.extractfile(entry)
                if stream is None: raise ValueError("Archive has no regular file data")
                with stream, target.open("xb") as output:
                    copied = 0
                    while True:
                        data = stream.read(65536)
                        if not data: break
                        copied += len(data)
                        if copied > expected or copied > MAX_FILE: raise ValueError("Java archive exceeds declared size")
                        output.write(data)
                    if copied != expected: raise ValueError("Truncated Java archive file")
                target.chmod(0o600 | (mode & 0o111))
        for parts, kind, _, _, _ in records:
            if kind not in ("sym", "hard"): continue
            target = root.joinpath(*parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination_parts, target_kind = resolved[parts]
            source_path = root.joinpath(*destination_parts)
            if kind == "hard": os.link(source_path, target)
            else: os.symlink(os.path.relpath(source_path, target.parent), target, target_is_directory=target_kind == "dir")
        return {"entries": len(records), "unpacked_bytes": total}
    except BaseException:
        shutil.rmtree(root)
        raise
    finally:
        if archive is not None: archive.close()

if __name__ == "__main__":
    print(json.dumps(extract(sys.argv[1], sys.argv[2]), separators=(",", ":")))
