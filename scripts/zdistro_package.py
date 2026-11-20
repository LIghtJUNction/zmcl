#!/usr/bin/env python3
"""Package a verified native zmcl executable with the pinned zdistro CLI.

Python 3.11+ is needed only on the packaging host. The shipped installer and
application helpers keep their own Python 3.8 minimum. No downloads, installation,
signing, publishing or application updates are performed by this wrapper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/LIghtJUNction/zmcl"
ZDISTRO_VERSION = "0.1.0"
ZDISTRO_COMMIT = "6a54a137fc697d372b54ef85105315f2ce522b3d"
ZDISTRO_SHA256 = "a30a4580254a6510c2019e3e7a684c31d4e435b69f32f33d3813ef6264544908"
ZDISTRO_URL = ("https://github.com/LIghtJUNction/zdistro/releases/download/"
               "v0.1.0/zdistro-0.1.0.pyz")
SUFFIXES = {"tar-gz": ".tar.gz", "zip": ".zip", "deb": ".deb"}
RUNTIME_PATHS = ("Cargo.toml", "Cargo.lock", "build.rs", ".gitmodules", "src", "crates", "vendor")
BUILD_PATHS = ("rust-toolchain", "rust-toolchain.toml", ".cargo")
PAYLOAD_PATHS = ("README.md", "LICENSE", "docs/FEATURES.md", "assets/zmcl.svg",
                 "scripts/install.py", "scripts/package.py",
                 "packaging/aur/zmcl-bin/zmcl.desktop")
SOURCE_PATHS = RUNTIME_PATHS + BUILD_PATHS + PAYLOAD_PATHS
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_BINARY_BYTES = 256 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def body_digest(body):
    return hashlib.sha256(body).hexdigest()


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "Missing or non-regular JSON evidence")
    require(path.stat().st_size <= MAX_JSON_BYTES, "JSON evidence exceeds its size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def run(argv, *, cwd=None, timeout=60):
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            check=False, timeout=timeout)
    require(result.returncode == 0,
            f"Command failed ({Path(str(argv[0])).name}, exit {result.returncode}): "
            + result.stderr[-4000:])
    return result.stdout


def source_identity(root, commit):
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "--source-commit must be a full Git commit")
    def git(*args):
        return run(["git", "-C", root, *args]).strip()
    require(git("rev-parse", "--verify", commit + "^{commit}") == commit,
            "Declared source commit is not available")
    require(git("rev-parse", "HEAD") == commit,
            "Source checkout HEAD does not match --source-commit")
    require(not git("diff", "--no-ext-diff", "--ignore-submodules=none", commit, "--", *SOURCE_PATHS),
            "Runtime sources, build configuration or packaged inputs differ from the declared commit")
    require(not git("ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATHS),
            "Untracked runtime source or packaged input files are outside the declared commit")
    require(not git("ls-files", "--others", "--ignored", "--exclude-standard", "--", *SOURCE_PATHS),
            "Ignored files in source or packaged input paths require a clean packaging checkout")
    epoch = int(git("show", "-s", "--format=%ct", commit))
    require(0 <= epoch <= 4294967295, "Source timestamp is outside zdistro's supported range")
    return {"commit": commit, "runtime_paths_match": True, "build_paths_match": True,
            "payload_paths_match": True, "source_date_epoch": epoch,
            "basis": "Caller-declared build source; checkout HEAD and checked runtime, build and payload paths match. "
                     "SHA-256 and --version identify the supplied executable, not its compiler provenance."}


def validate_tool(path):
    require(path.is_file() and not path.is_symlink(), "--zdistro must name the released regular .pyz file")
    require(0 < path.stat().st_size <= 1024 * 1024, "zdistro zipapp exceeds its size limit")
    require(digest(path) == ZDISTRO_SHA256, "zdistro zipapp SHA-256 does not match the pinned v0.1.0 asset")


def host_identity(binary):
    system, machine = platform.system(), platform.machine().lower()
    require(system in ("Linux", "Windows"), "This wrapper currently accepts native Linux or Windows builds")
    require(re.fullmatch(r"[a-z0-9_-]+", machine), "Unsupported host machine name")
    aliases = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
    require(machine in aliases, "Only x86_64 and aarch64 native packaging are supported")
    architecture = aliases[machine]
    with binary.open("rb") as stream:
        header = stream.read(64)
        if system == "Linux":
            require(len(header) >= 20 and header[:4] == b"\x7fELF" and header[4] == 2,
                    "Linux packaging requires a native 64-bit ELF executable")
            require(header[5] in (1, 2), "Invalid ELF byte order")
            elf_machine = struct.unpack("<H" if header[5] == 1 else ">H", header[18:20])[0]
            require(elf_machine == {"x86_64": 62, "aarch64": 183}[architecture],
                    "ELF architecture does not match the packaging host")
        else:
            require(len(header) >= 64 and header[:2] == b"MZ",
                    "Windows packaging requires a native PE executable")
            offset = struct.unpack("<I", header[60:64])[0]
            require(64 <= offset <= min(binary.stat().st_size - 6, 1024 * 1024),
                    "Invalid PE header offset")
            stream.seek(offset)
            pe = stream.read(6)
            require(pe[:4] == b"PE\0\0" and struct.unpack("<H", pe[4:6])[0]
                    == {"x86_64": 0x8664, "aarch64": 0xAA64}[architecture],
                    "PE architecture does not match the packaging host")
    return {"system": system, "machine": machine, "architecture": architecture,
            "platform": system.lower() + "-" + machine}


def validate_binary(root, binary, expected_sha256):
    require(re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256), "--expected-sha256 must be a SHA-256 digest")
    require(binary.is_file() and not binary.is_symlink(), "--binary must be a regular executable")
    require(0 < binary.stat().st_size <= MAX_BINARY_BYTES, "Executable exceeds the packaging size limit")
    actual = digest(binary)
    require(actual == expected_sha256.lower(), "Frozen binary SHA-256 mismatch")
    host = host_identity(binary)
    package = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    require(package["name"] == "zmcl" and package["repository"].rstrip("/") == REPOSITORY,
            "Expected the official zmcl package metadata")
    version = package["version"]
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version),
            "Unsupported release version")
    actual_version = run([binary, "--version"], timeout=15).strip()
    require(actual_version == "zmcl " + version,
            f"Binary --version does not match Cargo.toml ({version})")
    require(digest(binary) == actual, "Executable changed during its version check")
    return {**host, "version": version, "sha256": actual, "bytes": binary.stat().st_size,
            "version_output": actual_version}


def distribution_marker(version, format_name):
    return {"schema": 1, "application": "zmcl", "version": version, "format": format_name,
            "packager": "zdistro", "packager_version": ZDISTRO_VERSION,
            "packager_commit": ZDISTRO_COMMIT, "release_url": REPOSITORY + "/releases/tag/v" + version}


def validate_targets(identity, targets):
    require(len(set(targets)) == len(targets), "Duplicate requested targets")
    require(targets and set(targets) <= ({"zip"} if identity["system"] == "Windows" else set(SUFFIXES)),
            "Requested target is not supported for this native packaging host")
    if "deb" in targets:
        # SemVer's '-' and '+' do not have Debian's version ordering semantics.
        # Numeric preview releases (for example 0.1.4) remain supported; mapping
        # pre-release/build suffixes needs an explicit package-version contract.
        require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", identity["version"]),
                "DEB currently requires a numeric X.Y.Z version; SemVer suffixes are not mapped")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def file_rows(payload):
    return [{"path": name, "bytes": len(item["data"]), "sha256": body_digest(item["data"]),
             "mode": item["mode"]} for name, item in sorted(payload.items())]


def read_archive(path, expected, *, root_name="", deb_data=False, check_modes=True):
    """Validate every name, file byte and mode without extracting archive paths."""
    table = {item["path"]: item for item in expected}
    require(len(table) == len(expected), "Duplicate expected payload path")
    directories = set()
    for name in table:
        parts = PurePosixPath(name).parts
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))
    observed, entries_seen = {}, set()

    def accept(name, directory, regular, mode, size, reader):
        if deb_data and name.startswith("./"):
            name = name[2:]
        if directory and name in ("", "."):
            require(deb_data, "Unexpected archive root entry")
            return
        clean = name.rstrip("/") if directory else name
        parsed = PurePosixPath(clean)
        require(clean and not parsed.is_absolute() and ".." not in parsed.parts
                and str(parsed) == clean and "\\" not in clean, "Unsafe archive path")
        if root_name:
            if directory and clean == root_name:
                return
            require(clean.startswith(root_name + "/"), "Unexpected archive root")
            clean = clean[len(root_name) + 1:]
        require(clean not in entries_seen, "Duplicate archive entry")
        entries_seen.add(clean)
        if directory:
            require(clean in directories, "Unexpected archive directory")
            return
        require(regular and clean in table, "Unexpected or non-regular archive file")
        row = table[clean]
        require(size == row["bytes"], "Archive file size mismatch")
        body = reader(size + 1)
        require(len(body) == size and body_digest(body) == row["sha256"],
                "Archive file SHA-256 mismatch")
        expected_mode = row.get("mode", 0o755 if clean in ("zmcl", "zmcl.exe", "scripts/install.py") else 0o644)
        if check_modes:
            require(stat.S_IMODE(mode) == expected_mode, "Archive file mode mismatch")
        observed[clean] = {"data": body, "mode": expected_mode}

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                require(not info.flag_bits & 1, "Encrypted archive entries are unsupported")
                mode = info.external_attr >> 16
                with archive.open(info) as stream:
                    accept(info.filename, info.is_dir(), stat.S_ISREG(mode), mode, info.file_size, stream.read)
    else:
        with tarfile.open(path, "r:*") as archive:
            for info in archive:
                if info.isfile():
                    with archive.extractfile(info) as stream:
                        accept(info.name, False, True, info.mode, info.size, stream.read)
                else:
                    accept(info.name, info.isdir(), False, info.mode, info.size, lambda _n: b"")
    require(set(observed) == set(table), "Archive payload file set mismatch")
    return observed


def legacy_payload(root, binary, identity, commit, work):
    destination = work / "legacy"
    destination.mkdir()
    report = destination / "package-result.json"
    stdout = run([sys.executable, "-I", root / "scripts/package.py", "--binary", binary,
                  "--output", destination, "--source-commit", commit,
                  "--expected-sha256", identity["sha256"], "--expected-binary-bytes", identity["bytes"],
                  "--report", report], cwd=root, timeout=120)
    (destination / "stdout.json").write_text(stdout, encoding="utf-8")
    data = read_json(report)
    require(data["passed"] is True and data["app_source_commit"] == commit
            and data["binary_sha256"] == identity["sha256"]
            and data["platform"] == identity["platform"], "Legacy payload identity mismatch")
    archive = Path(data["archive"]).resolve()
    require(archive.parent == destination.resolve() and archive.is_file() and not archive.is_symlink(),
            "Legacy archive escaped its evidence directory")
    require(digest(archive) == data["sha256"], "Legacy archive SHA-256 mismatch")
    root_name = "zmcl-" + identity["version"] + "-" + identity["platform"]
    payload = read_archive(archive, data["files"], root_name=root_name)
    release = json.loads(payload["release.json"]["data"])
    require(release["version"] == identity["version"] and release["app_source_commit"] == commit
            and release["binary"]["sha256"] == identity["sha256"], "release.json identity mismatch")
    return payload, release, root_name


def deb_dependencies(release):
    runtime = release["runtime_dependencies"]
    needed = runtime["linux_direct_elf_needed"]
    require(isinstance(needed, list), "DEB requires inspected Linux ELF dependencies")
    mapping = {
        "libc.so.6": "libc6", "libm.so.6": "libc6", "libdl.so.2": "libc6",
        "libpthread.so.0": "libc6", "librt.so.1": "libc6",
        "ld-linux-x86-64.so.2": "libc6", "ld-linux-aarch64.so.1": "libc6",
        "libgcc_s.so.1": "libgcc-s1", "libstdc++.so.6": "libstdc++6",
        "libX11.so.6": "libx11-6", "libfreetype.so.6": "libfreetype6",
        "libfontconfig.so.1": "libfontconfig1", "libGL.so.1": "libgl1",
        "libOpenGL.so.0": "libopengl0", "libGLX.so.0": "libglx0",
    }
    unknown = sorted(set(needed) - set(mapping))
    require(not unknown, "Unmapped Debian runtime libraries: " + ", ".join(unknown))
    dependencies = {mapping[name] for name in needed}
    for dependency, field in (
        ("libc6", "linux_direct_elf_minimum_glibc"),
        ("libfontconfig1", "linux_direct_elf_minimum_fontconfig"),
    ):
        minimum = runtime.get(field)
        if dependency in dependencies and minimum:
            require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", minimum), "Invalid dependency version floor")
            dependencies.remove(dependency)
            dependencies.add(dependency + " (>= " + minimum + ")")
    dependencies.update(("curl", "python3 (>= 3.8)", "hicolor-icon-theme"))
    return sorted(dependencies)


def target_payload(root, seed, release, target, version):
    if target != "deb":
        payload = {key: dict(value) for key, value in seed.items() if key != "release.json"}
        binary_path, release_path, marker_path = ("zmcl.exe" if os.name == "nt" else "zmcl",
                                                 "release.json", "share/zmcl/distribution.json")
    else:
        mapping = {"zmcl": "usr/bin/zmcl", "README.md": "usr/share/doc/zmcl/README.md",
                   "LICENSE": "usr/share/doc/zmcl/copyright",
                   "docs/FEATURES.md": "usr/share/doc/zmcl/FEATURES.md",
                   "assets/zmcl.svg": "usr/share/icons/hicolor/scalable/apps/zmcl.svg"}
        require(set(seed) == set(mapping) | {"scripts/install.py", "release.json"},
                "Legacy payload changed; review the DEB file mapping")
        payload = {destination: dict(seed[source]) for source, destination in mapping.items()}
        desktop = root / "packaging/aur/zmcl-bin/zmcl.desktop"
        payload["usr/share/applications/zmcl.desktop"] = {"data": desktop.read_bytes(), "mode": 0o644}
        binary_path, release_path, marker_path = ("usr/bin/zmcl", "usr/share/doc/zmcl/release.json",
                                                 "usr/share/zmcl/distribution.json")
    marker = distribution_marker(version, "deb" if target == "deb" else "portable")
    payload[marker_path] = {"data": json_bytes(marker), "mode": 0o644}
    updated = json.loads(json.dumps(release))
    updated["binary"]["path"] = binary_path
    updated["files"] = [{key: value for key, value in row.items() if key != "mode"}
                        for row in file_rows(payload)]
    updated["distribution"] = marker
    updated["distribution_packager"] = {"path": "scripts/zdistro_package.py",
                                        "sha256": digest(Path(__file__)),
                                        "zdistro_sha256": ZDISTRO_SHA256,
                                        "scope": "Package creation and payload verification; no installation, signing or publishing."}
    payload[release_path] = {"data": json_bytes(updated), "mode": 0o644}
    return payload


def make_manifest(root, work, identity, source, targets, seed, release, root_name):
    version = identity["version"]
    lines = ["schema = 1", "source_date_epoch = " + str(source["source_date_epoch"]), "",
             "[package]", 'name = "zmcl"', "version = " + json.dumps(version),
             'description = "A small native Minecraft launcher"',
             'maintainer = "LIghtJUNction"', 'license = "MIT"',
             "homepage = " + json.dumps(REPOSITORY), 'identifier = "dev.projectz.zmcl"',
             "", "[build]", 'kind = "none"', ""]
    staged = {}
    for target in targets:
        payload = target_payload(root, seed, release, target, version)
        base = work / "payload" / target
        lines.append("[targets." + target + "]")
        if target == "deb":
            lines.extend(["architecture = " + json.dumps({"x86_64": "amd64", "aarch64": "arm64"}[identity["architecture"]]),
                          "depends = " + json.dumps(deb_dependencies(release))])
        lines.append("files = [")
        staged[target] = {}
        for name, item in sorted(payload.items()):
            source_file = base / name
            source_file.parent.mkdir(parents=True, exist_ok=True)
            source_file.write_bytes(item["data"])
            source_file.chmod(item["mode"])
            destination = name if target == "deb" else root_name + "/" + name
            staged[target][destination] = item
            lines.append("  {source = " + json.dumps(source_file.relative_to(work).as_posix())
                         + ", destination = " + json.dumps(destination)
                         + ', mode = "' + format(item["mode"], "04o") + '"},')
        lines.extend(["]", ""])
    manifest = work / "zdistro.toml"
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest, staged


class Zdistro:
    def __init__(self, tool, work, manifest, timeout):
        self.tool, self.work, self.manifest, self.timeout = tool, work, manifest, timeout
        self.count = 0

    def call(self, command, *arguments, json_output=True):
        validate_tool(self.tool)
        self.count += 1
        result = subprocess.run(
            [str(arg) for arg in (sys.executable, "-I", "-S", "-X", "utf8", self.tool, command,
                                  "--manifest", self.manifest, *arguments)],
            cwd=self.work, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=self.timeout)
        prefix = self.work / (f"{self.count:02d}-" + command)
        prefix.with_suffix(".stdout.json").write_text(result.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
        require(result.returncode == 0, f"zdistro {command} failed: " + result.stderr[-4000:])
        require(len(result.stdout.encode("utf-8")) <= MAX_JSON_BYTES,
                "zdistro JSON output exceeds its size limit")
        # The pinned 0.1.0 zipapp ignores main()'s return code. Its JSON contract,
        # including stderr error records, must therefore be checked independently.
        if result.stderr:
            try:
                error_record = json.loads(result.stderr)
            except ValueError:
                error_record = None
            require(not isinstance(error_record, dict) or error_record.get("status") != "error",
                    f"zdistro {command} failed: " + result.stderr[-4000:])
        try:
            data = json.loads(result.stdout)
        except ValueError as error:
            raise ValueError(f"zdistro {command} did not return JSON evidence: "
                             + result.stderr[-4000:]) from error
        require(not isinstance(data, dict) or data.get("status") != "error",
                f"zdistro {command} returned an error record")
        return data if json_output else result.stdout


def complete_targets(rows, targets, stage):
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            stage + " did not return target records")
    names = [row.get("target") for row in rows]
    require(len(names) == len(targets) and set(names) == set(targets),
            stage + " returned missing, duplicate or unexpected targets")


def checked_output(path, directory):
    path = Path(path)
    require(path.is_absolute(), "zdistro returned a non-absolute output path")
    require(not path.is_symlink() and path.is_file(), "Missing or non-regular zdistro output")
    resolved = path.resolve()
    require(resolved.is_relative_to(directory.resolve()), "zdistro output escaped its directory")
    return resolved


def validate_build(data, targets, manifest, output, staged, identity):
    require(isinstance(data, dict) and data.get("skipped") == [], "zdistro skipped a required target")
    complete_targets(data.get("built"), targets, "build")
    manifest_hash = digest(manifest)
    observed_paths, checked = set(), []
    for item in data["built"]:
        target = item["target"]
        artifact = checked_output(item["artifact"], output)
        receipt_path = checked_output(item["receipt"], output)
        require(artifact.parent == receipt_path.parent and artifact not in observed_paths,
                "Artifacts from different targets collide")
        observed_paths.add(artifact)
        receipt = read_json(receipt_path)
        expected_rows = [{"path": row["path"], "size": row["bytes"], "sha256": row["sha256"],
                          "mode": oct(row["mode"])} for row in file_rows(staged[target])]
        require(receipt.get("schema") == 1 and receipt.get("status") == "verified"
                and receipt.get("target") == target and receipt.get("zdistro_version") == ZDISTRO_VERSION
                and receipt.get("manifest_sha256") == manifest_hash
                and receipt.get("host") == identity["system"].lower()
                and receipt.get("package", {}).get("name") == "zmcl"
                and receipt.get("package", {}).get("version") == identity["version"],
                "Receipt identity or manifest SHA-256 mismatch")
        require(receipt.get("installed") is False and receipt.get("published") is False
                and receipt.get("signing") == "not performed by zdistro",
                "Receipt unexpectedly claims installation, publication or signing")
        require(receipt.get("artifact") == artifact.name and receipt.get("bytes") == artifact.stat().st_size
                and receipt.get("sha256") == digest(artifact) == item.get("sha256"),
                "Receipt artifact size or SHA-256 mismatch")
        require(sorted(receipt.get("staged_files", []), key=lambda row: row["path"]) == expected_rows,
                "Receipt staged payload differs from the requested files")
        checked.append({**item, "artifact": artifact, "receipt": receipt_path,
                        "receipt_sha256": digest(receipt_path)})
    return checked


def verify_payload(artifact, target, payload, work, identity, dependencies):
    rows = file_rows(payload)
    if target != "deb":
        # Windows ACLs do not round-trip POSIX chmod bits through its filesystem.
        # Check bytes/names there; Linux release payloads must also retain modes.
        read_archive(artifact, rows, check_modes=identity["system"] != "Windows")
        return
    require(run(["dpkg-deb", "--field", artifact, "Package"]).strip() == "zmcl"
            and run(["dpkg-deb", "--field", artifact, "Version"]).strip() == identity["version"]
            and run(["dpkg-deb", "--field", artifact, "Architecture"]).strip()
            == {"x86_64": "amd64", "aarch64": "arm64"}[identity["architecture"]],
            "DEB package identity mismatch")
    actual = run(["dpkg-deb", "--field", artifact, "Depends"]).strip()
    require(sorted(part.strip() for part in actual.split(",")) == sorted(dependencies),
            "DEB dependency metadata mismatch")
    data = work / "deb-payload.tar"
    with data.open("xb") as stream:
        process = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(artifact)],
                                 stdout=stream, stderr=subprocess.PIPE, check=False, timeout=60)
    require(process.returncode == 0, "dpkg-deb could not inspect the payload")
    read_archive(data, rows, deb_data=True)


def publish_copies(items, destination, identity, evidence):
    destination.mkdir()
    names = ["zmcl-" + identity["version"] + "-" + identity["platform"] + SUFFIXES[item["target"]]
             for item in items]
    require(len(set(names)) == len(names), "Public artifact filenames collide")
    mappings = []
    for item, name in zip(items, names):
        target = destination / name
        require(not target.exists(), "Refusing to replace an existing public artifact")
        require(digest(item["receipt"]) == item["receipt_sha256"],
                "Original receipt changed before making the public copy")
        shutil.copyfile(item["artifact"], target)
        require(digest(target) == item["sha256"], "Public copy changed the artifact bytes")
        target.with_name(name + ".sha256").write_text(item["sha256"] + "  " + name + "\n", encoding="ascii")
        mappings.append({"target": item["target"], "file": name, "bytes": target.stat().st_size,
                         "sha256": item["sha256"],
                         "original_artifact": item["artifact"].relative_to(evidence).as_posix(),
                         "original_receipt": item["receipt"].relative_to(evidence).as_posix(),
                         "receipt_sha256": item["receipt_sha256"]})
    (destination / "SHA256SUMS").write_text(
        "".join(item["sha256"] + "  " + item["file"] + "\n" for item in mappings), encoding="ascii")
    return mappings


def package_release(args):
    root = args.source_root.resolve(strict=True)
    binary = Path(os.path.abspath(args.binary))
    tool = Path(os.path.abspath(args.zdistro))
    validate_tool(tool)
    source = source_identity(root, args.source_commit)
    identity = validate_binary(root, binary, args.expected_sha256)
    targets = list(args.target)
    validate_targets(identity, targets)
    require(args.timeout > 0, "--timeout must be positive")
    output = args.output.resolve()
    require(not output.exists(), "--output must be a new directory; previous evidence is never overwritten")
    output.mkdir(parents=True)
    work = output / "evidence"
    work.mkdir()
    pinned_tool = work / "zdistro-0.1.0.pyz"
    shutil.copyfile(tool, pinned_tool)
    validate_tool(pinned_tool)
    write_json(work / "input-identity.json", {"source": source, "binary": identity})
    seed, release, root_name = legacy_payload(root, binary, identity, args.source_commit, work)
    manifest, staged = make_manifest(root, work, identity, source, targets, seed, release, root_name)
    manifest_hash = digest(manifest)
    client = Zdistro(pinned_tool, work, manifest, args.timeout)
    target_args = [value for target in targets for value in ("--target", target)]
    doctors = client.call("doctor", *target_args, "--json")
    complete_targets(doctors, targets, "doctor")
    require(all(row.get("ready") is True for row in doctors), "A required target is not ready")
    plans = client.call("plan", *target_args)
    complete_targets(plans, targets, "plan")
    require(all(row.get("manifest_sha256") == manifest_hash for row in plans), "Plan manifest changed")
    artifacts = work / "artifacts"
    result = client.call("build", *target_args, "--out", artifacts, "--timeout", args.timeout)
    checked = validate_build(result, targets, manifest, artifacts, staged, identity)
    for item in checked:
        verified = client.call("verify", item["receipt"], "--timeout", args.timeout)
        require(verified.get("sha256") == item["sha256"], "Independent receipt verification returned another artifact")
        require(digest(item["receipt"]) == item["receipt_sha256"] and digest(manifest) == manifest_hash,
                "Original manifest or receipt changed during verification")
        verify_payload(item["artifact"], item["target"], staged[item["target"]], work, identity,
                       deb_dependencies(release) if item["target"] == "deb" else [])
    matrix = client.call("matrix", *target_args, "--receipts", artifacts, "--format", "json")
    complete_targets(matrix.get("rows"), targets, "matrix")
    require(matrix.get("manifest_sha256") == manifest_hash
            and all(row["platforms"][identity["system"].lower()]["status"]
                    in ("verified-native", "verified-structure") for row in matrix["rows"]),
            "Matrix does not contain verified evidence for every requested target")
    require(digest(binary) == identity["sha256"] and source_identity(root, args.source_commit) == source,
            "Binary or runtime sources changed during packaging")
    mappings = publish_copies(checked, output / "public", identity, work)
    summary = {"schema": 1, "application": "zmcl", "version": identity["version"],
               "platform": identity["platform"], "source": source, "binary": identity,
               "packager": {"name": "zdistro", "version": ZDISTRO_VERSION,
                            "commit": ZDISTRO_COMMIT, "sha256": ZDISTRO_SHA256, "url": ZDISTRO_URL,
                            "wrapper": "scripts/zdistro_package.py", "wrapper_sha256": digest(Path(__file__))},
               "manifest": "evidence/zdistro.toml", "manifest_sha256": manifest_hash,
               "artifacts": mappings, "original_paths_relative_to": "evidence",
               "requested_targets": targets, "skipped_targets": [],
               "runtime_dependencies": release["runtime_dependencies"],
               "checks": {"native_binary_version": True, "all_requested_targets": True,
                          "original_receipts_reverified": True, "payload_bytes": True,
                          "posix_payload_modes": identity["system"] == "Linux",
                          "installed": False, "gui_started": False, "published": False, "signed": False},
               "scope": "Native binary --version and package format/payload verification. "
                        "No install, GUI launch, cross-platform runtime, signing or update acceptance. "
                        "Raw evidence is unchanged and may contain packaging-host paths; "
                        "publish the portable summary and asset hashes separately."}
    write_json(output / "release-result.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--zdistro", required=True, type=Path,
                        help="Downloaded zdistro v0.1.0 .pyz; exact release SHA-256 is required")
    parser.add_argument("--target", required=True, action="append", choices=sorted(SUFFIXES))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(package_release(args), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired,
            tarfile.TarError, zipfile.BadZipFile) as error:
        print("zmcl zdistro package: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
