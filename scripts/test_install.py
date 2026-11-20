#!/usr/bin/env python3
"""Regression checks for user-prefix installs, upgrades and removal.

The tests use fake executable bytes and temporary prefixes. They never execute
Minecraft or touch the current user's installed launcher or game data. By
default the installer is loaded from install.py next to this file. Set
ZMCL_INSTALLER_UNDER_TEST to exercise an implementation outside that directory.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


INSTALLER_PATH = Path(
    os.environ.get("ZMCL_INSTALLER_UNDER_TEST", Path(__file__).with_name("install.py"))
)
SPEC = importlib.util.spec_from_file_location("zmcl_installer_under_test", INSTALLER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load installer: {INSTALLER_PATH}")
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)

PAYLOAD_PATHS = (
    "bin/zmcl",
    "share/icons/hicolor/scalable/apps/zmcl.svg",
    "share/applications/zmcl.desktop",
)
MANIFEST_PATH = "share/zmcl/install.json"
MANAGED_PATHS = (*PAYLOAD_PATHS, MANIFEST_PATH)
REFUSAL_ERRORS = (ValueError, RuntimeError, OSError)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_state(path):
    """Describe a managed leaf without following a replacement symlink."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ("absent",)
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", os.readlink(path), mode)
    if stat.S_ISREG(metadata.st_mode):
        return ("file", path.read_bytes(), mode)
    if stat.S_ISDIR(metadata.st_mode):
        return ("directory", mode)
    return ("other", metadata.st_mode)


