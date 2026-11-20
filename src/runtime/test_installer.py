"""Installer metadata uses real ZIP fixtures and never extracts their contents."""
import importlib.util
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

SPEC = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("installer.py"))
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jar = self.root / "installer.jar"

    def write(self, members):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.jar, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in members:
                    archive.writestr(name, data)

    def test_reads_exact_resource_without_extracting_siblings(self):
        metadata = b'{"json":"/version.json"}'
        version = b'{"id":"1.21.1-forge-52.1.2","inheritsFrom":"1.21.1"}'
        self.write([
            ("install_profile.json", metadata),
            ("version.json", version),
            ("nested/version.json", b'{"id":"wrong"}'),
            ("../outside", b"never extracted"),
        ])
        self.assertEqual(installer.read_metadata(self.jar, "install_profile.json"), metadata)
        self.assertEqual(installer.read_metadata(self.jar, "version.json"), version)
        self.assertEqual(list(self.root.iterdir()), [self.jar])

    def test_preserves_duplicate_json_members_for_rust_validation(self):
        data = b'{"id":"selected","\\u0069d":"wrong","inheritsFrom":"1.21.1"}'
        self.write([("version.json", data)])
        self.assertEqual(installer.read_metadata(self.jar, "version.json"), data)

    def test_missing_and_duplicate_metadata_entries_are_errors(self):
        self.write([("version.json", b"{}")])
        with self.assertRaisesRegex(ValueError, "missing or has duplicate"):
            installer.read_metadata(self.jar, "install_profile.json")
        self.write([("version.json", b"{}"), ("version.json", b"{\"id\":\"wrong\"}")])
        with self.assertRaisesRegex(ValueError, "missing or has duplicate"):
            installer.read_metadata(self.jar, "version.json")

    def test_unsafe_resource_references_are_rejected(self):
        self.write([("version.json", b"{}")])
        for name in ("", "../version.json", "/version.json", "a//version.json",
                     "a/./version.json", "C:/version.json", "a\\version.json",
                     "version.json\0", "NUL", "a/COM1.json", "a./version.json",
                     "v" * 4097):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "resource path"):
                installer.read_metadata(self.jar, name)

    def test_metadata_size_limit_is_checked_before_decompression(self):
        self.write([("version.json", b" " * (installer.MAX_METADATA + 1))])
        with mock.patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("must not decompress")):
            with self.assertRaisesRegex(ValueError, "16 MiB"):
                installer.read_metadata(self.jar, "version.json")
        self.write([("version.json", b"")])
        with self.assertRaisesRegex(ValueError, "empty"):
            installer.read_metadata(self.jar, "version.json")

    def test_symlink_and_special_metadata_members_are_rejected(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR):
            with self.subTest(kind=kind):
                member = zipfile.ZipInfo("version.json")
                member.create_system = 3
                member.external_attr = (kind | 0o600) << 16
                self.write([(member, b"{}")])
                with self.assertRaisesRegex(ValueError, "regular ZIP file"):
                    installer.read_metadata(self.jar, "version.json")

    def test_large_entry_table_is_rejected_before_zipfile_allocation(self):
        self.write([("file-%d" % number, b"") for number in range(installer.MAX_ENTRIES + 1)])
        with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("must not allocate entries")):
            with self.assertRaisesRegex(ValueError, "central directory"):
                installer.read_metadata(self.jar, "version.json")

    def test_zip64_cannot_override_bounded_classic_directory_before_allocation(self):
        self.write([("version.json", b"{}"), ("extra-one", b"{}"), ("extra-two", b"{}")])
        data = self.jar.read_bytes()
        position = len(data) - 22
        fields = list(struct.unpack_from("<4s4H2LH", data, position))
        count, directory_size, offset = fields[4:7]
        self.assertEqual(directory_size, 168)
        zip64 = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45,
                            0, 0, count, count, directory_size, offset)
        locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, position, 1)
        fields[3:6] = [1, 1, 46]
        self.jar.write_bytes(data[:position] + zip64 + locator
                             + struct.pack("<4s4H2LH", *fields))
        # Python accepts this archive and uses the larger ZIP64 values, despite
        # the classic record advertising one entry and only 46 directory bytes.
        with zipfile.ZipFile(self.jar) as archive:
            self.assertEqual(len(archive.infolist()), 3)
            self.assertEqual(archive.read("version.json"), b"{}")
        with mock.patch.object(installer, "MAX_CENTRAL", 64):
            with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("must not allocate entries")):
                with self.assertRaisesRegex(ValueError, "ZIP64"):
                    installer.read_metadata(self.jar, "version.json")

    def test_invalid_directory_bounds_and_multidisk_archives_are_rejected(self):
        for field, value in ((1, 1), (4, 65535), (5, installer.MAX_CENTRAL + 1), (6, 0xffffffff)):
            with self.subTest(field=field):
                self.write([("version.json", b"{}")])
                data = bytearray(self.jar.read_bytes())
                fields = list(struct.unpack_from("<4s4H2LH", data, len(data) - 22))
                fields[field] = value
                struct.pack_into("<4s4H2LH", data, len(data) - 22, *fields)
                self.jar.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "central directory"):
                    installer.read_metadata(self.jar, "version.json")

    def test_archive_size_and_end_record_are_bounded(self):
        self.jar.write_bytes(b"truncated")
        with self.assertRaisesRegex(ValueError, "truncated"):
            installer.read_metadata(self.jar, "version.json")
        with self.jar.open("wb") as stream:
            stream.truncate(installer.MAX_ARCHIVE + 1)
        with self.assertRaisesRegex(ValueError, "512 MiB"):
            installer.read_metadata(self.jar, "version.json")
        self.jar.write_bytes(b"not-a-valid-archive" * 4)
        with self.assertRaisesRegex(ValueError, "end record"):
            installer.read_metadata(self.jar, "version.json")

    def test_cli_keeps_json_bytes_and_returns_useful_errors(self):
        version = b'{"id":"neoforge-21.1.2","inheritsFrom":"1.21.1"}'
        self.write([("version.json", version)])
        command = [sys.executable, "-I", "-S", "-X", "utf8",
                   str(Path(installer.__file__)), str(self.jar)]
        result = subprocess.run(command + ["version.json"], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, version)
        self.assertEqual(result.stderr, b"")
        result = subprocess.run(command + ["missing.json"], capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"metadata is missing", result.stderr)


if __name__ == "__main__":
    unittest.main()
