"""Local synthetic-world tests; never launches Minecraft or accesses the network."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location("world_manage", Path(__file__).with_name("manage.py"))
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


def utf(value):
    raw = value.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def tag(kind, name, payload):
    return bytes([kind]) + utf(name) + payload


def level_bytes(name="测试 World"):
    version = tag(8, "Name", utf("1.21.1")) + tag(3, "Id", struct.pack(">i", 3955))
    version += tag(1, "Snapshot", b"\0") + b"\0"
    data = tag(8, "LevelName", utf(name)) + tag(4, "LastPlayed", struct.pack(">q", 1730000000123))
    data += tag(3, "GameType", struct.pack(">i", 1)) + tag(1, "hardcore", b"\0")
    data += tag(1, "Difficulty", b"\2") + tag(3, "DataVersion", struct.pack(">i", 3955))
    data += tag(10, "Version", version) + b"\0"
    return b"\x0a\0\0" + tag(10, "Data", data) + b"\0"


class WorldsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zmcl-world-py-")
        self.root = Path(self.temp.name)
        self.instance = self.make_instance("instance")
        self.other = self.make_instance("other")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def make_instance(self, name):
        path = self.root / name
        (path / "game" / "saves").mkdir(parents=True)
        return path

    def world(self, name="Original", instance=None):
        parent = (instance or self.instance) / "game" / "saves"
        path = parent / name
        path.mkdir()
        (path / "level.dat").write_bytes(gzip.compress(level_bytes(), mtime=0))
        (path / "region").mkdir()
        (path / "region" / "r.0.0.mca").write_bytes(b"region-content\x00" * 1000)
        (path / "datapacks").mkdir()
        (path / "datapacks" / "settings.json").write_text('{"enabled":true}')
        return path

    def stage(self, instance=None):
        self.sequence += 1
        path = (instance or self.instance) / "game" / "saves" / (".zmcl-world-test" + str(self.sequence))
        path.mkdir(mode=0o700)
        return path

    def archive(self, entries, name=None):
        self.sequence += 1
        path = self.root / (name or f"source-{self.sequence}.zip")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item, body in entries:
                archive.writestr(item, body)
        return path

    def test_metadata_list_and_corrupt_world_visibility(self):
        world = self.world()
        (self.instance / "game" / "saves" / "unrelated").mkdir()
        corrupt = self.instance / "game" / "saves" / "Corrupt"
        corrupt.mkdir()
        (corrupt / "level.dat").write_bytes(b"not-NBT")
        result = manage.list_worlds(self.instance)
        self.assertEqual(len(result), 2)
        good = next(item for item in result if item["valid"])
        self.assertEqual(good["name"], "测试 World")
        self.assertEqual(good["version"], "1.21.1")
        self.assertEqual(good["last_played_ms"], 1730000000123)
        self.assertFalse(good["hardcore"])
        self.assertNotIn("seed", good)
        details = manage.info(self.instance, world.name)
        self.assertEqual(details["file_count"], 3)
        self.assertGreater(details["bytes"], 1000)

    def test_cli_list_preserves_unicode_json_protocol(self):
        self.world(name="测试 World")
        expected = manage.list_worlds(self.instance)
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-X", "utf8", str(Path(manage.__file__)),
             "list", str(self.instance)], capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(result.stdout, (json.dumps(
            expected, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode())

    def test_directory_clone_zip_roundtrip_without_hardlinks(self):
        source = self.world()
        imported = manage.import_world(self.other, source, "Imported", self.stage(self.other))
        self.assertEqual(imported["id"], "Imported")
        copied = self.other / "game" / "saves" / "Imported"
        cloned = manage.clone_world(self.other, "Imported", self.instance, "Cloned", self.stage())
        self.assertEqual(cloned["name"], "测试 World")
        clone = self.instance / "game" / "saves" / "Cloned"
        self.assertEqual((source / "level.dat").read_bytes(), (clone / "level.dat").read_bytes())
        self.assertNotEqual((source / "level.dat").stat().st_ino, (clone / "level.dat").stat().st_ino)
        output = self.root / "world.zip"
        stage = self.root / ".zmcl-world-export.zip"
        stage.touch(mode=0o600)
        exported = manage.export_world(self.instance, "Cloned", output, stage)
        self.assertEqual(exported["archive_bytes"], output.stat().st_size)
        self.assertEqual(len(exported["sha256"]), 64)
        with zipfile.ZipFile(output) as archive:
            self.assertIn("level.dat", archive.namelist())
            self.assertNotIn("session.lock", archive.namelist())
        manage.import_world(self.instance, output, "Returned", self.stage())
        returned = self.instance / "game" / "saves" / "Returned"
        for relative in ("level.dat", "region/r.0.0.mca", "datapacks/settings.json"):
            self.assertEqual((source / relative).read_bytes(), (returned / relative).read_bytes())
        self.assertEqual((copied / "level.dat").read_bytes(), (source / "level.dat").read_bytes())

    def test_single_wrapper_zip_and_empty_directories(self):
        source = self.archive([
            ("MyWorld/", b""),
            ("MyWorld/level.dat", gzip.compress(level_bytes())),
            ("MyWorld/empty/", b""),
            ("MyWorld/region/r.0.0.mca", b"data"),
        ])
        manage.import_world(self.instance, source, "Wrapper", self.stage())
        result = self.instance / "game" / "saves" / "Wrapper"
        self.assertTrue((result / "empty").is_dir())
        self.assertFalse((result / "MyWorld").exists())

    def test_export_large_compressible_files_roundtrip_with_import_limits(self):
        source = self.world()
        threshold = 16 * 1024 * 1024
        sizes = {"region/at-limit.mca": threshold,
                 "region/above-limit.mca": threshold + 1}
        for relative, size in sizes.items():
            with (source / relative).open("wb") as stream:
                stream.truncate(size)
        output = self.root / "large-world.zip"
        stage = self.root / ".zmcl-world-large-export.zip"
        stage.touch(mode=0o600)
        exported = manage.export_world(self.instance, source.name, output, stage)
        manage.import_world(self.other, output, "Returned", self.stage(self.other))
        returned = self.other / "game/saves/Returned"
        with zipfile.ZipFile(output) as archive:
            small = archive.getinfo("region/at-limit.mca")
            large = archive.getinfo("region/above-limit.mca")
            self.assertEqual(small.compress_type, zipfile.ZIP_DEFLATED)
            self.assertGreater(small.file_size, small.compress_size * 500)
            self.assertEqual(large.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(large.file_size, large.compress_size)
        def digest(path):
            result = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    result.update(block)
            return result.hexdigest()

        self.assertEqual(exported["sha256"], digest(output))
        for relative in ["level.dat", "datapacks/settings.json", *sizes]:
            with self.subTest(relative=relative):
                self.assertEqual((source / relative).stat().st_size,
                                 (returned / relative).stat().st_size)
                self.assertEqual(digest(source / relative), digest(returned / relative))

    def test_import_still_rejects_high_ratio_large_entries(self):
        output = self.root / "high-ratio.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("level.dat", gzip.compress(level_bytes()))
            with archive.open("region/large.mca", "w") as stream:
                for _ in range(257):
                    stream.write(b"\0" * 65536)
        with self.assertRaisesRegex(ValueError, "compression ratio"):
            manage.import_world(self.instance, output, "RejectedRatio", self.stage())
        self.assertFalse((self.instance / "game/saves/RejectedRatio").exists())

    def test_export_does_not_publish_archive_exceeding_directory_budget(self):
        source = self.world()
        output = self.root / "wide-directory.zip"
        stage = self.root / ".zmcl-world-wide-directory.zip"
        stage.touch(mode=0o600)
        with mock.patch.object(manage, "MAX_CENTRAL", 1):
            with self.assertRaisesRegex(ValueError, "central directory"):
                manage.export_world(self.instance, source.name, output, stage)
        self.assertFalse(output.exists())
        self.assertEqual(manage.metadata(source)["name"], "测试 World")

    def test_export_does_not_publish_archive_exceeding_total_budget(self):
        source = self.world()
        (source / "region/r.0.0.mca").write_bytes(b"tiny")
        total = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
        output = self.root / "archive-overhead.zip"
        stage = self.root / ".zmcl-world-archive-overhead.zip"
        stage.touch(mode=0o600)
        with mock.patch.object(manage, "MAX_TOTAL", total):
            with self.assertRaisesRegex(ValueError, "size budget"):
                manage.export_world(self.instance, source.name, output, stage)
        self.assertFalse(output.exists())
        self.assertEqual(manage.metadata(source)["name"], "测试 World")

    def test_export_retains_file_and_entry_budgets(self):
        source = self.world()
        for setting, value, message in [("MAX_FILE", 1, "file exceeds"),
                                        ("MAX_ENTRIES", 1, "entries")]:
            with self.subTest(setting=setting):
                output = self.root / (setting + ".zip")
                stage = self.root / (".zmcl-world-" + setting + ".zip")
                stage.touch(mode=0o600)
                with mock.patch.object(manage, setting, value):
                    with self.assertRaisesRegex(ValueError, message):
                        manage.export_world(self.instance, source.name, output, stage)
                self.assertFalse(output.exists())

    def test_ambiguous_world_roots_are_rejected(self):
        source = self.archive([
            ("a/level.dat", gzip.compress(level_bytes())),
            ("b/level.dat", gzip.compress(level_bytes())),
        ])
        with self.assertRaises(ValueError):
            manage.import_world(self.instance, source, "Ambiguous", self.stage())
        self.assertFalse((self.instance / "game/saves/Ambiguous").exists())

    def test_malicious_zip_paths_and_collisions(self):
        attacks = [
            [("../escape", b"x")],
            [("C:/escape", b"x")],
            [("back\\escape", b"x")],
            [("NUL.txt", b"x")],
            [("end. /x", b"x")],
            [("Region/a", b"x"), ("region/b", b"x")],
            [("same", b"x"), ("same/child", b"x")],
            [("caf\u00e9/a", b"x"), ("cafe\u0301/b", b"x")],
            [("level.dat", gzip.compress(level_bytes()))],
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                source = self.archive([("level.dat", gzip.compress(level_bytes()))] + attack)
                with self.assertRaises(ValueError):
                    manage.import_world(self.instance, source, "Rejected", self.stage())
                self.assertFalse((self.instance / "game/saves/Rejected").exists())
        self.assertFalse((self.root / "escape").exists())

    def test_zip_symlink_and_special_file_rejected(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            bad = zipfile.ZipInfo("outside")
            bad.create_system = 3
            bad.external_attr = mode << 16
            source = self.archive([("level.dat", gzip.compress(level_bytes())), (bad, b"../outside")])
            with self.assertRaises(ValueError):
                manage.import_world(self.instance, source, "BadLink", self.stage())

    @unittest.skipUnless(os.name == "posix", "POSIX symlink/hardlink test")
    def test_directory_links_and_special_files_rejected(self):
        for name, prepare in [
            ("Symbolic", lambda path: (path / "bad").symlink_to(self.root)),
            ("Hard", lambda path: os.link(path / "level.dat", path / "bad")),
            ("Pipe", lambda path: os.mkfifo(path / "bad")),
        ]:
            source = self.world(name)
            prepare(source)
            with self.subTest(name=name), self.assertRaises(ValueError):
                manage.import_world(self.other, source, "Never", self.stage(self.other))
            self.assertFalse((self.other / "game/saves/Never").exists())

    def test_nbt_structure_and_decompressed_budgets(self):
        bad_arrays = b"\x0a\0\0" + tag(7, "huge", struct.pack(">i", 2**31 - 1)) + b"\0"
        with self.assertRaises(ValueError):
            manage.Nbt(io.BytesIO(bad_arrays)).parse()
        duplicate = tag(8, "LevelName", utf("one")) + tag(8, "LevelName", utf("two")) + b"\0"
        raw = b"\x0a\0\0" + tag(10, "Data", duplicate) + b"\0"
        with self.assertRaises(ValueError):
            manage.Nbt(io.BytesIO(raw)).parse()
        old = manage.MAX_NBT_RAW
        try:
            manage.MAX_NBT_RAW = 64
            with self.assertRaises(ValueError):
                manage.Nbt(io.BytesIO(level_bytes())).parse()
        finally:
            manage.MAX_NBT_RAW = old
        nesting = b"\0"
        for _ in range(66):
            nesting = tag(10, "nested", nesting) + b"\0"
        with self.assertRaises(ValueError):
            manage.Nbt(io.BytesIO(b"\x0a\0\0" + nesting)).parse()

    def test_modified_utf8_title_and_truncated_gzip(self):
        raw_name = b"emoji \xed\xa0\xbd\xed\xb8\x80"
        data = tag(8, "LevelName", struct.pack(">H", len(raw_name)) + raw_name) + b"\0"
        parsed = manage.Nbt(io.BytesIO(b"\x0a\0\0" + tag(10, "Data", data) + b"\0")).parse()
        self.assertEqual(parsed["name"], "emoji 😀")
        world = self.world()
        body = (world / "level.dat").read_bytes()
        (world / "level.dat").write_bytes(body[:-8])
        with self.assertRaises((EOFError, OSError, ValueError)):
            manage.metadata(world)

    def test_no_replace_atomic_publication_preserves_existing_empty_directory(self):
        stage = self.stage()
        (stage / "payload").write_bytes(b"data")
        target = self.instance / "game/saves/Existing"
        target.mkdir()
        before = target.stat().st_ino
        with self.assertRaises(OSError):
            manage.publish_directory(stage, target)
        self.assertEqual(target.stat().st_ino, before)
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue((stage / "payload").exists())

    def test_export_refuses_overwrite_and_inside_world(self):
        source = self.world()
        existing = self.root / "existing.zip"
        existing.write_bytes(b"keep me")
        stage = self.root / ".zmcl-world-existing.zip"
        stage.touch()
        with self.assertRaises(ValueError):
            manage.export_world(self.instance, source.name, existing, stage)
        self.assertEqual(existing.read_bytes(), b"keep me")
        nested_stage = source / ".zmcl-world-export.zip"
        nested_stage.touch()
        with self.assertRaises(ValueError):
            manage.export_world(self.instance, source.name, source / "bad.zip", nested_stage)

    def test_central_directory_budget_is_checked_before_zip_loading(self):
        source = self.archive([("level.dat", gzip.compress(level_bytes()))])
        raw = bytearray(source.read_bytes())
        offset = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", raw, offset + 8, 60000, 60000)
        source.write_bytes(raw)
        with source.open("rb") as stream, self.assertRaises(ValueError):
            manage.check_zip_directory(stream)

    def test_mutating_source_is_not_published(self):
        source = self.world()
        original = manage.transfer
        changed = False

        def mutate(content, target, expected, budget):
            nonlocal changed
            original(content, target, expected, budget)
            if not changed:
                changed = True
                (source / "level.dat").write_bytes(b"changed")
        manage.transfer = mutate
        try:
            with self.assertRaises(ValueError):
                manage.import_world(self.other, source, "Changed", self.stage(self.other))
        finally:
            manage.transfer = original
        self.assertFalse((self.other / "game/saves/Changed").exists())

    def test_invalid_or_oversized_launch_record_is_not_trusted(self):
        record = self.instance / "last-launch.json"
        record.write_text('{"pid":"not-a-pid","started":1}')
        with self.assertRaises(ValueError):
            manage.guard_stopped(self.instance)
        record.write_bytes(b"x" * 65537)
        with self.assertRaises(ValueError):
            manage.guard_stopped(self.instance)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process identity test")
    def test_live_recorded_process_blocks_writes_then_dead_process_allows(self):
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"],
                                 cwd=self.instance / "game")
        try:
            record = self.instance / "last-launch.json"
            record.write_text(json.dumps({"pid": child.pid, "started": int(time.time())}))
            with self.assertRaises(ValueError):
                manage.guard_stopped(self.instance)
        finally:
            child.terminate()
            child.wait(timeout=5)
        manage.guard_stopped(self.instance)

    @unittest.skipUnless(os.name == "posix", "POSIX FileChannel compatible lock test")
    def test_existing_world_lock_blocks_even_without_launch_record(self):
        source = self.world()
        lock_path = source / "session.lock"
        lock_path.touch()
        program = ("import fcntl,sys,time;"
                   "f=open(sys.argv[1],'r+b');"
                   "fcntl.lockf(f.fileno(),fcntl.LOCK_EX);"
                   "print('ready',flush=True);time.sleep(30)")
        child = subprocess.Popen([sys.executable, "-c", program, str(lock_path)],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(ValueError):
                manage.guard_stopped(self.instance)
            with self.assertRaises(ValueError):
                manage.clone_world(self.instance, source.name, self.other, "Busy", self.stage(self.other))
            self.assertFalse((self.other / "game/saves/Busy").exists())
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()
        manage.guard_stopped(self.instance)


if __name__ == "__main__":
    unittest.main()
