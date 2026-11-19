"""Temporary-directory regressions for the local backup transaction and codec."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location("backup_archive", Path(__file__).with_name("archive.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zmcl-backup-regression-")
        self.root = Path(self.temporary.name)
        self.instance = self.root / "instance"
        self.game = self.instance / "game"
        self.snapshots = self.root / "backups"
        self.game.mkdir(parents=True)
        self.snapshots.mkdir()
        (self.game / "saves").mkdir()
        (self.game / "saves/world").mkdir()
        (self.game / "saves/world/level.dat").write_bytes(b"old-world")
        (self.game / "config").mkdir()
        (self.game / "config/video.txt").write_bytes(b"old-video")
        (self.game / "options.txt").write_bytes(b"music:0.5")
        (self.game / "mods").mkdir()
        (self.game / "mods/managed.jar").write_bytes(b"keep-mod")
        (self.game / "accounts.json").write_bytes(b"private-account")
        (self.game / ".fabric").mkdir()
        (self.game / ".fabric/state").write_bytes(b"keep-runtime")

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, name="first"):
        return backup.snapshot(self.game, self.snapshots, name, "demo", "test", int(time.time()))

    def alter_zip(self, original, name, transform):
        target = self.snapshots / (name + ".zip")
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(target, "x") as result:
            for info in source.infolist():
                body = source.read(info.filename)
                next_info, next_body = transform(info, body)
                result.writestr(next_info, next_body)
        return target

    def test_roundtrip_pre_snapshot_and_preserved_managed_roots(self):
        result = self.create()
        self.assertGreater(result["bytes"], 0)
        with zipfile.ZipFile(self.snapshots / "first.zip") as archive:
            names = archive.namelist()
            self.assertNotIn("game/mods/managed.jar", names)
            self.assertNotIn("game/accounts.json", names)
            self.assertNotIn("game/.fabric/state", names)
            manifest = json.loads(archive.read(backup.MANIFEST))
            self.assertTrue(all(len(file["sha256"]) == 64 for file in manifest["files"]))
        (self.game / "saves/world/level.dat").write_bytes(b"new-world")
        (self.game / "saves/extra-world").mkdir()
        (self.game / "saves/extra-world/level.dat").write_bytes(b"extra-world")
        outcome = backup.restore(self.instance, self.snapshots, "first", "demo")
        self.assertEqual((self.game / "saves/world/level.dat").read_bytes(), b"old-world")
        self.assertEqual((self.game / "mods/managed.jar").read_bytes(), b"keep-mod")
        self.assertEqual((self.game / "accounts.json").read_bytes(), b"private-account")
        self.assertEqual((self.game / ".fabric/state").read_bytes(), b"keep-runtime")
        self.assertFalse((self.game / "saves/extra-world").exists())
        with zipfile.ZipFile(self.snapshots / (outcome["pre_restore"] + ".zip")) as archive:
            self.assertEqual(archive.read("game/saves/world/level.dat"), b"new-world")
            self.assertEqual(archive.read("game/saves/extra-world/level.dat"), b"extra-world")
        self.assertFalse((self.instance / backup.JOURNAL).exists())
        self.assertEqual(len(backup.list_snapshots(self.snapshots, "demo")), 2)

    def test_tampered_file_rejected_before_live_changes_or_pre_snapshot(self):
        self.create()
        def transform(info, body):
            if info.filename == "game/saves/world/level.dat":
                body = b"bad-world"
            return info, body
        self.alter_zip(self.snapshots / "first.zip", "tampered", transform)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            backup.restore(self.instance, self.snapshots, "tampered", "demo")
        self.assertEqual((self.game / "saves/world/level.dat").read_bytes(), b"old-world")
        self.assertFalse(any(self.snapshots.glob("pre-*.zip")))
        self.assertFalse(any(self.instance.glob(".backup-stage-*")))

    def test_paths_links_special_files_and_zip_bombs_rejected(self):
        for index, name in enumerate(["../escape", "/escape", "C:/escape", "game/a\\b", "game/CON", "game/a/../../b"]):
            target = self.snapshots / ("bad" + str(index) + ".zip")
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(name, b"x")
            stage = self.root / ("stage" + str(index))
            stage.mkdir()
            with self.assertRaises(ValueError):
                backup.unpack(target, stage, "demo")
        for index, mode in enumerate([stat.S_IFLNK, stat.S_IFIFO]):
            target = self.snapshots / ("special" + str(index) + ".zip")
            info = zipfile.ZipInfo("game/config")
            info.create_system = 3
            info.external_attr = (mode | 0o777) << 16
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(info, b"/outside")
            stage = self.root / ("special-stage" + str(index))
            stage.mkdir()
            with self.assertRaisesRegex(ValueError, "special"):
                backup.unpack(target, stage, "demo")
        target = self.snapshots / "bomb.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.writestr("game/saves/bomb", b"0" * 2_000_000)
        stage = self.root / "bomb-stage"
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "ratio"):
            backup.unpack(target, stage, "demo")
        self.assertFalse((self.root / "escape").exists())

    def test_normalized_duplicate_and_duplicate_json_keys_rejected(self):
        target = self.snapshots / "duplicate.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("game/config/Caf\u00e9", b"a")
            archive.writestr("game/config/Cafe\u0301", b"b")
        stage = self.root / "duplicate-stage"
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "Duplicate normalized"):
            backup.unpack(target, stage, "demo")
        self.create()
        self.alter_zip(self.snapshots / "first.zip", "json-duplicate",
                       lambda i, b: (i, b'{"format_version":1,"format_version":1}') if i.filename == backup.MANIFEST else (i, b))
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            backup.unpack(self.snapshots / "json-duplicate.zip", stage, "demo")

    def test_manifest_cannot_restore_managed_mods_or_unlisted_data(self):
        self.create()
        def transform(info, body):
            if info.filename == backup.MANIFEST:
                value = json.loads(body)
                value["roots"].append("mods")
                body = json.dumps(value).encode()
            return info, body
        self.alter_zip(self.snapshots / "first.zip", "mods-injection", transform)
        stage = self.root / "managed-stage"
        stage.mkdir()
        with self.assertRaisesRegex(ValueError, "mutable data root"):
            backup.unpack(self.snapshots / "mods-injection.zip", stage, "demo")
        target = self.alter_zip(self.snapshots / "first.zip", "unlisted", lambda i, b: (i, b))
        with zipfile.ZipFile(target, "a") as archive:
            archive.writestr("game/config/unlisted", b"injected")
        with self.assertRaisesRegex(ValueError, "Unmanifested"):
            backup.unpack(target, stage, "demo")

    def test_overwrite_and_source_symlink_refused(self):
        self.create()
        original = (self.snapshots / "first.zip").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.create()
        self.assertEqual(original, (self.snapshots / "first.zip").read_bytes())
        if hasattr(os, "symlink"):
            (self.game / "config/link").symlink_to(self.root / "outside")
            with self.assertRaisesRegex(ValueError, "Symbolic"):
                self.create("link")
            self.assertFalse((self.snapshots / "link.zip").exists())

    def test_interruption_after_old_move_recovers_original_and_keeps_pre_snapshot(self):
        self.create()
        (self.game / "saves/world/level.dat").write_bytes(b"current-world")
        with self.assertRaises(backup.SimulatedInterruption):
            backup.restore(self.instance, self.snapshots, "first", "demo", fail_at="after_move_old")
        self.assertFalse(self.game.exists())
        self.assertTrue((self.instance / backup.JOURNAL).exists())
        backup.recover(self.instance, self.snapshots)
        self.assertEqual((self.game / "saves/world/level.dat").read_bytes(), b"current-world")
        self.assertTrue(any(self.snapshots.glob("pre-*.zip")))
        self.assertFalse((self.instance / backup.JOURNAL).exists())

    def test_interruption_after_new_move_finishes_verified_commit(self):
        self.create()
        (self.game / "saves/world/level.dat").write_bytes(b"current-world")
        with self.assertRaises(backup.SimulatedInterruption):
            backup.restore(self.instance, self.snapshots, "first", "demo", fail_at="after_move_new")
        backup.recover(self.instance, self.snapshots)
        self.assertEqual((self.game / "saves/world/level.dat").read_bytes(), b"old-world")
        self.assertEqual((self.game / "mods/managed.jar").read_bytes(), b"keep-mod")
        self.assertTrue(any(self.snapshots.glob("pre-*.zip")))
        self.assertFalse(any(self.instance.glob(".backup-old-*")))

    def test_ordinary_rename_failure_rolls_back(self):
        self.create()
        (self.game / "saves/world/level.dat").write_bytes(b"current-world")
        real_rename = os.rename
        def rename(source, target):
            if Path(source).name.startswith(".backup-stage-") and Path(target).name == "game":
                raise OSError("simulated disk error")
            return real_rename(source, target)
        with mock.patch.object(backup.os, "rename", side_effect=rename):
            with self.assertRaisesRegex(OSError, "simulated"):
                backup.restore(self.instance, self.snapshots, "first", "demo")
        self.assertEqual((self.game / "saves/world/level.dat").read_bytes(), b"current-world")
        self.assertFalse((self.instance / backup.JOURNAL).exists())
        self.assertTrue(any(self.snapshots.glob("pre-*.zip")))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process identity")
    def test_live_process_cwd_refused_and_stale_pid_reuse_ignored(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], cwd=self.game)
        try:
            (self.instance / "last-launch.json").write_text(json.dumps({"pid": child.pid, "started": int(time.time())}))
            with self.assertRaisesRegex(ValueError, "still running"):
                backup.guard_stopped(self.instance)
        finally:
            child.terminate()
            child.wait(timeout=5)
        backup.guard_stopped(self.instance)
        fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
        (self.instance / "last-launch.json").write_text(json.dumps({
            "pid": os.getpid(), "started": 0, "process_start_ticks": int(fields[19]) + 1}))
        backup.guard_stopped(self.instance)

    def test_existing_partial_file_is_never_removed(self):
        partial = self.snapshots / ".partial-occupied"
        partial.write_bytes(b"unrelated-existing-partial")
        with self.assertRaises(FileExistsError):
            self.create("occupied")
        self.assertEqual(partial.read_bytes(), b"unrelated-existing-partial")

    def test_source_mutation_is_detected_before_publication(self):
        original = backup.validate_state
        changed = False
        def validate(game, state, check_ctime=True):
            nonlocal changed
            if not changed:
                (self.game / "saves/world/new-user-file").write_bytes(b"keep-me")
                changed = True
            return original(game, state, check_ctime)
        with mock.patch.object(backup, "validate_state", side_effect=validate):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.create("changing")
        self.assertFalse((self.snapshots / "changing.zip").exists())
        self.assertEqual((self.game / "saves/world/new-user-file").read_bytes(), b"keep-me")

    def test_exact_depth_limit_roundtrips_and_preserved_hardlinks_work(self):
        path = self.game / "deep"
        path.mkdir()
        for index in range(30):
            path = path / ("d" + str(index))
            path.mkdir()
        (path / "last").write_bytes(b"depth-32")
        os.link(self.game / "mods/managed.jar", self.game / "mods/duplicate.jar")
        self.create()
        backup.restore(self.instance, self.snapshots, "first", "demo")
        self.assertEqual((path / "last").read_bytes(), b"depth-32")
        self.assertEqual((self.game / "mods/duplicate.jar").read_bytes(), b"keep-mod")

    def test_missing_pre_snapshot_retains_original_recovery_directory(self):
        self.create()
        (self.game / "saves/world/level.dat").write_bytes(b"current-world")
        with self.assertRaises(backup.SimulatedInterruption):
            backup.restore(self.instance, self.snapshots, "first", "demo", fail_at="after_move_new")
        next(self.snapshots.glob("pre-*.zip")).unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            backup.recover(self.instance, self.snapshots)
        old = next(self.instance.glob(".backup-old-*"))
        self.assertEqual((old / "saves/world/level.dat").read_bytes(), b"current-world")
        self.assertTrue((self.instance / backup.JOURNAL).exists())

    def test_directory_and_time_limits_apply_before_publication(self):
        with mock.patch.object(backup, "MAX_FILES", 2):
            with self.assertRaisesRegex(ValueError, "entries"):
                self.create("too-many")
        self.assertFalse((self.snapshots / "too-many.zip").exists())
        budget = backup.Budget()
        budget.started -= backup.MAX_SECONDS + 1
        with self.assertRaisesRegex(ValueError, "seconds"):
            backup.snapshot(self.game, self.snapshots, "timeout", "demo", "", int(time.time()), budget)
        self.assertFalse((self.snapshots / "timeout.zip").exists())
        target = self.snapshots / "huge-directory.zip"
        target.write_bytes(struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 1, 1, backup.MAX_INDEX + 1, 0, 0))
        with self.assertRaisesRegex(ValueError, "central directory"):
            backup.check_directory_budget(target)


if __name__ == "__main__":
    unittest.main()
