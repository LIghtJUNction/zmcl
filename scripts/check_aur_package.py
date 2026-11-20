#!/usr/bin/env python3
"""Verify an Arch package's installed payload in an isolated staging directory."""
import argparse
import hashlib
import json
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
}
METADATA = {".PKGINFO", ".BUILDINFO", ".MTREE"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, **kwargs):
    return subprocess.run(
        command, check=True, text=True, capture_output=True, timeout=30, **kwargs
    ).stdout


def verify(package):
    members = run(["bsdtar", "-tf", str(package)]).splitlines()
    files = {name.removeprefix("./") for name in members if not name.endswith("/")}
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
        manifest = json.loads((root / "usr/share/doc/zmcl/release.json").read_text())
        binary = root / "usr/bin/zmcl"
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        require(digest == manifest["binary"]["sha256"], "Official binary digest changed")
        require(binary.stat().st_size == manifest["binary"]["bytes"], "Binary size changed")
        require(metadata["pkgver"][0].startswith(manifest["version"] + "-"),
                "Package and executable release versions differ")
        output = run([str(binary), "--version"]).strip()
        require(output.split()[:2] == ["zmcl", manifest["version"]], "Wrong executable version")
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
