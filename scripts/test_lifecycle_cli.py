#!/usr/bin/env python3
"""Exercise actual CLI file workflows with an isolated, explicitly synthetic world.

Run from the repository:
    python3 -B scripts/test_lifecycle_cli.py [target/release/zmcl]

No game, Java runtime, server or network download is started. The region file is
opaque fixture data, so this is not a real-world loading/compatibility test.
The retained acceptance.json contains sanitized command results and digests.
"""
import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import zipfile

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def load_fixture():
    spec = importlib.util.spec_from_file_location(
        "zmcl_lifecycle_world_fixture", REPO / "src/worlds/test_manage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.level_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", default=str(REPO / "target/release/zmcl"))
    args = parser.parse_args()
    binary = Path(args.binary).resolve()
    require(binary.is_file(), "Build the requested zmcl binary before running this script")
    case = REPO / ".test-data" / ("lifecycle-e2e-" + str(time.time_ns()))
    case.mkdir(parents=True, mode=0o700)
    root = case / "home"
    root.mkdir(mode=0o700)
    fixture = case / "synthetic-world"
    fixture.mkdir()
    (fixture / "region").mkdir()
    level_bytes = load_fixture()
    expected = {
        "level.dat": gzip.compress(level_bytes("Synthetic lifecycle fixture"), mtime=0),
        "region/r.0.0.mca": b"SYNTHETIC OPAQUE REGION FIXTURE; NOT GAME-LOADABLE\n" * 37,
        "SYNTHETIC_WORLD.txt": b"Generated for CLI file-boundary acceptance only.\n",
    }
    for name, data in expected.items():
        (fixture / name).write_bytes(data)

    report = {
        "schema": 1,
        "scope": "actual CLI with synthetic NBT and opaque region fixture",
        "game_launched": False,
        "network_downloads_requested": False,
        "real_world_compatibility_claimed": False,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platform": platform.system() + "-" + platform.machine(),
        "binary_sha256": sha256(binary),
        "commands": [],
        "checks": {},
        "passed": False,
    }

    def sanitize(value):
        if isinstance(value, str):
            return value.replace(str(case), "<test-case>").replace(str(REPO), "<repo>")
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    def cli(*arguments, expect_ok=True):
        command = [str(binary), "--root", str(root), *(str(arg) for arg in arguments)]
        completed = subprocess.run(
            command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=45, check=False,
        )
        record = {
            "arguments": sanitize(command[1:]),
            "exit_code": completed.returncode,
            "expected_success": expect_ok,
        }
        if completed.stderr:
            record["stderr"] = sanitize(completed.stderr[:8192])
        report["commands"].append(record)
        require(
            (completed.returncode == 0) == expect_ok,
            "Unexpected CLI exit for " + " ".join(str(arg) for arg in arguments[:3]),
        )
        if not expect_ok:
            require(completed.returncode != 0, "Protection command unexpectedly succeeded")
            return None
        result = json.loads(completed.stdout)
        record["result"] = sanitize(result)
        return result

    def world_files(path, values=expected):
        found = {
            str(item.relative_to(path))
            for item in path.rglob("*")
            if item.is_file() and item.name != "session.lock"
        }
        require(found == set(values), "World copy changed the expected file set")
        for name, data in values.items():
            require((path / name).read_bytes() == data, "World bytes differ: " + name)

    def verify_backup(backup_id, required_files):
        require(re.fullmatch(r"[A-Za-z0-9_-]+", backup_id), "Unsafe backup result ID")
        archive_path = root / "backups/alpha" / (backup_id + ".zip")
        with zipfile.ZipFile(archive_path) as archive:
            require(archive.testzip() is None, "Backup ZIP failed CRC verification")
            manifests = [
                name for name in archive.namelist()
                if not name.startswith("game/") and name.endswith(".json")
            ]
            require(len(manifests) == 1, "Expected one backup manifest")
            manifest = json.loads(archive.read(manifests[0]))
            require(manifest["instance"] == "alpha", "Backup instance identity changed")
            for item in manifest["files"]:
                data = archive.read("game/" + item["path"])
                require(len(data) == item["size"], "Backup manifest size mismatch")
                require(hashlib.sha256(data).hexdigest() == item["sha256"],
                        "Backup manifest SHA-256 mismatch")
            for name, data in required_files.items():
                require(archive.read("game/" + name) == data,
                        "Backup did not retain expected bytes: " + name)
            require(not any(name.startswith("game/mods/") for name in archive.namelist()),
                    "Backup unexpectedly included the excluded mods root")
        return {"id": backup_id, "sha256": sha256(archive_path),
                "file_count": len(manifest["files"])}

    try:
        for instance in ("alpha", "beta"):
            created = cli("instance", "create", instance, "--version", "1.21.1")
            require(created["id"] == instance and created["installed"] is False,
                    "Fresh instance response is inconsistent")

        cli("world", "import", "alpha", fixture, "Fixture")
        original = root / "instances/alpha/game/saves/Fixture"
        world_files(original)
        listed = cli("world", "list", "alpha")
        require(len(listed) == 1 and listed[0]["id"] == "Fixture" and listed[0]["valid"],
                "World list did not identify the imported fixture")
        info = cli("world", "info", "alpha", "Fixture")
        require(info["format"] == "java" and info["valid"]
                and info["name"] == "Synthetic lifecycle fixture",
                "World metadata fields do not match the synthetic NBT")
        require(info["file_count"] == len(expected)
                and info["bytes"] == sum(map(len, expected.values())),
                "World info file/byte count changed")
        revealed = cli("world", "path", "alpha", "Fixture")
        require(Path(revealed["path"]).resolve() == original.resolve(),
                "World path points outside the selected fixture")

        exported = case / "exported-world.zip"
        cli("world", "export", "alpha", "Fixture", exported)
        with zipfile.ZipFile(exported) as archive:
            require(archive.testzip() is None, "Exported world ZIP failed CRC verification")
            files = {info.filename: archive.read(info) for info in archive.infolist()
                     if not info.is_dir()}
            require(files == expected, "World export changed bytes or included session.lock")
        cli("world", "import", "beta", exported, "RoundTrip")
        world_files(root / "instances/beta/game/saves/RoundTrip")
        cli("world", "clone", "alpha", "Fixture", "beta", "Cloned")
        cloned_world = root / "instances/beta/game/saves/Cloned"
        world_files(cloned_world)
        require(not os.path.samefile(original / "level.dat", cloned_world / "level.dat"),
                "World clone shares a writable file inode")
        cli("world", "import", "alpha", fixture, "Fixture", expect_ok=False)
        cli("world", "export", "alpha", "Fixture", exported, expect_ok=False)
        world_files(original)
        report["checks"]["world_directory_zip_clone"] = {
            "passed": True, "files": len(expected),
            "bytes": sum(map(len, expected.values())),
            "metadata_name": info["name"], "existing_destinations_refused": True,
            "export_sha256": sha256(exported),
        }

        game = root / "instances/alpha/game"
        options_before = b"music:0.25\n# synthetic CLI fixture\n"
        options_after = b"music:0.75\n# pre-restore fixture\n"
        (game / "options.txt").write_bytes(options_before)
        (game / "mods").mkdir()
        manual = game / "mods/manual-fixture.jar"
        manual.write_bytes(b"OPAQUE MOD FIXTURE; NEVER LAUNCHED\n")
        created = cli("backup", "create", "alpha", "Synthetic fixture before edit")
        backup_id = created["id"]
        before_list = cli("backup", "list", "alpha")
        require(len(before_list) == 1 and before_list[0]["id"] == backup_id,
                "Created backup is absent from CLI listing")
        require(before_list[0]["manifest_valid"] and not before_list[0]["data_verified"],
                "Backup listing incorrectly reports whole-data verification")
        required_original = {"saves/Fixture/" + name: data for name, data in expected.items()}
        required_original["options.txt"] = options_before
        original_backup = verify_backup(backup_id, required_original)

        changed_level = gzip.compress(level_bytes("Synthetic changed before restore"), mtime=0)
        changed_region = b"SYNTHETIC PRE-RESTORE REGION BYTES\n"
        (original / "level.dat").write_bytes(changed_level)
        (original / "region/r.0.0.mca").write_bytes(changed_region)
        (game / "options.txt").write_bytes(options_after)
        (game / "created-after-backup.txt").write_bytes(b"preserve in pre-restore snapshot\n")
        preserved_mod = b"MANUALLY CHANGED EXCLUDED MOD FIXTURE\n"
        manual.write_bytes(preserved_mod)
        restored = cli("backup", "restore", "alpha", backup_id)
        require(restored == {"restored": True}, "Backup restore response changed")
        world_files(original)
        require((game / "options.txt").read_bytes() == options_before,
                "Backup did not restore original options")
        require(not (game / "created-after-backup.txt").exists(),
                "Restored mutable root retained a post-snapshot file")
        require(manual.read_bytes() == preserved_mod,
                "Backup restore overwrote the excluded mods root")
        after_list = cli("backup", "list", "alpha")
        pre = [item for item in after_list if item["id"].startswith("pre-")]
        require(len(after_list) == 2 and len(pre) == 1, "Pre-restore snapshot is missing")
        before_restore = verify_backup(pre[0]["id"], {
            "saves/Fixture/level.dat": changed_level,
            "saves/Fixture/region/r.0.0.mca": changed_region,
            "options.txt": options_after,
            "created-after-backup.txt": b"preserve in pre-restore snapshot\n",
        })
        report["checks"]["backup_restore"] = {
            "passed": True, "original_bytes_restored": True,
            "excluded_mods_preserved": True, "manifest_sha256_verified": True,
            "original_snapshot": original_backup, "pre_restore_snapshot": before_restore,
        }

        cloned = cli("instance", "clone", "alpha", "copy")
        require(cloned["requires_install"] is True
                and cloned["instance"]["installed"] is False,
                "Cloned instance was incorrectly marked installed")
        copy_game = root / "instances/copy/game"
        world_files(copy_game / "saves/Fixture")
        copy_options = copy_game / "options.txt"
        require(not os.path.samefile(game / "options.txt", copy_options),
                "Instance clone shares a writable file inode")
        copy_options.write_bytes(b"music:0.1\n# independent copy\n")
        require((game / "options.txt").read_bytes() == options_before,
                "Editing the clone changed its source")
        trashed = cli("instance", "trash", "copy")
        trash_id = trashed["trash_id"]
        require(not (root / "instances/copy").exists(), "Trashed instance remains active")
        trash_list = cli("instance", "trash-list")
        require(any(item["trash_id"] == trash_id for item in trash_list),
                "Trashed instance is absent from listing")
        cli("instance", "restore", trash_id, "alpha", expect_ok=False)
        require((game / "options.txt").read_bytes() == options_before,
                "Occupied restore target was modified")
        restored_instance = cli("instance", "restore", trash_id)
        require(restored_instance["id"] == "copy", "Restored instance identity changed")
        require(copy_options.read_bytes() == b"music:0.1\n# independent copy\n",
                "Trash/restore lost changed clone bytes")
        world_files(copy_game / "saves/Fixture")
        require(cli("instance", "trash-list") == [], "Restored item remains in trash listing")
        report["checks"]["instance_clone_trash_restore"] = {
            "passed": True, "independent_writable_files": True,
            "requires_install": True, "occupied_target_refused": True,
            "restored_changed_clone_bytes": True,
        }

        journals = [
            path for instance in (root / "instances").iterdir()
            for name in (".content-journal.json", ".backup-restore.json",
                         ".resources-journal.json")
            if (path := instance / name).exists()
        ]
        require(not journals, "Successful lifecycle left a pending mutation journal")
        report["checks"]["no_pending_journals"] = True
        report["passed"] = True
    except Exception as error:
        report["error"] = sanitize(type(error).__name__ + ": " + str(error))
    finally:
        report["command_count"] = len(report["commands"])
        report["expected_protection_failures"] = sum(
            not item["expected_success"] for item in report["commands"]
        )
        report["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        body = json.dumps(sanitize(report), ensure_ascii=False, indent=2) + "\n"
        (case / "acceptance.json").write_text(body, encoding="utf-8")
        print(json.dumps({
            "passed": report["passed"],
            "report": str((case / "acceptance.json").relative_to(REPO)),
            "binary_sha256": report["binary_sha256"],
            "command_count": report["command_count"],
            "expected_protection_failures": report["expected_protection_failures"],
            "checks": report["checks"],
            **({"error": report["error"]} if "error" in report else {}),
        }, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
