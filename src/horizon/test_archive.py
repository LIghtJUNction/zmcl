"""Offline regressions for Horizon release trust and portable extraction."""
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
import tarfile

spec = importlib.util.spec_from_file_location("horizon_archive", Path(__file__).with_name("archive.py"))
horizon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(horizon)


def elf():
    body = bytearray(128)
    body[:6] = b"\x7fELF\x02\x01"
    body[18:20] = b"\x3e\x00"
    return bytes(body)


class HorizonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zmcl-horizon-test-")
        self.root = Path(self.temporary.name)
        self.payload = self.root / "payload"
        self.payload.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def tar(self, name="horizon-launcher-linux-x86_64.tar.gz", entries=None, format=tarfile.USTAR_FORMAT):
        target = self.root / name
        with tarfile.open(target, "w:gz", format=format) as archive:
            for path, content, type in entries or [("horizon-launcher", elf(), tarfile.REGTYPE)]:
                item = tarfile.TarInfo(path)
                item.type = type
                item.mode = 0o755
                item.size = len(content) if type == tarfile.REGTYPE else 0
                if type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    item.linkname = "../../outside"
                archive.addfile(item, io.BytesIO(content) if item.size else None)
        return target

    def manifest(self, proof, platform="linux-x86_64"):
        value = dict(proof, provider="horizon-rust/horizon", component="launcher",
                     platform=platform, asset=horizon.ASSETS[platform])
        path = self.root / "installation.json"
        path.write_text(json.dumps(value))
        return path

    def test_linux_extract_and_installed_integrity(self):
        archive = self.tar()
        proof = horizon.extract_archive(archive, self.payload, "linux-x86_64")
        self.assertEqual(proof["entrypoint"], "horizon-launcher")
        self.assertEqual(proof["entry_sha256"], hashlib.sha256(elf()).hexdigest())
        manifest = self.manifest(proof)
        self.assertTrue(horizon.check_install(manifest, self.payload)["verified"])
        (self.payload / "horizon-launcher").write_bytes(elf() + b"tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            horizon.check_install(manifest, self.payload)

    def test_windows_and_macos_portable_entrypoints(self):
        for platform, name, contents in [
            ("windows-x64", "horizon-launcher.exe", b"MZ" + bytes(126)),
            ("windows-arm64", "horizon-launcher.exe", b"MZ" + bytes(126)),
            ("macos-arm64", "Horizon.app/Contents/MacOS/horizon-launcher", b"\xcf\xfa\xed\xfe" + bytes(124)),
            ("macos-x64", "Horizon.app/Contents/MacOS/horizon-launcher", b"\xcf\xfa\xed\xfe" + bytes(124)),
        ]:
            target = self.root / horizon.ASSETS[platform]
            output = self.root / platform
            output.mkdir()
            with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(name, contents)
            proof = horizon.extract_archive(target, output, platform)
            self.assertEqual(proof["entrypoint"], name)

    def test_tar_paths_links_special_and_extended_headers_rejected(self):
        cases = [
            ("../escape", b"x", tarfile.REGTYPE),
            ("/escape", b"x", tarfile.REGTYPE),
            ("C:/escape", b"x", tarfile.REGTYPE),
            ("bad\\path", b"x", tarfile.REGTYPE),
            ("horizon-launcher", b"", tarfile.SYMTYPE),
            ("horizon-launcher", b"", tarfile.LNKTYPE),
            ("horizon-launcher", b"", tarfile.FIFOTYPE),
            ("horizon-launcher", b"", tarfile.CHRTYPE),
        ]
        for index, entry in enumerate(cases):
            archive = self.tar(name="bad" + str(index) + ".tar.gz", entries=[entry])
            with self.assertRaises(ValueError):
                horizon.extract_archive(archive, self.payload, "linux-x86_64")
            self.assertFalse((self.root / "escape").exists())
        long_archive = self.tar("pax.tar.gz", [("a" * 200, b"x", tarfile.REGTYPE)], tarfile.PAX_FORMAT)
        with self.assertRaisesRegex(ValueError, "extended metadata"):
            horizon.extract_archive(long_archive, self.payload, "linux-x86_64")

    def test_zip_links_paths_aliases_and_bombs_rejected(self):
        cases = [
            [("../escape", b"x"), ("horizon-launcher.exe", b"MZ")],
            [("config/Caf\u00e9", b"x"), ("config/Cafe\u0301", b"y")],
            [("DIR/a", b"x"), ("dir/b", b"y")],
            [("CON", b"x")],
        ]
        for index, entries in enumerate(cases):
            target = self.root / ("bad" + str(index) + ".zip")
            stage = self.root / ("bad" + str(index))
            stage.mkdir()
            with zipfile.ZipFile(target, "w") as archive:
                for path, content in entries:
                    archive.writestr(path, content)
            with self.assertRaises(ValueError):
                horizon.extract_archive(target, stage, "windows-x64")
        target = self.root / "link.zip"
        info = zipfile.ZipInfo("horizon-launcher.exe")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(info, "/outside")
        with self.assertRaisesRegex(ValueError, "links"):
            horizon.extract_archive(target, self.payload, "windows-x64")
        target = self.root / "bomb.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.writestr("horizon-launcher.exe", b"\0" * 2_000_000)
        with self.assertRaisesRegex(ValueError, "ratio"):
            horizon.extract_archive(target, self.payload, "windows-x64")

    def test_tar_declared_size_and_nonempty_stage_rejected(self):
        header = tarfile.TarInfo("horizon-launcher")
        header.size = horizon.MAX_FILE + 1
        archive = self.root / "oversized.tar.gz"
        with gzip.open(archive, "wb") as output:
            output.write(header.tobuf(format=tarfile.USTAR_FORMAT))
        with self.assertRaisesRegex(ValueError, "limits"):
            horizon.extract_archive(archive, self.payload, "linux-x86_64")
        (self.payload / "user-file").write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "empty"):
            horizon.extract_archive(self.tar(), self.payload, "linux-x86_64")
        self.assertEqual((self.payload / "user-file").read_bytes(), b"keep")

    def test_zip_directory_allocation_budget(self):
        target = self.root / "large-directory.zip"
        target.write_bytes(struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, horizon.MAX_INDEX + 1, 0, 0))
        with self.assertRaisesRegex(ValueError, "memory budget"):
            horizon.check_zip_directory(target)

    def test_unmanifested_installed_files_rejected(self):
        proof = horizon.extract_archive(self.tar(), self.payload, "linux-x86_64")
        manifest = self.manifest(proof)
        (self.payload / "injected.dll").write_bytes(b"unlisted")
        with self.assertRaisesRegex(ValueError, "unmanifested"):
            horizon.check_install(manifest, self.payload)

    def test_official_signed_checksum_fixture(self):
        fixtures = Path(__file__).with_name("fixtures")
        sums = (fixtures / "launcher-0.2.84.SHA256SUMS").read_bytes()
        signature = (fixtures / "launcher-0.2.84.SHA256SUMS.sig").read_bytes()
        digest = "9580c1017fc0aefe5baeabfb0c513746ed262a7b5dd53104915f3cabfa9ab0f9"
        self.assertEqual(horizon.signed_hash("launcher@0.2.84", sums, signature,
            "horizon-launcher-linux-x86_64.tar.gz", digest, self.root), digest)
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            horizon.signed_hash("launcher@0.2.85", sums, signature,
                "horizon-launcher-linux-x86_64.tar.gz", digest, self.root)
        with self.assertRaisesRegex(ValueError, "disagrees"):
            horizon.signed_hash("launcher@0.2.84", sums, signature,
                "horizon-launcher-linux-x86_64.tar.gz", "a" * 64, self.root)

    def test_pinned_key_rejects_another_signer_and_checksum_tampering(self):
        key = self.root / "ephemeral.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        public = subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
                                check=True, capture_output=True).stdout
        expected = "a" * 64
        sums = (expected + "  horizon-launcher-linux-x86_64.tar.gz\n").encode()
        message = self.root / "message"
        message.write_bytes(b"horizon release launcher@0.2.84\n" + sums)
        signature = self.root / "signature"
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin",
                        "-in", str(message), "-out", str(signature)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        encoded = base64.b64encode(signature.read_bytes()) + b"\n"
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            horizon.signed_hash("launcher@0.2.84", sums, encoded,
                "horizon-launcher-linux-x86_64.tar.gz", expected, self.root)
        public_key = base64.b64encode(public[-32:]).decode()
        horizon.signed_hash("launcher@0.2.84", sums, encoded,
            "horizon-launcher-linux-x86_64.tar.gz", expected, self.root, keys=(public_key,))
        changed = sums.replace(b"a" * 64, b"b" * 64)
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            horizon.signed_hash("launcher@0.2.84", changed, encoded,
                "horizon-launcher-linux-x86_64.tar.gz", "b" * 64, self.root, keys=(public_key,))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            horizon.signed_hash("launcher@0.2.84", sums + sums, encoded,
                "horizon-launcher-linux-x86_64.tar.gz", expected, self.root, keys=(public_key,))

    def test_missing_openssl_fails_closed(self):
        fixtures = Path(__file__).with_name("fixtures")
        sums = (fixtures / "launcher-0.2.84.SHA256SUMS").read_bytes()
        signature = (fixtures / "launcher-0.2.84.SHA256SUMS.sig").read_bytes()
        with mock.patch.object(horizon.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, "requires system OpenSSL"):
                horizon.signed_hash("launcher@0.2.84", sums, signature,
                    "horizon-launcher-linux-x86_64.tar.gz",
                    "9580c1017fc0aefe5baeabfb0c513746ed262a7b5dd53104915f3cabfa9ab0f9", self.root)


if __name__ == "__main__":
    unittest.main()
