#!/usr/bin/env python3
"""Offline file acceptance for the CLI operations dispatched by the native GUI.

Uses a temporary launcher root and synthetic Java save. This is not a Minecraft
launch, online provider test, or proof of native window interaction.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("world_fixture", ROOT / "src/worlds/test_manage.py")
world_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(world_fixture)
BINARY = None


class ManagementAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zmcl-gui-management-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "launcher root"
        self.run_cli("instance", "create", "demo", "--version", "1.21.1")
        self.game = self.root / "instances/demo/game"
        self.source = self.base / "save with spaces"
        self.source.mkdir()
        self.original = gzip.compress(world_fixture.level_bytes("GUI fixture"), mtime=0)
        (self.source / "level.dat").write_bytes(self.original)
        (self.source / "region").mkdir()
        (self.source / "region/r.0.0.mca").write_bytes(bytes(range(256)) * 4)

    def run_cli(self, *args, succeeds=True):
        result = subprocess.run([str(BINARY), "--root", str(self.root), *args],
                                capture_output=True, text=True, timeout=60)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, args)
        return result.stderr

    def import_world(self):
        self.run_cli("world", "import", "--", "demo", str(self.source), "Imported world")
        return self.game / "saves/Imported world"

    def test_world_zip_roundtrip_and_existing_destination_guard(self):
        world = self.import_world()
        rows = self.run_cli("world", "list", "--", "demo")
        self.assertTrue(any(row["id"] == "Imported world" and row["valid"] for row in rows))
        archive = self.base / "export $(never); world.zip"
        self.run_cli("world", "export", "--", "demo", "Imported world", str(archive))
        before = archive.read_bytes()
        self.assertTrue(zipfile.is_zipfile(archive))
        self.run_cli("world", "export", "--", "demo", "Imported world", str(archive), succeeds=False)
        self.assertEqual(archive.read_bytes(), before)
        self.run_cli("world", "import", "--", "demo", str(archive), "Roundtrip")
        self.assertEqual((world / "level.dat").read_bytes(),
                         (self.game / "saves/Roundtrip/level.dat").read_bytes())
        self.assertFalse((self.base / "never").exists())

    def test_backup_restore_creates_pre_restore_snapshot(self):
        world = self.import_world()
        backup = self.run_cli("backup", "create", "--", "demo", "Before edit")
        (world / "region/r.0.0.mca").write_bytes(b"edited region")
        self.run_cli("backup", "restore", "--", "demo", backup["id"])
        self.assertEqual((world / "region/r.0.0.mca").read_bytes(), bytes(range(256)) * 4)
        rows = self.run_cli("backup", "list", "--", "demo")
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(row["manifest_valid"] for row in rows))
        retained_edit = False
        for path in (self.root / "backups/demo").glob("*.zip"):
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith("region/r.0.0.mca") and archive.read(name) == b"edited region":
                        retained_edit = True
        self.assertTrue(retained_edit, "The pre-restore snapshot must preserve the edited file")

    def test_uncertain_game_state_refuses_world_mutation(self):
        record = self.root / "instances/demo/last-launch.json"
        record.write_text('{"pid":"unverified","started":0}')
        error = self.run_cli("world", "import", "--", "demo", str(self.source), "Blocked", succeeds=False)
        self.assertIn("launch record", error.lower())
        self.assertFalse((self.game / "saves/Blocked").exists())

    def test_resource_listing_preserves_unmanaged_files(self):
        folder = self.game / "resourcepacks"
        folder.mkdir()
        manual = folder / "manual.zip"
        manual.write_bytes(b"user-owned fixture")
        rows = self.run_cli("resources", "list", "--", "demo")
        self.assertEqual(rows["entries"], [])
        self.assertTrue(any(row["path"] == "resourcepacks/manual.zip" for row in rows["unmanaged"]))
        self.assertEqual(manual.read_bytes(), b"user-owned fixture")

    def test_server_create_and_start_leave_eula_unaccepted(self):
        self.run_cli("server", "create", "--version", "1.21.1", "--", "local")
        directory = self.root / "servers/local"
        eula = (directory / "eula.txt").read_bytes()
        self.assertIn(b"eula=false", eula)
        rows = self.run_cli("server", "list")
        self.assertEqual(rows[0]["server"]["id"], "local")
        self.run_cli("server", "start", "--", "local", succeeds=False)
        # A synthetic installed marker tests the EULA guard without a download.
        config_file = directory / "server.json"
        config = json.loads(config_file.read_text())
        config.update(installed=True, java_major=21, server_sha1="0" * 40, jar_size=1)
        config_file.write_text(json.dumps(config))
        error = self.run_cli("server", "start", "--", "local", succeeds=False)
        self.assertIn("EULA", error)
        self.assertEqual((directory / "eula.txt").read_bytes(), eula)
        self.assertFalse((directory / "process.json").exists())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args, rest = parser.parse_known_args()
    BINARY = args.binary.resolve(strict=True)
    unittest.main(argv=[__file__, *rest])
