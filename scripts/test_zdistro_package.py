#!/usr/bin/env python3
"""Release-boundary tests, including real pinned zdistro packaging of a tiny ELF.

Set ZMCL_TEST_ZDISTRO to the released v0.1.0 zipapp to run native format tests.
An optional ZMCL_TEST_ZDISTRO_EVIDENCE directory retains the synthetic fixture.
No real application compilation, system package installation or GUI is performed.
"""
import argparse
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPT = Path(__file__).with_name("zdistro_package.py")
SPEC = importlib.util.spec_from_file_location("zdistro_package", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)
ROOT = SCRIPT.parents[1]


class ReleaseBoundaries(unittest.TestCase):
    def test_wrong_tool_pin_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / "untrusted.pyz"
            tool.write_text("raise RuntimeError('must never execute')\n")
            with mock.patch.object(packaging, "run", side_effect=AssertionError("executed")):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    packaging.validate_tool(tool)

    def test_unknown_debian_library_fails_instead_of_omitting_dependency(self):
        release = {"runtime_dependencies": {"linux_direct_elf_needed": ["libFuture.so.99"]}}
        with self.assertRaisesRegex(ValueError, "Unmapped.*libFuture"):
            packaging.deb_dependencies(release)

    def test_debian_floors_use_binary_evidence_not_host_versions(self):
        release = {"runtime_dependencies": {
            "linux_direct_elf_needed": ["libc.so.6", "libfontconfig.so.1", "libX11.so.6"],
            "linux_direct_elf_minimum_glibc": "2.34",
            "linux_direct_elf_minimum_fontconfig": "2.17.0",
        }}
        self.assertEqual(packaging.deb_dependencies(release),
                         ["curl", "hicolor-icon-theme", "libc6 (>= 2.34)",
                          "libfontconfig1 (>= 2.17.0)", "libx11-6", "python3 (>= 3.8)"])

    def test_archive_traversal_and_links_are_rejected_without_extracting(self):
        body = b"payload"
        rows = [{"path": "zmcl", "bytes": len(body), "sha256": packaging.body_digest(body), "mode": 0o755}]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for kind in ("traversal", "symlink"):
                with self.subTest(kind=kind):
                    archive = base / (kind + ".tar.gz")
                    with tarfile.open(archive, "w:gz") as stream:
                        member = tarfile.TarInfo("../escaped" if kind == "traversal" else "zmcl")
                        member.mode = 0o755
                        if kind == "symlink":
                            member.type, member.linkname = tarfile.SYMTYPE, "../escaped"
                            stream.addfile(member)
                        else:
                            member.size = len(body)
                            stream.addfile(member, io.BytesIO(body))
                    with self.assertRaisesRegex(ValueError, "Unsafe|non-regular"):
                        packaging.read_archive(archive, rows)
                    self.assertFalse((base.parent / "escaped").exists())

    def test_posix_mode_gate_remains_strict_for_linux_payloads(self):
        body = b"fixture executable"
        rows = [{"path": "zmcl", "bytes": len(body), "sha256": packaging.body_digest(body), "mode": 0o755}]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "permissions.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                entry = zipfile.ZipInfo("zmcl")
                entry.create_system = 3
                entry.external_attr = (0o100644 << 16)
                stream.writestr(entry, body)
            with self.assertRaisesRegex(ValueError, "mode"):
                packaging.read_archive(archive, rows)
            # A byte/layout-only check does not imply Windows ACL or runtime validation.
            self.assertEqual(packaging.read_archive(archive, rows, check_modes=False)["zmcl"]["data"], body)

    def test_public_copy_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "public"
            items = [{"target": "zip"}, {"target": "zip"}]
            with self.assertRaisesRegex(ValueError, "collide"):
                packaging.publish_copies(items, destination,
                                         {"version": "0.1.4", "platform": "windows-amd64"}, Path(directory))
            self.assertEqual(list(destination.iterdir()), [])


