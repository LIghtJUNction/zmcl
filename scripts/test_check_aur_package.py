#!/usr/bin/env python3
"""Check the real PKGBUILD marker conversion and Arch acceptance helper.

Fixtures use a tiny native ELF and synthetic package metadata, not a released
application or a pacman installation. No source version/checksum is rewritten.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PKGBUILD = ROOT / "packaging/aur/zmcl-bin/PKGBUILD"
SPEC = importlib.util.spec_from_file_location("check_aur_package", ROOT / "scripts/check_aur_package.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def encoded(value):
    # Deliberately differs from the generated installed marker's whitespace.
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def portable_marker(version="7.8.9"):
    return {"schema": 1, "application": "zmcl", "version": version, "format": "portable",
            "packager": "zdistro", "packager_version": "0.1.0",
            "packager_commit": "6a54a137fc697d372b54ef85105315f2ce522b3d",
            "release_url": "https://github.com/LIghtJUNction/zmcl/releases/tag/v" + version}


class DistributionContract(unittest.TestCase):
    def setUp(self):
        self.original = portable_marker()
        self.manifest = {"version": "7.8.9", "distribution": self.original}
        self.installed = {**self.original, "format": "arch"}

    def test_only_format_changes_and_package_revision_is_separate(self):
        self.assertEqual(checker.verify_distribution(self.manifest, self.installed, "7.8.9-2.1"),
                         self.original)
        self.assertEqual(self.original["format"], "portable")

    def test_application_and_packager_versions_are_read_from_source(self):
        original = portable_marker("12.34.56")
        original.update(packager_version="3.2.1", packager_commit="a" * 40)
        manifest = {"version": "12.34.56", "distribution": original}
        self.assertEqual(checker.verify_distribution(
            manifest, {**original, "format": "arch"}, "12.34.56-1"), original)

    def test_changed_or_extra_arch_fields_are_rejected(self):
        for field, value in (
            ("version", "7.8.10"), ("packager_commit", "b" * 40),
            ("packager_version", "0.2.0"), ("release_url", "https://example.invalid/release"),
            ("format", "portable"), ("application", "other"), ("schema", True), ("unexpected", "x"),
        ):
            with self.subTest(field=field):
                changed = {**self.installed, field: value}
                with self.assertRaisesRegex(ValueError, "preserve every portable field"):
                    checker.verify_distribution(self.manifest, changed, "7.8.9-2")

    def test_invalid_portable_identity_is_not_accepted_as_arch_source(self):
        for field, value in (("format", "arch"), ("schema", True), ("release_url", "https://example.invalid"),
                             ("packager", "other"), ("packager_commit", "short")):
            with self.subTest(field=field):
                original = {**self.original, field: value}
                with self.assertRaisesRegex(ValueError, "portable release distribution"):
                    checker.verify_distribution(
                        {"version": "7.8.9", "distribution": original},
                        {**original, "format": "arch"}, "7.8.9-2")

    def test_package_version_comparison_is_exact(self):
        for package_version in ("7.8.90-1", "7.8.9-fake-1", "7.8.9", "7.8.9-invalid", "7.8.10-1"):
            with self.subTest(package_version=package_version):
                with self.assertRaisesRegex(ValueError, "versions differ"):
                    checker.verify_distribution(self.manifest, self.installed, package_version)

    def test_conversion_snippet_and_helper_parse_as_python_38(self):
        code = PKGBUILD.read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        ast.parse(code, feature_version=(3, 8))
        ast.parse((ROOT / "scripts/check_aur_package.py").read_text(), feature_version=(3, 8))


@unittest.skipUnless(platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64"),
                     "Arch fixture acceptance requires native Linux x86_64")
class NativeArchConversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in ("cc", "bash", "install", "bsdtar", "desktop-file-validate", "ldd"):
            if not shutil.which(name):
                raise unittest.SkipTest("Missing native test tool: " + name)
        retained = os.environ.get("ZMCL_TEST_AUR_EVIDENCE")
        if retained:
            parent = Path(retained).resolve()
            parent.mkdir(parents=True, exist_ok=True)
            cls.base = Path(tempfile.mkdtemp(prefix="aur-fixture-", dir=parent))
        else:
            cls.temporary = tempfile.TemporaryDirectory(prefix="zmcl-aur-test-")
            cls.base = Path(cls.temporary.name)
        c_file = cls.base / "fixture.c"
        c_file.write_text('#include <stdio.h>\n#include <string.h>\n'
                          '#ifndef VERSION\n#define VERSION "7.8.9"\n#endif\n'
                          'int main(int argc, char **argv) {\n'
                          '  if (argc == 2 && strcmp(argv[1], "--version") == 0) {\n'
                          '    puts("zmcl " VERSION); return 0;\n'
                          '  }\n  return 2;\n}\n', encoding="utf-8")
        cls.binary, cls.wrong_binary = cls.base / "zmcl", cls.base / "wrong-zmcl"
        subprocess.run(["cc", "-O2", "-s", str(c_file), "-o", str(cls.binary)], check=True)
        subprocess.run(["cc", "-O2", "-s", '-DVERSION="99.99.99"', str(c_file), "-o", str(cls.wrong_binary)],
                       check=True)
        print("Synthetic AUR conversion evidence:", cls.base)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def setUp(self):
        self.directory = self.base / self._testMethodName
        self.directory.mkdir()
        self.sources = self.directory / "sources"
        self.sources.mkdir()
        self.stage = self.directory / "package"
        self.stage.mkdir()
        self.source = self.sources / "zmcl-7.8.9-linux-x86_64"
        self.source.mkdir()
        for name in ("assets", "docs", "share/zmcl"):
            (self.source / name).mkdir(parents=True)
        shutil.copyfile(ROOT / "packaging/aur/zmcl-bin/zmcl.desktop", self.sources / "zmcl.desktop")
        shutil.copyfile(ROOT / "assets/zmcl.svg", self.source / "assets/zmcl.svg")
        shutil.copyfile(ROOT / "LICENSE", self.source / "LICENSE")
        (self.source / "README.md").write_text("# Synthetic fixture only\n")
        (self.source / "docs/FEATURES.md").write_text("# No real application or GUI acceptance\n")
        shutil.copyfile(self.binary, self.source / "zmcl")
        self.original_marker = portable_marker()
        self.write_release(self.original_marker)

    def write_release(self, marker, *, binary=None):
        if binary:
            shutil.copyfile(binary, self.source / "zmcl")
        body = encoded(marker)
        (self.source / "share/zmcl/distribution.json").write_bytes(body)
        binary_body = (self.source / "zmcl").read_bytes()
        release = {"schema": 1, "application": "zmcl", "version": "7.8.9",
                   "app_source_commit": "c" * 40, "distribution": marker,
                   "binary": {"path": "zmcl", "bytes": len(binary_body), "sha256": sha(binary_body)},
                   "files": [{"path": "share/zmcl/distribution.json", "bytes": len(body), "sha256": sha(body)}]}
        (self.source / "release.json").write_bytes(encoded(release))

    def convert(self, version="7.8.9"):
        # Source the actual recipe; only the isolated fixture's version and paths
        # are supplied as argv. The repository PKGBUILD/checksums stay unchanged.
        return subprocess.run(
            ["bash", "-e", "-c",
             'source "$1"; pkgver="$2"; CARCH=x86_64; srcdir="$3"; pkgdir="$4"; cd "$srcdir"; package',
             "fixture", str(PKGBUILD), version, str(self.sources), str(self.stage)],
            capture_output=True, text=True, timeout=30)

    def package(self, package_version="7.8.9-2"):
        # These minimal metadata records exercise this helper, not makepkg's
        # independent ALPM/signature/install acceptance.
        (self.stage / ".PKGINFO").write_text(
            "pkgname = zmcl-bin\npkgver = " + package_version + "\narch = x86_64\n")
        (self.stage / ".BUILDINFO").write_text("format = 2\npkgname = zmcl-bin\n")
        (self.stage / ".MTREE").write_text("# synthetic metadata fixture\n")
        archive = self.directory / "fixture.pkg.tar"
        subprocess.run(["bsdtar", "-cf", str(archive), "-C", str(self.stage),
                        *sorted(checker.METADATA), "usr"], check=True)
        return archive

    def test_converter_and_helper_preserve_portable_metadata_and_binary(self):
        before_release = (self.source / "release.json").read_bytes()
        before_marker = (self.source / "share/zmcl/distribution.json").read_bytes()
        result = self.convert()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.source / "release.json").read_bytes(), before_release)
        self.assertEqual((self.stage / "usr/share/doc/zmcl/release.json").read_bytes(), before_release)
        self.assertEqual((self.source / "share/zmcl/distribution.json").read_bytes(), before_marker)
        installed = (self.stage / "usr/share/zmcl/distribution.json").read_bytes()
        self.assertNotEqual(sha(installed), sha(before_marker))
        self.assertEqual(json.loads(installed), {**self.original_marker, "format": "arch"})
        report = checker.verify(self.package())
        self.assertEqual(report["source_distribution"]["format"], "portable")
        self.assertEqual(report["installed_distribution"]["format"], "arch")
        self.assertTrue(report["checks"]["distribution_version_matches_package_and_executable"])
        self.assertEqual(report["binary_sha256"], sha(self.binary.read_bytes()))
        (self.directory / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n")

    def test_converter_rejects_changed_source_marker_bytes(self):
        marker_path = self.source / "share/zmcl/distribution.json"
        marker_path.write_bytes(marker_path.read_bytes() + b" ")
        result = self.convert()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("marker digest differs", result.stderr)
        self.assertFalse((self.stage / "usr/share/zmcl/distribution.json").exists())

    def test_converter_rejects_nonportable_input_and_wrong_package_version(self):
        self.write_release({**self.original_marker, "format": "deb"})
        result = self.convert()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match this package", result.stderr)
        self.write_release(self.original_marker)
        self.source.rename(self.sources / "zmcl-7.8.10-linux-x86_64")
        result = self.convert("7.8.10")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match this package", result.stderr)

    def test_helper_rejects_changed_arch_hint_before_running_binary(self):
        result = self.convert()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.stage / "usr/share/zmcl/distribution.json"
        marker = json.loads(path.read_text())
        marker["packager_commit"] = "b" * 40
        path.write_bytes(encoded(marker))
        archive = self.package()
        with mock.patch.object(checker, "run", wraps=checker.run) as run:
            with self.assertRaisesRegex(ValueError, "preserve every portable field"):
                checker.verify(archive)
            self.assertFalse(any("--version" in call.args[0] for call in run.call_args_list))

    def test_helper_rejects_wrong_native_executable_version(self):
        self.write_release(self.original_marker, binary=self.wrong_binary)
        result = self.convert()
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(ValueError, "Wrong executable version"):
            checker.verify(self.package())

    def test_helper_rejects_package_version_that_only_shares_a_prefix(self):
        result = self.convert()
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(ValueError, "versions differ"):
            checker.verify(self.package("7.8.9-fake-2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
