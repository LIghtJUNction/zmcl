#!/usr/bin/env python3
"""Source and Debian version regressions using isolated real Git checkouts."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("zdistro_package.py")
SPEC = importlib.util.spec_from_file_location("zdistro_package", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


@unittest.skipUnless(shutil.which("git"), "Source identity checks need Git")
class SourceIdentity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zmcl identity with spaces ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("src/main.rs", "Cargo.toml", "rust-toolchain.toml", *packaging.PAYLOAD_PATHS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("tracked fixture\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("target/\nsrc/generated.rs\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Packaging fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic source identity fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, timeout=30).stdout

    def test_clean_inputs_allow_build_outputs_outside_source_paths(self):
        (self.root / "target").mkdir()
        (self.root / "target/release-output").write_bytes(b"ignored build output")
        result = packaging.source_identity(self.root, self.commit)
        self.assertEqual(result["commit"], self.commit)
        self.assertTrue(result["runtime_paths_match"])
        self.assertTrue(result["payload_paths_match"])
        self.assertTrue(result["build_paths_match"])

    def test_dirty_packaged_inputs_cannot_claim_clean_source_commit(self):
        for name in packaging.PAYLOAD_PATHS:
            with self.subTest(path=name):
                path = self.root / name
                before = path.read_bytes()
                try:
                    path.write_bytes(before + b"uncommitted payload\n")
                    with self.assertRaisesRegex(ValueError, "packaged inputs differ"):
                        packaging.source_identity(self.root, self.commit)
                finally:
                    path.write_bytes(before)

    def test_dirty_toolchain_is_rejected(self):
        (self.root / "rust-toolchain.toml").write_text("different toolchain\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "build configuration"):
            packaging.source_identity(self.root, self.commit)

    def test_ignored_source_file_is_rejected(self):
        (self.root / "src/generated.rs").write_text("fn generated() {}\n", encoding="utf-8")
        self.assertEqual(self.git("ls-files", "--others", "--exclude-standard", "--", "src"), "")
        with self.assertRaisesRegex(ValueError, "Ignored files"):
            packaging.source_identity(self.root, self.commit)

    def test_untracked_build_configuration_is_rejected(self):
        (self.root / ".cargo").mkdir()
        (self.root / ".cargo/config.toml").write_text("[build]\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Untracked"):
            packaging.source_identity(self.root, self.commit)


class DebianVersions(unittest.TestCase):
    def test_suffixes_cannot_silently_change_debian_upgrade_order(self):
        packaging.validate_targets({"system": "Linux", "version": "0.1.4"}, ["tar-gz", "deb"])
        for version in ("0.1.4-rc.1", "0.1.4+build.2"):
            with self.subTest(version=version):
                # Portable formats retain SemVer; only DEB needs an explicit mapping.
                packaging.validate_targets({"system": "Linux", "version": version}, ["tar-gz"])
                with self.assertRaisesRegex(ValueError, "SemVer suffixes are not mapped"):
                    packaging.validate_targets({"system": "Linux", "version": version}, ["deb"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
