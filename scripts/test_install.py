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
import shlex
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

EXECUTABLE_NAME = "zmcl.exe" if os.name == "nt" else "zmcl"
BINARY_PATH = "bin/" + EXECUTABLE_NAME
PAYLOAD_PATHS = (
    BINARY_PATH,
    "share/icons/hicolor/scalable/apps/zmcl.svg",
    "share/applications/zmcl.desktop",
)
MANIFEST_PATH = "share/zmcl/install.json"
INSTALLATION_PATH = "share/zmcl/installation.json"
DISTRIBUTION_PATH = "share/zmcl/distribution.json"
MANAGED_PATHS = (*PAYLOAD_PATHS, INSTALLATION_PATH, MANIFEST_PATH)
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
        self.assertEqual((prefix / BINARY_PATH).read_bytes(), binary.read_bytes())
        if os.name != "nt":
            self.assertTrue((prefix / BINARY_PATH).stat().st_mode & stat.S_IXUSR)

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
        # Recreate an actual old installation, which did not have these sidecars.
        for relative in (INSTALLATION_PATH, DISTRIBUTION_PATH):
            (prefix / relative).unlink(missing_ok=True)

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
        (self.prefix / BINARY_PATH).write_bytes(b"user supplied executable\n")
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))

    def test_install_refuses_unmanaged_files_even_if_bytes_match(self):
        for index, body in enumerate((b"unmanaged executable\n", self.old_binary.read_bytes())):
            with self.subTest(same_bytes=index == 1):
                prefix = self.root / f"unmanaged-{index}"
                target = prefix / BINARY_PATH
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
        self.assertFalse((self.prefix / BINARY_PATH).exists())
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
        for index, relative in enumerate(MANAGED_PATHS):
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
        self.assertFalse((self.prefix / BINARY_PATH).exists())
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
                    for name, mode in zip(MANAGED_PATHS, (0o700, 0o600, 0o640, 0o644, 0o640)):
                        if (prefix / name).exists():
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
                target = prefix / BINARY_PATH
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


    def package_fixture(self, version="0.1.2", with_marker=True, binary=None):
        package = self.root / ("package-" + version)
        (package / "assets").mkdir(parents=True, exist_ok=True)
        (package / "assets/zmcl.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        (package / "zmcl").write_bytes((binary or self.old_binary).read_bytes())
        if with_marker:
            marker = package / DISTRIBUTION_PATH
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({
                "schema": 1, "application": "zmcl", "version": version,
                "format": "portable", "packager": "zdistro", "packager_version": "0.1.0",
                "packager_commit": "6a54a137fc697d372b54ef85105315f2ce522b3d",
                "release_url": "https://github.com/LIghtJUNction/zmcl/releases/tag/v" + version,
            }), encoding="utf-8")
        self.write_release(package, version)
        return package

    def write_release(self, package, version):
        files = []
        for relative in ("zmcl", "assets/zmcl.svg", DISTRIBUTION_PATH):
            path = package / relative
            if path.exists():
                body = path.read_bytes()
                files.append({"path": relative, "bytes": len(body), "sha256": sha256(body)})
        (package / "release.json").write_text(json.dumps({
            "schema": 1, "application": "zmcl", "version": version,
            "binary": files[0], "files": files,
        }), encoding="utf-8")

    def installed_receipt(self):
        return json.loads((self.prefix / INSTALLATION_PATH).read_text(encoding="utf-8"))

    def test_receipt_is_checked_and_legacy_manifest_remains_a_list(self):
        self.install()
        self.assertEqual(self.installed_receipt(), {
            "schema": 1, "application": "zmcl", "version": "unknown",
            "channel": "portable", "executable": str(self.prefix / BINARY_PATH),
            "prefix": str(self.prefix), "release_url": None,
        })
        rows = json.loads((self.prefix / MANIFEST_PATH).read_text())
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(set(row), {"path", "sha256"})
            self.assertEqual(row["sha256"], sha256((self.prefix / row["path"]).read_bytes()))
        self.assertIn("Installed zmcl unknown", self.output.getvalue())

    def test_verified_distribution_is_copied_and_removed_by_roundtrip(self):
        package = self.package_fixture()
        game = self.install_game_fixtures(self.prefix)
        with mock.patch.object(installer, "ROOT", package):
            self.install(binary=package / "zmcl")
        self.assertEqual(self.installed_receipt()["version"], "0.1.2")
        self.assertEqual(self.installed_receipt()["release_url"],
                         "https://github.com/LIghtJUNction/zmcl/releases/tag/v0.1.2")
        marker = self.prefix / DISTRIBUTION_PATH
        self.assertEqual(marker.read_bytes(), (package / DISTRIBUTION_PATH).read_bytes())
        rows = json.loads((self.prefix / MANIFEST_PATH).read_text())
        self.assertEqual(len(rows), 5)
        self.assertIn({"path": DISTRIBUTION_PATH, "sha256": sha256(marker.read_bytes())}, rows)
        self.assertIn("Installed zmcl 0.1.2", self.output.getvalue())
        self.uninstall()
        self.assertFalse(marker.exists())
        self.assertFalse((self.prefix / INSTALLATION_PATH).exists())
        self.assert_game_fixtures(self.prefix, game)

    def test_verified_upgrade_refreshes_version_and_receipt(self):
        for version, binary in (("0.1.2", self.old_binary), ("0.1.3", self.new_binary)):
            package = self.package_fixture(version, binary=binary)
            with mock.patch.object(installer, "ROOT", package):
                self.install(binary=package / "zmcl")
            self.assertEqual(self.installed_receipt()["version"], version)
            self.assert_installed(self.prefix, binary)
        marker = json.loads((self.prefix / DISTRIBUTION_PATH).read_text())
        self.assertEqual(marker["version"], "0.1.3")

    def test_legacy_archive_without_marker_has_verified_version(self):
        package = self.package_fixture(with_marker=False)
        with mock.patch.object(installer, "ROOT", package):
            self.install(binary=package / "zmcl")
        self.assertEqual(self.installed_receipt()["version"], "0.1.2")
        self.assertFalse((self.prefix / DISTRIBUTION_PATH).exists())

    def test_incomplete_legacy_metadata_is_unknown(self):
        package = self.package_fixture(with_marker=False)
        (package / "release.json").write_text(json.dumps({
            "schema": 1, "application": "zmcl", "version": "99.0.0",
        }))
        with mock.patch.object(installer, "ROOT", package):
            self.install(binary=package / "zmcl")
        self.assertEqual(self.installed_receipt()["version"], "unknown")

    def test_checked_release_rejects_different_binary_before_any_write(self):
        package = self.package_fixture()
        with mock.patch.object(installer, "ROOT", package):
            self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))

    def test_changed_distribution_is_rejected_before_install(self):
        package = self.package_fixture()
        marker = package / DISTRIBUTION_PATH
        marker.write_bytes(marker.read_bytes() + b" ")
        with mock.patch.object(installer, "ROOT", package):
            self.assert_refused_without_changes(lambda: self.install(binary=package / "zmcl"))

    def test_unlisted_distribution_is_not_accepted_as_provenance(self):
        package = self.package_fixture()
        release = package / "release.json"
        metadata = json.loads(release.read_text())
        metadata["files"] = [entry for entry in metadata["files"] if entry["path"] != DISTRIBUTION_PATH]
        release.write_text(json.dumps(metadata))
        with mock.patch.object(installer, "ROOT", package):
            self.assert_refused_without_changes(lambda: self.install(binary=package / "zmcl"))

    def test_marker_requires_release_manifest(self):
        package = self.package_fixture()
        (package / "release.json").unlink()
        with mock.patch.object(installer, "ROOT", package):
            self.assert_refused_without_changes(lambda: self.install(binary=package / "zmcl"))

    def test_wrong_channel_version_and_release_url_are_rejected(self):
        for field, value in (
            ("format", "arch"), ("format", "deb"), ("version", "0.1.3"),
            ("application", "other"), ("release_url", "https://example.invalid/download"),
            ("schema", True), ("packager_commit", "not-a-commit"),
        ):
            with self.subTest(field=field, value=value):
                package = self.package_fixture("0.1.2-" + str(len(list(self.root.glob("package-*")))))
                marker = package / DISTRIBUTION_PATH
                data = json.loads(marker.read_text())
                version = data["version"]
                data[field] = value
                marker.write_text(json.dumps(data))
                self.write_release(package, version)
                with mock.patch.object(installer, "ROOT", package):
                    self.assert_refused_without_changes(lambda: self.install(binary=package / "zmcl"))

    def test_modified_receipt_is_preserved_during_upgrade_and_uninstall(self):
        self.install()
        game = self.install_game_fixtures(self.prefix)
        receipt = self.prefix / INSTALLATION_PATH
        changed = receipt.read_bytes() + b"\n"
        receipt.write_bytes(changed)
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))
        with self.assertRaisesRegex(RuntimeError, "Modified files preserved"):
            self.uninstall()
        self.assertEqual(receipt.read_bytes(), changed)
        self.assertTrue((self.prefix / MANIFEST_PATH).exists())
        self.assertFalse((self.prefix / BINARY_PATH).exists())
        self.assert_game_fixtures(self.prefix, game)

    def test_modified_distribution_is_preserved_even_when_new_source_has_no_marker(self):
        package = self.package_fixture()
        with mock.patch.object(installer, "ROOT", package):
            self.install(binary=package / "zmcl")
        marker = self.prefix / DISTRIBUTION_PATH
        changed = marker.read_bytes() + b"\n"
        marker.write_bytes(changed)
        self.assert_refused_without_changes(lambda: self.install(binary=self.new_binary))
        self.assertEqual(marker.read_bytes(), changed)
        with self.assertRaisesRegex(RuntimeError, "Modified files preserved"):
            self.uninstall()
        self.assertEqual(marker.read_bytes(), changed)
        self.assertTrue((self.prefix / MANIFEST_PATH).exists())

    def test_old_managed_marker_is_removed_when_installing_unlabelled_source(self):
        package = self.package_fixture()
        with mock.patch.object(installer, "ROOT", package):
            self.install(binary=package / "zmcl")
        self.install(binary=self.new_binary)
        self.assertFalse((self.prefix / DISTRIBUTION_PATH).exists())
        self.assertEqual(self.installed_receipt()["version"], "unknown")
        self.assertNotIn(DISTRIBUTION_PATH, installer.read_manifest(self.prefix / MANIFEST_PATH))

    def test_unmanaged_sidecars_are_never_adopted_or_removed(self):
        for index, relative in enumerate((INSTALLATION_PATH, DISTRIBUTION_PATH)):
            with self.subTest(path=relative):
                prefix = self.root / ("unmanaged-sidecar-" + str(index))
                path = prefix / relative
                path.parent.mkdir(parents=True)
                path.write_bytes(b"leave this local information alone\n")
                before = path.read_bytes()
                self.assert_refused_without_changes(lambda: self.install(prefix), prefix)
                self.assertEqual(path.read_bytes(), before)
                self.uninstall(prefix)
                self.assertEqual(path.read_bytes(), before)

    def test_path_shadow_reports_both_paths_without_running_either(self):
        other = self.root / "other bin"
        other.mkdir()
        selected = other / EXECUTABLE_NAME
        selected.write_bytes(b"not an executable program\n")
        selected.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join((str(other), str(self.prefix / "bin")))}):
            self.install()
        output = self.output.getvalue()
        self.assertIn("PATH selects another zmcl: " + str(selected), output)
        self.assertIn("This installation: " + str(self.prefix / BINARY_PATH), output)
        self.assertEqual(selected.read_bytes(), b"not an executable program\n")
        launch = next(line[len("Launch: "):] for line in output.splitlines() if line.startswith("Launch: "))
        self.assertEqual(launch, installer.display_command([self.prefix / BINARY_PATH, "gui"]))

    def test_matching_path_and_missing_path_have_useful_guidance(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.prefix / "bin")}):
            self.install()
        self.assertIn("PATH selects this installation", self.output.getvalue())
        self.output.seek(0)
        self.output.truncate()
        with mock.patch.dict(os.environ, {"PATH": str(self.root / "empty-bin")}):
            self.install()
        output = self.output.getvalue()
        self.assertIn("PATH does not contain zmcl", output)
        self.assertIn("Update: Download a newer release from https://github.com/LIghtJUNction/zmcl/releases", output)
        self.assertIn("scripts/install.py", output)
        self.assertIn("--prefix", output)

    def test_posix_completion_quotes_special_characters_as_literal_arguments(self):
        if os.name == "nt":
            self.skipTest("POSIX shell quoting")
        prefix = self.root / ("literal $HOME; " + chr(96) + "none" + chr(96) + " 'quotes'")
        self.install(prefix)
        output = self.output.getvalue()
        launch = next(line[len("Launch: "):] for line in output.splitlines() if line.startswith("Launch: "))
        self.assertEqual(shlex.split(launch), [str(prefix / BINARY_PATH), "gui"])
        update = next(line.strip() for line in output.splitlines() if line.startswith("  "))
        self.assertEqual(shlex.split(update)[-2:], ["--prefix", str(prefix)])



if __name__ == "__main__":
    unittest.main()