@unittest.skipUnless(platform.system() == "Linux", "Native fixture tests run on Linux")
class NativeZdistroFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tool = os.environ.get("ZMCL_TEST_ZDISTRO")
        if not tool:
            raise unittest.SkipTest("Set ZMCL_TEST_ZDISTRO to run actual pinned CLI format checks")
        for command in ("cc", "git", "readelf", "dpkg-deb"):
            if not shutil.which(command):
                raise unittest.SkipTest("Missing native fixture tool: " + command)
        cls.tool = Path(tool).resolve()
        packaging.validate_tool(cls.tool)
        retained = os.environ.get("ZMCL_TEST_ZDISTRO_EVIDENCE")
        if retained:
            parent = Path(retained).resolve()
            parent.mkdir(parents=True, exist_ok=True)
            cls.directory = Path(tempfile.mkdtemp(prefix="fixture with spaces-", dir=parent))
        else:
            cls.temporary = tempfile.TemporaryDirectory(prefix="zmcl-zdistro-test-")
            cls.directory = Path(cls.temporary.name)
        cls.root = cls.directory / "source"
        cls.root.mkdir()
        for name in ("src", "scripts", "assets", "docs", "packaging/aur/zmcl-bin"):
            (cls.root / name).mkdir(parents=True)
        (cls.root / "Cargo.toml").write_text(
            '[package]\nname = "zmcl"\nversion = "0.1.3"\nlicense = "MIT"\n'
            'repository = "https://github.com/LIghtJUNction/zmcl"\n', encoding="utf-8")
        (cls.root / ".gitignore").write_text("build/\n", encoding="utf-8")
        (cls.root / "README.md").write_text("# Synthetic packaging fixture\n[Features](docs/FEATURES.md)\n", encoding="utf-8")
        (cls.root / "docs/FEATURES.md").write_text("# Fixture only; no launcher or GUI acceptance\n", encoding="utf-8")
        for name in ("LICENSE", "assets/zmcl.svg", "scripts/package.py", "scripts/install.py",
                     "packaging/aur/zmcl-bin/zmcl.desktop"):
            shutil.copyfile(ROOT / name, cls.root / name)
        fixture = cls.root / "src/main.c"
        fixture.write_text('#include <stdio.h>\n#include <string.h>\n'
                           '#ifndef VERSION\n#define VERSION "0.1.3"\n#endif\n'
                           'int main(int argc, char **argv) {\n'
                           '  if (argc == 2 && strcmp(argv[1], "--version") == 0) {\n'
                           '    puts("zmcl " VERSION); return 0;\n'
                           '  }\n  return 2;\n}\n', encoding="utf-8")
        def command(*argv):
            return packaging.run(argv, cwd=cls.root)
        command("git", "init", "-q")
        command("git", "add", ".")
        command("git", "-c", "user.name=Packaging Fixture", "-c",
                "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic packaging fixture")
        cls.commit = command("git", "rev-parse", "HEAD").strip()
        (cls.root / "build").mkdir()
        cls.binary = cls.root / "build/zmcl"
        cls.wrong_version = cls.root / "build/wrong-version"
        command("cc", "-O2", "-s", fixture, "-o", cls.binary)
        command("cc", "-O2", "-s", '-DVERSION="9.9.9"', fixture, "-o", cls.wrong_version)
        cls.args = argparse.Namespace(
            source_root=cls.root, source_commit=cls.commit, binary=cls.binary,
            expected_sha256=packaging.digest(cls.binary), zdistro=cls.tool,
            target=["tar-gz", "deb", "zip"], output=cls.directory / "result", timeout=120)
        cls.summary = packaging.package_release(cls.args)
        cls.work = cls.args.output / "evidence"
        cls.manifest = cls.work / "zdistro.toml"
        cls.build_result = packaging.read_json(cls.work / "03-build.stdout.json")
        cls.identity = cls.summary["binary"]
        cls.records = {item["target"]: item for item in cls.summary["artifacts"]}
        # Derive the expected staged bytes from the immutable manifest used by the real CLI.
        manifest = packaging.tomllib.loads(cls.manifest.read_text())
        cls.staged = {}
        for target, config in manifest["targets"].items():
            cls.staged[target] = {
                rule["destination"]: {"data": (cls.work / rule["source"]).read_bytes(),
                                      "mode": int(rule["mode"], 8)} for rule in config["files"]}
        print("Synthetic native package evidence:", cls.args.output)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_legacy_tar_root_installer_marker_and_source_identity(self):
        item = self.records["tar-gz"]
        path = self.args.output / "public" / item["file"]
        prefix = "zmcl-0.1.3-" + self.identity["platform"]
        with tarfile.open(path) as archive:
            names = {member.name for member in archive if member.isfile()}
            self.assertIn(prefix + "/scripts/install.py", names)
            self.assertIn(prefix + "/share/zmcl/distribution.json", names)
            marker = json.load(archive.extractfile(prefix + "/share/zmcl/distribution.json"))
            release = json.load(archive.extractfile(prefix + "/release.json"))
        self.assertEqual(marker, packaging.distribution_marker("0.1.3", "portable"))
        self.assertEqual(release["app_source_commit"], self.commit)
        self.assertEqual(release["binary"]["sha256"], self.args.expected_sha256)
        self.assertEqual(release["runtime_dependencies"], self.summary["runtime_dependencies"])
        self.assertTrue(all(name.startswith(prefix + "/") for name in names))
        self.assertEqual(self.summary["checks"]["installed"], False)

    def test_real_zip_keeps_payload_layout_without_claiming_windows_runtime(self):
        item = self.records["zip"]
        path = self.args.output / "public" / item["file"]
        with zipfile.ZipFile(path) as archive:
            prefix = "zmcl-0.1.3-" + self.identity["platform"]
            self.assertIn(prefix + "/zmcl", archive.namelist())
            marker = json.loads(archive.read(prefix + "/share/zmcl/distribution.json"))
        self.assertEqual(marker["format"], "portable")
        self.assertTrue(self.summary["platform"].startswith("linux-"))
        self.assertFalse(self.summary["checks"]["gui_started"])

    def test_deb_extracts_same_native_binary_marker_and_desktop_entry(self):
        artifact = self.args.output / "public" / self.records["deb"]["file"]
        destination = self.directory / "deb-private-extract"
        packaging.run(["dpkg-deb", "--extract", artifact, destination])
        self.assertEqual(packaging.run([destination / "usr/bin/zmcl", "--version"]).strip(), "zmcl 0.1.3")
        self.assertEqual(packaging.digest(destination / "usr/bin/zmcl"), self.args.expected_sha256)
        marker = packaging.read_json(destination / "usr/share/zmcl/distribution.json")
        self.assertEqual(marker, packaging.distribution_marker("0.1.3", "deb"))
        self.assertIn("Exec=zmcl gui", (destination / "usr/share/applications/zmcl.desktop").read_text())
        self.assertFalse((destination / "usr/bin/install.py").exists())

    def test_all_targets_preserve_original_receipts_and_unique_public_mapping(self):
        self.assertEqual(set(self.records), {"tar-gz", "deb", "zip"})
        self.assertEqual(len({item["file"] for item in self.records.values()}), 3)
        for item in self.records.values():
            with self.subTest(target=item["target"]):
                receipt = self.work / item["original_receipt"]
                original = self.work / item["original_artifact"]
                public = self.args.output / "public" / item["file"]
                data = packaging.read_json(receipt)
                self.assertEqual(packaging.digest(receipt), item["receipt_sha256"])
                self.assertEqual(packaging.digest(original), packaging.digest(public))
                self.assertEqual(data["artifact"], original.name)
                self.assertFalse(data["installed"])
                self.assertFalse(data["published"])
                self.assertEqual(data["signing"], "not performed by zdistro")

    def test_missing_duplicate_and_skipped_targets_fail_closed(self):
        cases = []
        missing = copy.deepcopy(self.build_result)
        missing["built"].pop()
        cases.append((missing, "missing"))
        duplicate = copy.deepcopy(self.build_result)
        duplicate["built"][-1] = duplicate["built"][0]
        cases.append((duplicate, "duplicate"))
        skipped = copy.deepcopy(self.build_result)
        skipped["skipped"] = [{"target": "deb", "ready": False}]
        cases.append((skipped, "skipped"))
        for changed, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, "missing|duplicate|skipped"):
                    packaging.validate_build(changed, self.args.target, self.manifest,
                                             self.work / "artifacts", self.staged, self.identity)

    def test_manifest_receipt_and_artifact_tampering_fail_real_cli_reverify(self):
        item = self.records["tar-gz"]
        original_receipt = self.work / item["original_receipt"]
        original_artifact = self.work / item["original_artifact"]
        for kind in ("manifest", "receipt", "artifact"):
            with self.subTest(kind=kind):
                directory = self.directory / ("tamper-" + kind)
                directory.mkdir()
                receipt = directory / "receipt.json"
                artifact = directory / original_artifact.name
                manifest = directory / "zdistro.toml"
                shutil.copyfile(original_receipt, receipt)
                shutil.copyfile(original_artifact, artifact)
                shutil.copyfile(self.manifest, manifest)
                if kind == "manifest":
                    with manifest.open("a") as stream:
                        stream.write("\n# changed original manifest\n")
                elif kind == "receipt":
                    data = packaging.read_json(receipt)
                    data["sha256"] = "0" * 64
                    receipt.write_text(json.dumps(data))
                else:
                    with artifact.open("ab") as stream:
                        stream.write(b"tampered")
                result = subprocess.run([os.sys.executable, "-I", "-S", self.tool, "verify",
                                         "--manifest", manifest, receipt],
                                        capture_output=True, text=True, timeout=30)
                # v0.1.0's released zipapp drops main()'s nonzero return code.
                # The wrapper must reject the error record even when exit is zero.
                self.assertIn(result.returncode, (0, 2), result.stdout)
                self.assertEqual(json.loads(result.stderr)["status"], "error")
                self.assertRegex(result.stderr, "manifest changed|SHA-256")
                client = packaging.Zdistro(self.tool, directory, manifest, 30)
                with self.assertRaisesRegex(ValueError, "manifest changed|SHA-256"):
                    client.call("verify", receipt)
        self.assertEqual(packaging.digest(original_receipt), item["receipt_sha256"])

    def test_binary_digest_version_and_native_format_are_checked(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            packaging.validate_binary(self.root, self.binary, "0" * 64)
        with self.assertRaisesRegex(ValueError, "--version"):
            packaging.validate_binary(self.root, self.wrong_version, packaging.digest(self.wrong_version))
        fake = self.root / "build/not-native"
        fake.write_text("#!/bin/sh\necho zmcl 0.1.3\n")
        fake.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "ELF"):
            packaging.validate_binary(self.root, fake, packaging.digest(fake))

    def test_dirty_or_untracked_runtime_sources_are_rejected(self):
        path = self.root / "src/main.c"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"/* changed after declared source commit */\n")
            with self.assertRaisesRegex(ValueError, "differ"):
                packaging.source_identity(self.root, self.commit)
        finally:
            path.write_bytes(original)
        untracked = self.root / "src/not-in-commit.c"
        try:
            untracked.write_text("/* untracked runtime source */\n")
            with self.assertRaisesRegex(ValueError, "Untracked"):
                packaging.source_identity(self.root, self.commit)
        finally:
            untracked.unlink()

    def test_cli_bad_binary_hash_exits_nonzero_without_publication(self):
        output = self.directory / "must-not-publish"
        result = subprocess.run(
            [os.sys.executable, str(SCRIPT), "--binary", str(self.binary),
             "--source-root", str(self.root), "--source-commit", self.commit,
             "--expected-sha256", "0" * 64, "--zdistro", str(self.tool),
             "--target", "tar-gz", "--output", str(output)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Frozen binary SHA-256 mismatch", result.stderr)
        self.assertFalse(output.exists())

    def test_existing_evidence_directory_is_not_overwritten(self):
        before = packaging.digest(self.args.output / "release-result.json")
        with self.assertRaisesRegex(ValueError, "new directory"):
            packaging.package_release(self.args)
        self.assertEqual(packaging.digest(self.args.output / "release-result.json"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
