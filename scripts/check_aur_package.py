#!/usr/bin/env python3
"""Verify an Arch package's installed payload in an isolated staging directory."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile

PAYLOAD = {
    "usr/bin/zmcl",
    "usr/share/applications/zmcl.desktop",
    "usr/share/icons/hicolor/scalable/apps/zmcl.svg",
    "usr/share/licenses/zmcl-bin/LICENSE",
    "usr/share/doc/zmcl/README.md",
    "usr/share/doc/zmcl/release.json",
    "usr/share/doc/zmcl/FEATURES.md",
    "usr/share/zmcl/distribution.json",
}
METADATA = {".PKGINFO", ".BUILDINFO", ".MTREE"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, **kwargs):
    return subprocess.run(
        command, check=True, text=True, capture_output=True, timeout=30, **kwargs
    ).stdout


def read_json(path, limit=4 * 1024 * 1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit,
            "Missing or oversized JSON metadata")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_distribution(manifest, installed, package_version):
    """Compare the source portable hint with the Arch hint, not their byte hashes."""
    original = manifest.get("distribution")
    fields = {"schema", "application", "version", "format", "packager",
              "packager_version", "packager_commit", "release_url"}
    require(isinstance(original, dict) and set(original) == fields,
            "Missing or invalid portable release distribution metadata")
    version = manifest.get("version")
    require(isinstance(version, str) and re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version),
        "Invalid application release version")
    require(type(original["schema"]) is int and original["schema"] == 1
            and original["application"] == "zmcl" and original["format"] == "portable"
            and original["packager"] == "zdistro" and original["version"] == version
            and original["release_url"] == "https://github.com/LIghtJUNction/zmcl/releases/tag/v" + version
            and isinstance(original["packager_version"], str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?",
                             original["packager_version"])
            and isinstance(original["packager_commit"], str)
            and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", original["packager_commit"]),
            "Invalid portable release distribution identity")
    require(isinstance(package_version, str), "Missing Arch package version")
    upstream, separator, release = package_version.rpartition("-")
    require(separator and upstream == version and re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", release),
            "Package and distribution release versions differ")
    require(isinstance(installed, dict) and type(installed.get("schema")) is int
            and installed == {**original, "format": "arch"},
            "Arch marker must preserve every portable field except format")
    return original


def verify(package):
    members = run(["bsdtar", "-tf", str(package)]).splitlines()
    names = [name[2:] if name.startswith("./") else name for name in members if not name.endswith("/")]
    files = set(names)
    require(len(names) == len(files), "Duplicate package file entries")
    require(files == PAYLOAD | METADATA, "Unexpected package file set")
    info = run(["bsdtar", "-xOf", str(package), ".PKGINFO"])
    metadata = {}
    for line in info.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            metadata.setdefault(key, []).append(value)
    require(metadata.get("pkgname") == ["zmcl-bin"], "Unexpected package name")
    require(metadata.get("arch") == ["x86_64"], "Unexpected package architecture")
    with tempfile.TemporaryDirectory(prefix="zmcl-arch-") as directory:
        root = Path(directory)
        run(["bsdtar", "-xf", str(package), "-C", str(root)])
        for name in PAYLOAD:
            path = root / name
            require(path.is_file() and not path.is_symlink(), "Non-regular package payload")
        manifest = read_json(root / "usr/share/doc/zmcl/release.json")
        marker_path = root / "usr/share/zmcl/distribution.json"
        marker = read_json(marker_path, 8192)
        require(len(metadata.get("pkgver", [])) == 1, "Missing or duplicate package version")
        original_distribution = verify_distribution(manifest, marker, metadata["pkgver"][0])
        binary = root / "usr/bin/zmcl"
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        require(digest == manifest["binary"]["sha256"], "Official binary digest changed")
        require(binary.stat().st_size == manifest["binary"]["bytes"], "Binary size changed")
        output = run([str(binary), "--version"]).strip()
        require(output == "zmcl " + marker["version"], "Wrong executable version")
        desktop = root / "usr/share/applications/zmcl.desktop"
        run(["desktop-file-validate", str(desktop)])
        text = desktop.read_text()
        require("Exec=zmcl gui\n" in text and "Icon=zmcl\n" in text,
                "Desktop entry does not launch the packaged application")
        linked = run(["ldd", str(binary)])
        require("not found" not in linked, "An ELF runtime dependency is missing")
        checks = {
            "exact_package_file_set": True,
            "regular_payload_files": True,
            "official_binary_sha256_preserved": True,
            "portable_release_distribution_preserved": True,
            "arch_marker_differs_only_in_format": True,
            "distribution_version_matches_package_and_executable": True,
            "installed_layout_version_executed": True,
            "desktop_entry_valid": True,
            "runtime_libraries_resolved_on_test_host": True,
        }
        return {
            "schema": 1,
            "passed": True,
            "package": package.name,
            "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            "pkgver": metadata["pkgver"][0],
            "arch": metadata["arch"][0],
            "binary_bytes": binary.stat().st_size,
            "binary_sha256": digest,
            "app_source_commit": manifest["app_source_commit"],
            "source_distribution": original_distribution,
            "installed_distribution": marker,
            "installed_distribution_sha256": hashlib.sha256(marker_path.read_bytes()).hexdigest(),
            "distribution_scope": "The original release.json describes the portable source archive. "
                                  "Its portable marker digest is not the installed Arch marker digest; "
                                  "only the format field is converted to arch.",
            "payload_file_count": len(PAYLOAD),
            "checks": checks,
            "scope": (
                "Extracted installed filesystem layout in a temporary directory; "
                "executed its binary and validated the desktop file. "
                "This check does not modify the host pacman database or start the GUI."
            ),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.package.resolve(strict=True))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(body)
    print(body, end="")


if __name__ == "__main__":
    main()
