#!/usr/bin/env python3
"""Build a minimal local release archive; optionally verify private-prefix install.

The application source commit must be supplied explicitly: a working tree's HEAD
cannot establish which source produced an existing executable. release.json
records the actual binary and packager digests; it never contains its own digest
or the enclosing archive's digest.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INLINE_LINK = re.compile(
    r'(?P<prefix>!?\[[^\]\n]*\]\(\s*)'
    r'(?P<url><[^>\n]+>|[^\s)]+)'
    r'(?P<tail>(?:\s+(?:"[^"\n]*"|\'[^\'\n]*\'|\([^)\n]*\)))?\s*\))'
)
REFERENCE_LINK = re.compile(r'^(?P<prefix> {0,3}\[[^\]\n]+\]:\s*)(?P<url><[^>\n]+>|\S+)')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(65536):
            result.update(block)
    return result.hexdigest()


def git_output(*arguments):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def absolute_markdown(text, source_name, repository):
    """Rewrite repository-local Markdown destinations, leaving source files alone."""
    rewrites = 0
    in_fence = None
    output = []

    def destination(raw, image=False):
        nonlocal rewrites
        enclosed = raw.startswith("<") and raw.endswith(">")
        target = raw[1:-1] if enclosed else raw
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return raw
        require("\\" not in target, "Backslash Markdown destination is ambiguous")
        decoded = unquote(parsed.path)
        if not decoded:
            relative = source_name
        elif decoded.startswith("/"):
            relative = posixpath.normpath(decoded.lstrip("/"))
        else:
            relative = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_name), decoded)
            )
        require(
            relative not in ("", ".", "..") and not relative.startswith("../"),
            "Markdown destination escapes the repository",
        )
        if image:
            owner_repo = urlsplit(repository).path.strip("/")
            base = "https://raw.githubusercontent.com/" + owner_repo + "/main/"
        else:
            is_directory = decoded.endswith("/") or (ROOT / relative).is_dir()
            base = repository + ("/tree/main/" if is_directory else "/blob/main/")
        absolute = base + quote(relative, safe="/")
        split = urlsplit(absolute)
        absolute = urlunsplit(
            (split.scheme, split.netloc, split.path, parsed.query, parsed.fragment)
        )
        rewrites += 1
        return "<" + absolute + ">" if enclosed else absolute

    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if in_fence is None:
                in_fence = run
            elif run[0] == in_fence[0] and len(run) >= len(in_fence):
                in_fence = None
            output.append(line)
            continue
        if in_fence is not None or line.startswith("    "):
            output.append(line)
            continue
        # A code-formatted link label is valid; only protect URLs inside code spans.
        code_spans = [(m.start(), m.end()) for m in re.finditer(r"(`+).*?\1", line)]

        def inline(match):
            if any(start <= match.start("url") < end for start, end in code_spans):
                return match.group(0)
            return (
                match.group("prefix")
                + destination(match.group("url"), match.group("prefix").startswith("!"))
                + match.group("tail")
            )

        rewritten = INLINE_LINK.sub(inline, line)
        rewritten = REFERENCE_LINK.sub(
            lambda match: match.group("prefix") + destination(match.group("url")),
            rewritten,
        )
        output.append(rewritten)
    return "".join(output), rewrites


def dynamic_libraries(binary):
    if platform.system() != "Linux":
        return None
    result = subprocess.run(
        ["readelf", "--dynamic", str(binary)], capture_output=True, text=True, check=False
    )
    require(result.returncode == 0, "readelf could not inspect the Linux executable")
    return sorted(set(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", result.stdout)))


def glibc_requirements(binary):
    if platform.system() != "Linux":
        return None
    result = subprocess.run(
        ["readelf", "--version-info", str(binary)], capture_output=True, text=True, check=False
    )
    require(result.returncode == 0, "readelf could not inspect ELF symbol versions")
    versions = sorted(
        set(re.findall(r"Name:\s+GLIBC_([0-9]+(?:\.[0-9]+)+)", result.stdout)),
        key=lambda value: tuple(int(part) for part in value.split(".")),
    )
    return {"minimum_version": versions[-1] if versions else None,
            "required_symbol_versions": versions}


def native_gui_requirements(libraries):
    """Describe the packaged renderer without imposing unused GPU dependencies."""
    system = platform.system()
    if system == "Linux":
        if libraries is None:
            return "Linux native GUI dependencies were not inspected."
        has_x11 = any(name.startswith("libX11.so") for name in libraries)
        has_opengl = any(
            name.startswith(("libGL.so", "libOpenGL.so", "libGLX.so", "libEGL.so",
                             "libGLESv1_CM.so", "libGLESv2.so"))
            for name in libraries
        )
        if not has_x11 and not has_opengl:
            return "No directly linked X11 or OpenGL GUI library detected in this Linux executable."
        window_system = "X11 or XWayland" if has_x11 else "a native window system"
        rendering = (
            "OpenGL rendering and a graphics driver"
            if has_opengl else
            "CPU software rendering, with no direct OpenGL ELF dependency"
        )
        fonts = []
        if any(name.startswith("libfreetype.so") for name in libraries):
            fonts.append("FreeType")
        if any(name.startswith("libfontconfig.so") for name in libraries):
            fonts.append("Fontconfig")
        font_note = " Font libraries: " + ", ".join(fonts) + "." if fonts else ""
        return "Linux " + window_system + "; " + rendering + "." + font_note
    if system == "Windows":
        return "GUI-enabled Windows builds use native Win32 (User32/GDI32) and OpenGL system libraries."
    if system == "Darwin":
        return "GUI-enabled macOS builds use AppKit, CoreText, CoreGraphics, Foundation and OpenGL frameworks."
    return "GUI-enabled builds use platform-native window and rendering libraries."


def runtime_dependencies(libraries, glibc):
    return {
        "bundled_runtimes": [],
        "linux_direct_elf_needed": libraries,
        "linux_direct_elf_minimum_glibc": glibc["minimum_version"] if glibc else None,
        "linux_direct_elf_glibc_symbol_versions": glibc["required_symbol_versions"] if glibc else None,
        "linux_glibc_scope": "Highest GLIBC_* version required directly by this executable; this is not a complete distribution compatibility test. Shared libraries and external helpers have their own requirements.",
        "native_gui": native_gui_requirements(libraries),
        "downloads": "System curl with HTTPS and its certificate store.",
        "archive_metadata_and_process_helpers": "Python 3 standard library, started on demand.",
        "horizon_signature_verification": "Optional system OpenSSL with Ed25519 verification support.",
        "linux_account_secrets": "secret-tool and a desktop Secret Service for account credential operations.",
        "minecraft": "A compatible system or separately downloaded managed Java runtime; game files are separate.",
        "horizon": "Separately downloaded official Horizon launcher and its legitimate game preparation.",
        "scope": "These dependencies and their files/processes are outside the launcher payload; only Linux x86_64 is accepted by this package-install test.",
    }


def safe_member(name, root_name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in name,
            "Unsafe archive path")
    require(len(path.parts) > 1 and path.parts[0] == root_name, "Unexpected archive root")
    require(str(path) == name and all(part not in ("", ".") for part in path.parts),
            "Non-canonical archive path")
    return path


def build_archive(target, root_name, payload):
    descriptor, temporary = tempfile.mkstemp(prefix=".zmcl-package-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        if target.name.endswith(".zip"):
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as archive:
                for name, entry in sorted(payload.items()):
                    info = zipfile.ZipInfo(root_name + "/" + name, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | entry["mode"]) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, entry["data"], compresslevel=9)
        else:
            with temporary.open("wb") as output:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output,
                                   compresslevel=9, mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w",
                                      format=tarfile.USTAR_FORMAT) as archive:
                        for name, entry in sorted(payload.items()):
                            info = tarfile.TarInfo(root_name + "/" + name)
                            info.size = len(entry["data"])
                            info.mode = entry["mode"]
                            info.mtime = 0
                            info.uid = info.gid = 0
                            info.uname = info.gname = ""
                            archive.addfile(info, io.BytesIO(entry["data"]))
                output.flush()
                os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def verified_contents(target, root_name, payload):
    contents = {}
    if target.name.endswith(".zip"):
        with zipfile.ZipFile(target) as archive:
            entries = archive.infolist()
            require(len(entries) == len(payload), "Unexpected ZIP entry count")
            for info in entries:
                path = safe_member(info.filename, root_name)
                name = str(PurePosixPath(*path.parts[1:]))
                require(name in payload and name not in contents, "Unexpected/duplicate ZIP file")
                mode = info.external_attr >> 16
                require(stat.S_ISREG(mode), "ZIP contains a non-regular entry")
                require(info.file_size == len(payload[name]["data"]), "ZIP file size mismatch")
                with archive.open(info) as source:
                    body = source.read(info.file_size + 1)
                require(sha256(body) == sha256(payload[name]["data"]), "ZIP file digest mismatch")
                contents[name] = body
    else:
        with tarfile.open(target, "r:gz") as archive:
            entries = archive.getmembers()
            require(len(entries) == len(payload), "Unexpected tar entry count")
            for info in entries:
                path = safe_member(info.name, root_name)
                name = str(PurePosixPath(*path.parts[1:]))
                require(name in payload and name not in contents, "Unexpected/duplicate tar file")
                require(info.isfile(), "Tar contains a link or non-regular entry")
                require(info.size == len(payload[name]["data"]), "Tar file size mismatch")
                require(info.mode == payload[name]["mode"], "Tar file mode mismatch")
                with archive.extractfile(info) as source:
                    body = source.read(info.size + 1)
                require(sha256(body) == sha256(payload[name]["data"]), "Tar file digest mismatch")
                contents[name] = body
    require(set(contents) == set(payload), "Archive file set mismatch")
    return contents


def verify_install(contents, payload, version):
    require(platform.system() == "Linux", "Private-prefix install acceptance is Linux-only")
    scratch = ROOT / ".test-data"
    scratch.mkdir(exist_ok=True)
    checks = {"performed": True, "gui_started": False, "game_started": False}
    with tempfile.TemporaryDirectory(prefix="package-install-", dir=scratch) as directory:
        temporary = Path(directory)
        extracted = temporary / "unpacked"
        extracted.mkdir()
        for name, body in contents.items():
            destination = extracted / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
            destination.chmod(payload[name]["mode"])
        installer = extracted / "scripts/install.py"
        commands = []

        def run(command, expected=0, label=""):
            result = subprocess.run(command, capture_output=True, text=True,
                                    check=False, timeout=30)
            item = {"operation": label, "exit_code": result.returncode,
                    "expected_exit_code": expected}
            if result.stdout:
                item["stdout"] = result.stdout.replace(str(temporary), "<private-test>")
            if result.stderr:
                item["stderr"] = result.stderr.replace(str(temporary), "<private-test>")
            commands.append(item)
            require(result.returncode == expected, "Install acceptance failed: " + label)
            return result

        prefix = temporary / "clean prefix"
        data_root = temporary / "simulated-game-data"
        run([sys.executable, str(installer), "install", "--prefix", str(prefix)],
            label="install")
        manifest = prefix / "share/zmcl/install.json"
        installed = json.loads(manifest.read_text())
        require(len(installed) == 3, "Unexpected installed-file manifest")
        installed_files = []
        for entry in installed:
            relative = entry["path"]
            require(relative in ("bin/zmcl", "share/icons/hicolor/scalable/apps/zmcl.svg",
                                 "share/applications/zmcl.desktop"),
                    "Installer manifest has an unexpected target")
            path = prefix / relative
            require(file_sha256(path) == entry["sha256"], "Installed digest mismatch")
            installed_files.append({"path": relative, "bytes": path.stat().st_size,
                                    "sha256": entry["sha256"]})
        installed_files.append({"path": "share/zmcl/install.json",
                                "bytes": manifest.stat().st_size,
                                "sha256": file_sha256(manifest)})
        version_result = run(
            [str(prefix / "bin/zmcl"), "--version"],
            label="installed-version",
        )
        require(re.search(r"^zmcl\s+" + re.escape(version) + r"(?:\s|$)",
                          version_result.stdout), "Installed version does not match the package")
        require((prefix / "bin/zmcl").read_bytes() == contents["zmcl"],
                "Installer changed the binary")
        markers = [
            prefix / "share/zmcl/instances/fixture/game/saves/world/level.dat",
            data_root / "instances/fixture/game/saves/world/level.dat",
        ]
        marker_bytes = b"SYNTHETIC GAME-DATA PRESERVATION FIXTURE; NOT NBT\n"
        for marker in markers:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(marker_bytes)
        run([sys.executable, str(installer), "uninstall", "--prefix", str(prefix)],
            label="clean-uninstall")
        require(all(not (prefix / item["path"]).exists() for item in installed_files),
                "Uninstall left an unchanged installed file")
        require(all(marker.read_bytes() == marker_bytes for marker in markers),
                "Uninstall changed simulated game data")
        checks["clean_install_uninstall"] = {
            "passed": True, "installed_regular_file_count": len(installed_files),
            "installed_regular_file_bytes": sum(item["bytes"] for item in installed_files),
            "installed_files": installed_files,
            "prefix_character_count": len(str(prefix)),
            "size_scope": "Binary, icon, desktop entry and install manifest; desktop size depends on the prefix.",
            "simulated_game_data_preserved_inside_and_outside_prefix": True,
        }

        modified_prefix = temporary / "modified prefix"
        run([sys.executable, str(installer), "install", "--prefix", str(modified_prefix)],
            label="install-for-modified-file")
        desktop = modified_prefix / "share/applications/zmcl.desktop"
        edited = desktop.read_bytes() + b"# User fixture edit: preserve this file\n"
        desktop.write_bytes(edited)
        modified_game = modified_prefix / "share/zmcl/instances/fixture/game/saves/world/level.dat"
        modified_game.parent.mkdir(parents=True)
        modified_game.write_bytes(marker_bytes)
        refusal = run(
            [sys.executable, str(installer), "uninstall", "--prefix", str(modified_prefix)],
            expected=1, label="modified-file-uninstall-refusal",
        )
        require("Modified files preserved:" in refusal.stderr
                and "share/applications/zmcl.desktop" in refusal.stderr,
                "Uninstall did not report the modified-file protection")
        require(desktop.read_bytes() == edited, "Uninstall removed or changed the edited file")
        require(modified_game.read_bytes() == marker_bytes, "Uninstall changed simulated game data")
        require((modified_prefix / "share/zmcl/install.json").is_file(),
                "Partial uninstall discarded the remaining-file record")
        require(not (modified_prefix / "bin/zmcl").exists()
                and not (modified_prefix / "share/icons/hicolor/scalable/apps/zmcl.svg").exists(),
                "Partial uninstall failed to remove unchanged installed files")
        checks["modified_file_protection"] = {
            "passed": True, "modified_file": "share/applications/zmcl.desktop",
            "modified_file_preserved": True, "install_manifest_retained": True,
            "unchanged_binary_and_icon_removed": True, "simulated_game_data_preserved": True,
        }
        checks["commands"] = commands
        checks["command_count"] = len(commands)
        checks["expected_protection_failures"] = 1
    checks["private_test_prefixes_cleaned"] = not temporary.exists()
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path,
                        default=Path("target/release/zmcl" + (".exe" if os.name == "nt" else "")))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--source-commit", help="Explicit application source identity for this binary")
    parser.add_argument("--expected-sha256", help="Refuse a different executable")
    parser.add_argument("--expected-binary-bytes", type=int)
    parser.add_argument("--verify-install", action="store_true",
                        help="Test install/version/uninstall in private Linux temporary prefixes")
    parser.add_argument("--report", type=Path, help="Write the same JSON result to this external file")
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]
    version = project["version"]
    repository = project["repository"].rstrip("/")
    require(re.fullmatch(r"[0-9][A-Za-z0-9.+-]{0,63}", version), "Unsafe package version")
    require(repository.startswith("https://github.com/"), "Expected the public GitHub repository")
    if args.source_commit:
        require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.source_commit),
                "Source commit must be a full hexadecimal identity")
        require(git_output("rev-parse", "--verify", args.source_commit + "^{commit}")
                == args.source_commit, "Declared source commit is not present in this repository")
    binary = args.binary.resolve(strict=True)
    require(binary.is_file(), "Binary must be a regular file")
    binary_data = binary.read_bytes()
    binary_digest = sha256(binary_data)
    if args.expected_sha256:
        require(binary_digest == args.expected_sha256.lower(), "Frozen binary SHA-256 mismatch")
    if args.expected_binary_bytes is not None:
        require(len(binary_data) == args.expected_binary_bytes, "Frozen binary size mismatch")
    system = platform.system().lower()
    machine = platform.machine().lower()
    require(re.fullmatch(r"[a-z0-9_-]+", system) and re.fullmatch(r"[a-z0-9_-]+", machine),
            "Unsafe package platform")
    platform_name = system + "-" + machine
    name = "zmcl-" + version + "-" + platform_name
    executable_name = "zmcl.exe" if os.name == "nt" else "zmcl"
    payload = {executable_name: {"data": binary_data, "mode": 0o755}}
    source_digests = {}
    link_counts = {}
    for source_name in ("README.md", "LICENSE", "docs/FEATURES.md",
                        "assets/zmcl.svg", "scripts/install.py"):
        source = ROOT / source_name
        body = source.read_bytes()
        source_digests[source_name] = sha256(body)
        if source_name.endswith(".md"):
            transformed, count = absolute_markdown(body.decode("utf-8"), source_name, repository)
            body = transformed.encode("utf-8")
            link_counts[source_name] = count
            repeated, extra = absolute_markdown(transformed, source_name, repository)
            require(extra == 0 and repeated == transformed,
                    "A relative Markdown destination survived package conversion")
        payload[source_name] = {"data": body, "mode": 0o755 if source_name.endswith(".py") else 0o644}
    script = Path(__file__).resolve()
    script_status = git_output("status", "--porcelain", "--", "scripts/package.py")
    packager = {
        "path": "scripts/package.py", "sha256": file_sha256(script),
        "git_head_at_packaging": git_output("rev-parse", "HEAD"),
        "working_tree_modified": bool(script_status) if script_status is not None else None,
        "identity_note": "The script digest identifies the executed packager; HEAD alone does not identify uncommitted script changes.",
    }
    release = {
        "schema": 1, "application": "zmcl", "version": version,
        "repository": repository, "app_source_commit": args.source_commit,
        "source_identity_basis": "Explicit --source-commit; never inferred from an existing executable.",
        "platform": platform_name, "platform_basis": "Packaging host; native executable required.",
        "binary": {"path": executable_name, "bytes": len(binary_data), "sha256": binary_digest},
        "packager": packager, "runtime_dependencies": runtime_dependencies(dynamic_libraries(binary), glibc_requirements(binary)),
        "markdown_links": {"repository_branch": "main", "rewritten_counts": link_counts},
        "files": [{"path": key, "bytes": len(value["data"]), "sha256": sha256(value["data"])}
                  for key, value in sorted(payload.items())],
        "manifest_scope": "files excludes release.json; archive and manifest digests are recorded outside this archive.",
    }
    payload["release.json"] = {
        "data": (json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
        "mode": 0o644,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / (name + (".zip" if os.name == "nt" else ".tar.gz"))
    build_archive(target, name, payload)
    digest = file_sha256(target)
    checksum = target.with_name(target.name + ".sha256")
    checksum.write_text(digest + "  " + target.name + "\n", encoding="ascii")
    require(checksum.read_text() == digest + "  " + target.name + "\n",
            "Checksum sidecar format mismatch")
    contents = verified_contents(target, name, payload)
    install_check = verify_install(contents, payload, version) if args.verify_install else {
        "performed": False
    }
    require(all(file_sha256(ROOT / path) == value for path, value in source_digests.items()),
            "A source payload file changed while packaging")
    require(file_sha256(binary) == binary_digest, "The binary changed while packaging")
    files = [{"path": key, "bytes": len(value["data"]), "sha256": sha256(value["data"])}
             for key, value in sorted(payload.items())]
    result = {
        "schema": 1, "passed": True, "archive": str(target), "sha256": digest,
        "bytes": target.stat().st_size, "binary_bytes": len(binary_data),
        "binary_sha256": binary_digest, "unpacked_bytes": sum(item["bytes"] for item in files),
        "unpacked_metric": "Sum of regular-file content bytes; excludes archive headers and filesystem allocation.",
        "file_count": len(files), "files": files, "app_source_commit": args.source_commit,
        "packager": packager, "platform": platform_name,
        "checks": {
            "archive_paths_confined": True, "regular_files_only": True,
            "file_sizes_and_sha256_match": True, "checksum_sidecar_verified": True,
            "source_documents_unchanged": True, "markdown_link_rewrites": link_counts,
            "no_relative_markdown_links_outside_code": True,
            "no_game_java_cache_screenshots_or_logs_bundled": True,
            "installation": install_check,
        },
        "runtime_dependencies": release["runtime_dependencies"],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("zmcl package:", str(error), file=sys.stderr)
        sys.exit(1)
