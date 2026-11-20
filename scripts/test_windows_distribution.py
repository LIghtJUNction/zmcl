#!/usr/bin/env python3
"""Portable contract checks for the manual Windows workflow helper.

These tests do not claim Windows, MSVC, installer or GUI runtime acceptance.
The real workflow must execute those gates on a Windows runner.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile
import zlib

SPEC = importlib.util.spec_from_file_location(
    "windows_distribution", Path(__file__).with_name("windows_distribution.py"))
windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(windows)


class WindowsDistributionContracts(unittest.TestCase):
    def test_source_input_requires_an_immutable_commit_not_shell_text(self):
        self.assertEqual(windows.valid_commit("a" * 40), "a" * 40)
        for value in ("main", "a" * 39, "A" * 40, "a" * 40 + "\n", "a" * 40 + "; echo injected"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows.valid_commit(value)

    def test_toolchain_comes_from_source_and_rejects_floating_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "rust-toolchain.toml"
            file.write_text('[toolchain]\nchannel="1.100.0"\nprofile="minimal"\n'
                            'components=["clippy","rustfmt"]\n')
            self.assertEqual(windows.toolchain(root), {
                "channel": "1.100.0", "components": "clippy,rustfmt",
                "target": "x86_64-pc-windows-msvc"})
            for channel in ("stable", "nightly", "1.100", "1.100.0 --bad"):
                file.write_text('[toolchain]\nchannel=' + json.dumps(channel) + "\n")
                with self.subTest(channel=channel), self.assertRaisesRegex(ValueError, "fixed Rust"):
                    windows.toolchain(root)

    def test_windows_asset_cannot_be_relabeled_from_linux_or_x86_64_alias(self):
        for name in ("zmcl-7.8.9-linux-x86_64.zip", "zmcl-7.8.9-windows-x86_64.zip"):
            report = {"version": "7.8.9", "platform": "windows-amd64",
                      "requested_targets": ["zip"], "skipped_targets": [],
                      "artifacts": [{"target": "zip", "file": name}]}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "windows-amd64"):
                windows.portable_roundtrip(Path("unused"), report, None, {})

    def test_direct_import_check_rejects_external_visual_cpp_runtime(self):
        imports = windows.pe_dependencies("KERNEL32.dll\nUSER32.dll\nmsvcp_win.dll\n")
        self.assertIn("KERNEL32.dll", imports["direct_dll_imports"])
        for name in ("VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "MSVCP140.dll"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "external MSVC"):
                windows.pe_dependencies("KERNEL32.dll\n" + name + "\n")
        with self.assertRaisesRegex(ValueError, "did not report"):
            windows.pe_dependencies("failed to read executable")

    def test_failure_evidence_never_becomes_a_successful_gui_check(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = windows.Evidence(Path(directory) / "evidence", "a" * 40)
            def unavailable():
                raise RuntimeError("No interactive desktop")
            with self.assertRaisesRegex(RuntimeError, "No interactive desktop"):
                evidence.check("native_gui", unavailable)
            saved = json.loads((evidence.output / "windows-acceptance.json").read_text())
            self.assertFalse(saved["passed"])
            self.assertFalse(saved["published"])
            self.assertFalse(saved["signed"])
            self.assertEqual(saved["checks"]["native_gui"]["status"], "failed")

    def test_timed_out_command_preserves_its_partial_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = windows.Evidence(Path(directory) / "evidence", "a" * 40)
            timeout = subprocess.TimeoutExpired(["synthetic"], 1, b"partial stdout", b"partial stderr")
            with patch.object(windows.subprocess, "run", side_effect=timeout):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    evidence.command("synthetic", ["synthetic"], timeout=1)
            self.assertEqual((evidence.output / "01-synthetic.stdout.txt").read_text(), "partial stdout")
            self.assertEqual((evidence.output / "01-synthetic.stderr.txt").read_text(), "partial stderr")

    def test_installed_manifest_requires_all_five_paths_and_exact_digest_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            for name in sorted(windows.MANAGED):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic installed fixture")
                entries.append({"path": name, "sha256": windows.sha256(path)})
            manifest = root / "share/zmcl/install.json"
            manifest.write_text(json.dumps(entries))
            self.assertEqual(windows.verified_managed_paths(root), sorted(windows.MANAGED))
            mutations = {
                "missing-desktop": [item for item in entries if item["path"] != "share/applications/zmcl.desktop"],
                "unknown-field": [{**item, "unknown": True} for item in entries],
                "wrong-digest": [{**item, "sha256": "0" * 64} for item in entries],
                "duplicate": entries + [entries[0]],
            }
            for name, changed in mutations.items():
                manifest.write_text(json.dumps(changed))
                with self.subTest(name=name), self.assertRaises(ValueError):
                    windows.verified_managed_paths(root)

    def test_png_encoder_keeps_actual_pixels_and_valid_chunk_checksums(self):
        rgb = bytearray([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
        encoded = windows.png_rgb(2, 2, rgb)
        self.assertTrue(encoded.startswith(b"\x89PNG\r\n\x1a\n"))
        offset, chunks = 8, {}
        while offset < len(encoded):
            length = struct.unpack(">I", encoded[offset:offset + 4])[0]
            kind = encoded[offset + 4:offset + 8]
            data = encoded[offset + 8:offset + 8 + length]
            checksum = struct.unpack(">I", encoded[offset + 8 + length:offset + 12 + length])[0]
            self.assertEqual(checksum, zlib.crc32(kind + data) & 0xffffffff)
            chunks[kind] = data
            offset += 12 + length
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\0" + bytes(rgb[:6]) + b"\0" + bytes(rgb[6:]))
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (2, 2, 8, 2, 0, 0, 0))


class WindowsZipContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prefix = "zmcl-7.8.9-windows-amd64"
        marker = {"schema": 1, "application": "zmcl", "version": "7.8.9", "format": "portable",
                  "packager": "zdistro", "packager_version": "0.1.0",
                  "packager_commit": "6a54a137fc697d372b54ef85105315f2ce522b3d",
                  "release_url": "https://github.com/LIghtJUNction/zmcl/releases/tag/v7.8.9"}
        self.payload = {name: b"SYNTHETIC ZIP CONTRACT FIXTURE\n" for name in windows.PAYLOAD}
        self.payload["share/zmcl/distribution.json"] = json.dumps(marker).encode()
        binary = self.payload["zmcl.exe"]
        self.binary_sha = hashlib.sha256(binary).hexdigest()
        release = {
            "version": "7.8.9", "app_source_commit": "a" * 40,
            "binary": {"path": "zmcl.exe", "bytes": len(binary), "sha256": self.binary_sha},
            "distribution": marker,
            "files": [{"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                      for name, body in self.payload.items() if name != "release.json"],
        }
        self.payload["release.json"] = json.dumps(release).encode()

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, changes=None):
        entries = [(self.prefix + "/" + name, body, stat.S_IFREG | 0o644)
                   for name, body in sorted(self.payload.items())]
        if changes:
            entries = changes(entries)
        path = self.root / "fixture.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w") as archive:
                for name, body, mode in entries:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = mode << 16
                    archive.writestr(info, body)
        return path

    def test_complete_zip_has_independent_release_and_marker_identity(self):
        destination = windows.extract_zip(self.archive(), self.root / "extracted", self.prefix)
        marker = windows.verify_extracted(destination, "7.8.9", self.binary_sha, "a" * 40)
        self.assertEqual(marker["format"], "portable")
        self.assertEqual(marker["version"], "7.8.9")

    def test_bad_zip_layout_is_rejected_before_any_extraction(self):
        mutations = {
            "traversal": lambda rows: rows + [(self.prefix + "/../escape", b"bad", stat.S_IFREG | 0o644)],
            "duplicate": lambda rows: rows + [rows[0]],
            "symlink": lambda rows: [(rows[0][0], b"elsewhere", stat.S_IFLNK | 0o777)] + rows[1:],
            "missing": lambda rows: rows[1:],
            "wrong-root": lambda rows: [("wrong/" + name.split("/", 1)[1], body, mode)
                                       for name, body, mode in rows],
        }
        for name, mutation in mutations.items():
            destination = self.root / ("extract-" + name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                windows.extract_zip(self.archive(mutation), destination, self.prefix)
            self.assertFalse(destination.exists())

    def test_changed_payload_and_boolean_schema_are_not_accepted(self):
        destination = windows.extract_zip(self.archive(), self.root / "extracted", self.prefix)
        (destination / "README.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "differs from release.json"):
            windows.verify_extracted(destination, "7.8.9", self.binary_sha, "a" * 40)
        (destination / "README.md").write_bytes(self.payload["README.md"])
        marker = json.loads((destination / "share/zmcl/distribution.json").read_text())
        marker["schema"] = True
        body = json.dumps(marker).encode()
        (destination / "share/zmcl/distribution.json").write_bytes(body)
        release = json.loads((destination / "release.json").read_text())
        release["distribution"] = marker
        for item in release["files"]:
            if item["path"] == "share/zmcl/distribution.json":
                item.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        (destination / "release.json").write_text(json.dumps(release))
        with self.assertRaisesRegex(ValueError, "marker identity"):
            windows.verify_extracted(destination, "7.8.9", self.binary_sha, "a" * 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