def managed_state(prefix):
    return {relative: file_state(prefix / relative) for relative in MANAGED_PATHS}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zmcl-install-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "prefix with spaces"
        self.old_binary = self.root / "old-launcher"
        self.new_binary = self.root / "new-launcher"
        self.old_binary.write_bytes(b"old launcher fixture\n")
        self.new_binary.write_bytes(b"new launcher fixture\n")
        self.output = io.StringIO()
        self.output_redirect = contextlib.redirect_stdout(self.output)
        self.output_redirect.__enter__()
        self.addCleanup(self.output_redirect.__exit__, None, None, None)

    def install(self, prefix=None, binary=None):
        return installer.install(prefix or self.prefix, binary or self.old_binary)

    def uninstall(self, prefix=None):
        return installer.uninstall(prefix or self.prefix)

    def assert_installed(self, prefix, binary):
        record = prefix / MANIFEST_PATH
        self.assertTrue(record.is_file())
        self.assertFalse(record.is_symlink())
        serialized = record.read_text(encoding="utf-8")
        json.loads(serialized)
        for relative in PAYLOAD_PATHS:
            path = prefix / relative
            self.assertTrue(path.is_file(), relative)
            self.assertFalse(path.is_symlink(), relative)
            self.assertIn(relative, serialized)
            self.assertIn(sha256(path.read_bytes()), serialized)
        self.assertEqual((prefix / "bin/zmcl").read_bytes(), binary.read_bytes())
        if os.name != "nt":
            self.assertTrue((prefix / "bin/zmcl").stat().st_mode & stat.S_IXUSR)

    def old_manifest(self, prefix=None):
        prefix = prefix or self.prefix
        return [
            {"path": relative, "sha256": sha256((prefix / relative).read_bytes())}
            for relative in PAYLOAD_PATHS
        ]

    def write_old_manifest(self, prefix=None):
        prefix = prefix or self.prefix
        record = prefix / MANIFEST_PATH
        record.write_text(json.dumps(self.old_manifest(prefix), indent=2), encoding="utf-8")

    def assert_refused_without_changes(self, action, prefix=None):
        prefix = prefix or self.prefix
        before = managed_state(prefix)
        with self.assertRaises(REFUSAL_ERRORS):
            action()
        self.assertEqual(managed_state(prefix), before)

    def symlink(self, target, link, *, directory=False):
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks are unavailable: {error}")

    def install_game_fixtures(self, prefix):
        fixtures = {
            "share/zmcl/instances/demo/game/saves/Keep/level.dat": b"synthetic save\x00\x01",
            "share/zmcl/instances/demo/game/options.txt": b"music:0.25\n",
            "share/zmcl/instances/demo/instance.json": b'{"id":"demo"}\n',
            "share/zmcl/unrelated-user-file.txt": b"keep this file\n",
        }
        for relative, body in fixtures.items():
            path = prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return fixtures

    def assert_game_fixtures(self, prefix, fixtures):
        for relative, expected in fixtures.items():
            self.assertEqual((prefix / relative).read_bytes(), expected, relative)

    def injected_failure(self, prefix, relative, error_type=OSError):
        original = installer.atomic_bytes
        target = prefix / relative
        failures = []

        def fail_once(path, data, mode=0o644):
            if Path(path) == target and not failures:
                failures.append(relative)
                raise error_type("injected installer write failure")
            return original(path, data, mode)

        return mock.patch.object(installer, "atomic_bytes", side_effect=fail_once), failures

    def test_fresh_install_and_reinstall(self):
        self.install()
        self.assert_installed(self.prefix, self.old_binary)
        before = {relative: file_state(self.prefix / relative) for relative in PAYLOAD_PATHS}
        self.install()
        self.assert_installed(self.prefix, self.old_binary)
        self.assertEqual(
            {relative: file_state(self.prefix / relative) for relative in PAYLOAD_PATHS},
            before,
        )

    def test_main_accepts_explicit_arguments(self):
        installer.main([
            "install", "--prefix", str(self.prefix), "--binary", str(self.old_binary),
        ])
        self.assert_installed(self.prefix, self.old_binary)
        installer.main(["uninstall", "--prefix", str(self.prefix)])
        self.assertEqual(managed_state(self.prefix), {path: ("absent",) for path in MANAGED_PATHS})

    def test_upgrade_accepts_legacy_manifest(self):
        self.install()
        self.write_old_manifest()
        game = self.install_game_fixtures(self.prefix)
        self.install(binary=self.new_binary)
        self.assert_installed(self.prefix, self.new_binary)
        self.assert_game_fixtures(self.prefix, game)

    def test_roundtrip_preserves_game_and_unrelated_files(self):
        game = self.install_game_fixtures(self.prefix)
        self.install()
        self.install(binary=self.new_binary)
        self.uninstall()
        self.assertEqual(managed_state(self.prefix), {path: ("absent",) for path in MANAGED_PATHS})
        self.assert_game_fixtures(self.prefix, game)
        self.uninstall()
        self.assert_game_fixtures(self.prefix, game)

    def test_uninstall_accepts_legacy_manifest(self):
        self.install()
        self.write_old_manifest()
        self.uninstall()
        self.assertEqual(managed_state(self.prefix), {path: ("absent",) for path in MANAGED_PATHS})

    def test_upgrade_preserves_modified_desktop(self):
        self.install()
        path = self.prefix / "share/applications/zmcl.desktop"
        path.write_bytes(path.read_bytes() + b"# user's custom desktop setting\n")
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))

    def test_upgrade_preserves_modified_binary(self):
        self.install()
        (self.prefix / "bin/zmcl").write_bytes(b"user supplied executable\n")
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))

    def test_install_refuses_unmanaged_files_even_if_bytes_match(self):
        for index, body in enumerate((b"unmanaged executable\n", self.old_binary.read_bytes())):
            with self.subTest(same_bytes=index == 1):
                prefix = self.root / f"unmanaged-{index}"
                target = prefix / "bin/zmcl"
                target.parent.mkdir(parents=True)
                target.write_bytes(body)
                self.assert_refused_without_changes(lambda: self.install(prefix), prefix)

    def test_icon_symlink_refusal_happens_before_binary_upgrade(self):
        self.install()
        icon = self.prefix / "share/icons/hicolor/scalable/apps/zmcl.svg"
        icon.unlink()
        outside = self.root / "outside-icon"
        outside.write_bytes(b"outside file\n")
        self.symlink(outside, icon)
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))
        self.assertEqual(outside.read_bytes(), b"outside file\n")
        original_record = (self.prefix / MANIFEST_PATH).read_bytes()
        with self.assertRaises(REFUSAL_ERRORS):
            self.uninstall()
        self.assertTrue(icon.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside file\n")
        self.assertEqual((self.prefix / MANIFEST_PATH).read_bytes(), original_record)
        self.assertFalse((self.prefix / "bin/zmcl").exists())
        self.assertFalse((self.prefix / "share/applications/zmcl.desktop").exists())

    def test_install_and_uninstall_refuse_symlink_parents(self):
        for index, relative in enumerate(("bin", "share", "share/icons", "share/applications", "share/zmcl")):
            with self.subTest(parent=relative):
                prefix = self.root / f"parent-prefix-{index}"
                self.install(prefix)
                parent = prefix / relative
                outside = self.root / f"outside-parent-{index}"
                shutil.move(str(parent), outside)
                self.symlink(outside, parent, directory=True)
                external_before = {
                    path.relative_to(outside): path.read_bytes()
                    for path in outside.rglob("*") if path.is_file()
                }
                self.assert_refused_without_changes(lambda: self.install(prefix, self.new_binary), prefix)
                self.assert_refused_without_changes(lambda: self.uninstall(prefix), prefix)
                self.assertEqual(
                    {path.relative_to(outside): path.read_bytes()
                     for path in outside.rglob("*") if path.is_file()},
                    external_before,
                )

    def test_install_and_uninstall_refuse_manifest_symlink(self):
        self.install()
        record = self.prefix / MANIFEST_PATH
        outside = self.root / "outside-manifest.json"
        outside.write_bytes(record.read_bytes())
        original = outside.read_bytes()
        record.unlink()
        self.symlink(outside, record)
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))
        self.assert_refused_without_changes(self.uninstall)
        self.assertEqual(outside.read_bytes(), original)

    def test_install_and_uninstall_refuse_directory_target(self):
        for index, relative in enumerate((*PAYLOAD_PATHS, MANIFEST_PATH)):
            with self.subTest(target=relative):
                prefix = self.root / f"directory-target-{index}"
                self.install(prefix)
                path = prefix / relative
                path.unlink()
                path.mkdir()
                child = path / "keep"
                child.write_bytes(b"do not remove\n")
                self.assert_refused_without_changes(lambda: self.install(prefix, self.new_binary), prefix)
                self.assert_refused_without_changes(lambda: self.uninstall(prefix), prefix)
                self.assertEqual(child.read_bytes(), b"do not remove\n")

    def test_malformed_manifest_is_checked_before_any_write_or_delete(self):
        # Place each malformed row after valid rows: a sequential validate/delete
        # implementation would already have removed managed files when it fails.
        cases = (
            ("traversal", lambda rows: rows + [{"path": "../outside", "sha256": "0" * 64}]),
            ("absolute", lambda rows: rows + [{"path": "/tmp/outside", "sha256": "0" * 64}]),
            ("unexpected", lambda rows: rows + [{"path": "share/zmcl/instances/demo/save", "sha256": "0" * 64}]),
            ("duplicate", lambda rows: rows + [dict(rows[0])]),
            ("duplicate-alias", lambda rows: rows + [{"path": "bin/./zmcl", "sha256": rows[0]["sha256"]}]),
            ("invalid-hash", lambda rows: rows[:-1] + [{"path": rows[-1]["path"], "sha256": "not-a-hash"}]),
            ("missing-hash", lambda rows: rows[:-1] + [{"path": rows[-1]["path"]}]),
            ("non-object", lambda rows: rows + ["not an entry"]),
        )
        for index, (name, change) in enumerate(cases):
            with self.subTest(case=name):
                prefix = self.root / f"bad-manifest-{index}"
                self.install(prefix)
                (prefix / MANIFEST_PATH).write_text(json.dumps(change(self.old_manifest(prefix))), encoding="utf-8")
                self.assert_refused_without_changes(lambda: self.install(prefix, self.new_binary), prefix)
                self.assert_refused_without_changes(lambda: self.uninstall(prefix), prefix)

    def test_invalid_manifest_json_does_not_change_installation(self):
        self.install()
        (self.prefix / MANIFEST_PATH).write_bytes(b"this is not JSON\n")
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))
        self.assert_refused_without_changes(self.uninstall)

    def test_uninstall_retains_modified_file_and_record(self):
        self.install()
        self.write_old_manifest()
        game = self.install_game_fixtures(self.prefix)
        path = self.prefix / "share/applications/zmcl.desktop"
        edited = path.read_bytes() + b"# local user edit\n"
        path.write_bytes(edited)
        with self.assertRaises(REFUSAL_ERRORS):
            self.uninstall()
        self.assertEqual(path.read_bytes(), edited)
        self.assertTrue((self.prefix / MANIFEST_PATH).is_file())
        self.assertFalse((self.prefix / "bin/zmcl").exists())
        self.assertFalse((self.prefix / "share/icons/hicolor/scalable/apps/zmcl.svg").exists())
        self.assert_game_fixtures(self.prefix, game)

    def test_failed_upgrade_restores_old_bytes_modes_and_manifest(self):
        for index, relative in enumerate(MANAGED_PATHS):
            with self.subTest(failed_write=relative):
                prefix = self.root / f"rollback-{index}"
                self.install(prefix)
                self.write_old_manifest(prefix)
                game = self.install_game_fixtures(prefix)
                if os.name != "nt":
                    for name, mode in zip(MANAGED_PATHS, (0o700, 0o600, 0o640, 0o640)):
                        (prefix / name).chmod(mode)
                before = managed_state(prefix)
                patch, failures = self.injected_failure(prefix, relative)
                with patch:
                    with self.assertRaises(REFUSAL_ERRORS):
                        self.install(prefix, self.new_binary)
                self.assertEqual(failures, [relative], "The intended write was not exercised")
                self.assertEqual(managed_state(prefix), before)
                self.assert_game_fixtures(prefix, game)
                # A handled write failure must not leave the prefix unusable.
                self.install(prefix, self.new_binary)
                self.assert_installed(prefix, self.new_binary)

    def test_failed_first_install_restores_absent_payload(self):
        for index, relative in enumerate(MANAGED_PATHS):
            with self.subTest(failed_write=relative):
                prefix = self.root / f"fresh-rollback-{index}"
                game = self.install_game_fixtures(prefix)
                before = managed_state(prefix)
                patch, failures = self.injected_failure(prefix, relative)
                with patch:
                    with self.assertRaises(REFUSAL_ERRORS):
                        self.install(prefix)
                self.assertEqual(failures, [relative], "The intended write was not exercised")
                self.assertEqual(managed_state(prefix), before)
                self.assert_game_fixtures(prefix, game)
                self.install(prefix)
                self.assert_installed(prefix, self.old_binary)

    def test_interrupt_during_upgrade_restores_old_installation(self):
        self.install()
        self.write_old_manifest()
        before = managed_state(self.prefix)
        relative = "share/applications/zmcl.desktop"
        patch, failures = self.injected_failure(self.prefix, relative, KeyboardInterrupt)
        with patch:
            with self.assertRaises(KeyboardInterrupt):
                self.install(binary=self.new_binary)
        self.assertEqual(failures, [relative])
        self.assertEqual(managed_state(self.prefix), before)
        self.install(binary=self.new_binary)
        self.assert_installed(self.prefix, self.new_binary)

    def test_failed_rollback_retains_old_bytes_for_manual_recovery(self):
        for error_type in (OSError, KeyboardInterrupt):
            with self.subTest(error=error_type.__name__):
                prefix = self.root / ("failed-rollback-" + error_type.__name__)
                self.install(prefix)
                self.write_old_manifest(prefix)
                original_record = (prefix / MANIFEST_PATH).read_bytes()
                old_bytes = self.old_binary.read_bytes()
                target = prefix / "bin/zmcl"
                original_replace = installer.os.replace
                interrupted = []

                def interrupt_restore(source, destination):
                    source = Path(source)
                    # Identify restoring the old executable by its bytes,
                    # without depending on temporary directory/file names.
                    if Path(destination) == target and source.read_bytes() == old_bytes and not interrupted:
                        interrupted.append(True)
                        raise error_type("interrupted old executable restoration")
                    return original_replace(source, destination)

                patch, failures = self.injected_failure(prefix, "share/applications/zmcl.desktop")
                with patch, mock.patch.object(installer.os, "replace", side_effect=interrupt_restore):
                    with self.assertRaises(RuntimeError) as caught:
                        self.install(prefix, self.new_binary)
                self.assertIn("rollback needs attention", str(caught.exception))
                self.assertTrue(failures)
                self.assertEqual(interrupted, [True])
                self.assertEqual((prefix / MANIFEST_PATH).read_bytes(), original_record)
                recoverable = [
                    path for path in prefix.rglob("*")
                    if path.is_file() and not path.is_symlink() and path.read_bytes() == old_bytes
                ]
                self.assertTrue(recoverable, "Failed rollback discarded every copy of the old executable")

    def lock_probe(self, *, exit_without_cleanup=False):
        code = """
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("zmcl_lock_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
try:
    with module.installation_lock(Path(sys.argv[2])):
        print("acquired", flush=True)
        if sys.argv[3] == "exit":
            os._exit(0)
except (OSError, RuntimeError):
    print("busy", flush=True)
    sys.exit(75)
"""
        return subprocess.run(
            [sys.executable, "-B", "-c", code, str(INSTALLER_PATH), str(self.prefix),
             "exit" if exit_without_cleanup else "return"],
            text=True, capture_output=True, timeout=10, check=False,
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux directory flock contract")
    def test_lock_refuses_another_process_and_releases_on_context_exit(self):
        with installer.installation_lock(self.prefix):
            competing = self.lock_probe()
            self.assertEqual(competing.returncode, 75, competing.stderr)
            self.assertEqual(competing.stdout.strip(), "busy")
        released = self.lock_probe()
        self.assertEqual(released.returncode, 0, released.stderr)
        self.assertEqual(released.stdout.strip(), "acquired")
        self.assertEqual(list(self.prefix.iterdir()), [])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux directory flock contract")
    def test_lock_releases_when_holder_process_exits_without_cleanup(self):
        holder = self.lock_probe(exit_without_cleanup=True)
        self.assertEqual(holder.returncode, 0, holder.stderr)
        self.assertEqual(holder.stdout.strip(), "acquired")
        with installer.installation_lock(self.prefix):
            self.assertEqual(list(self.prefix.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
