"""Regression checks for release metadata across native rendering backends."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import package as packager


class RuntimeDependencyTests(unittest.TestCase):
    def metadata(self, system, libraries):
        glibc = {"minimum_version": "2.43", "required_symbol_versions": ["2.2.5", "2.43"]}
        with patch.object(packager.platform, "system", return_value=system):
            return packager.runtime_dependencies(libraries, glibc if system == "Linux" else None)

    def test_linux_cpu_does_not_require_graphics_driver(self):
        libraries = ["libX11.so.6", "libc.so.6", "libfreetype.so.6", "libfontconfig.so.1"]
        metadata = self.metadata("Linux", libraries)
        self.assertEqual(metadata["linux_direct_elf_needed"], libraries)
        self.assertEqual(metadata["linux_direct_elf_minimum_glibc"], "2.43")
        text = metadata["native_gui"]
        self.assertIn("CPU software rendering", text)
        self.assertIn("no direct OpenGL ELF dependency", text)
        self.assertIn("X11 or XWayland", text)
        self.assertIn("FreeType, Fontconfig", text)
        self.assertNotIn("graphics driver", text)

    def test_linux_opengl_keeps_driver_requirement(self):
        for library in ("libGL.so.1", "libOpenGL.so.0", "libGLX.so.0", "libEGL.so.1"):
            with self.subTest(library=library):
                text = self.metadata("Linux", ["libX11.so.6", library])["native_gui"]
                self.assertIn("OpenGL rendering and a graphics driver", text)
                self.assertNotIn("CPU software rendering", text)

    def test_linux_cli_does_not_claim_a_native_renderer(self):
        text = self.metadata("Linux", ["libc.so.6", "libgcc_s.so.1"])["native_gui"]
        self.assertIn("No directly linked X11 or OpenGL GUI library detected", text)
        self.assertNotIn("CPU software rendering", text)
        self.assertNotIn("graphics driver", text)

    def test_fonts_follow_the_actual_elf_list(self):
        text = self.metadata("Linux", ["libX11.so.6", "libfreetype.so.6"])["native_gui"]
        self.assertIn("FreeType", text)
        self.assertNotIn("Fontconfig", text)

    def test_missing_inspection_does_not_claim_software_rendering(self):
        text = self.metadata("Linux", None)["native_gui"]
        self.assertIn("not inspected", text)
        self.assertNotIn("CPU software rendering", text)

    def test_windows_keeps_native_system_libraries(self):
        text = self.metadata("Windows", None)["native_gui"]
        self.assertIn("Win32 (User32/GDI32)", text)
        self.assertIn("OpenGL system libraries", text)
        self.assertNotIn("XWayland", text)

    def test_macos_keeps_native_frameworks(self):
        text = self.metadata("Darwin", None)["native_gui"]
        self.assertIn("AppKit", text)
        self.assertIn("OpenGL frameworks", text)
        self.assertNotIn("XWayland", text)


class FontconfigAbiTests(unittest.TestCase):
    def inspect(self, symbol_rows):
        result = packager.subprocess.CompletedProcess([], 0, stdout=symbol_rows, stderr="")
        with patch.object(packager.platform, "system", return_value="Linux"), \
                patch.object(packager.subprocess, "run", return_value=result):
            return packager.fontconfig_requirements("zmcl")

    def test_new_mandatory_import_sets_binary_minimum(self):
        requirements = self.inspect(
            "49: 0000000000000000 0 FUNC GLOBAL DEFAULT UND FcConfigSetDefaultSubstitute\n"
        )
        self.assertEqual(requirements["minimum_version"], "2.17.0")
        self.assertEqual(requirements["symbol_version_basis"],
                         {"FcConfigSetDefaultSubstitute": "2.17.0"})
        with patch.object(packager.platform, "system", return_value="Linux"):
            metadata = packager.runtime_dependencies(
                ["libX11.so.6", "libfontconfig.so.1"], None, requirements
            )
        self.assertEqual(metadata["linux_direct_elf_minimum_fontconfig"], "2.17.0")
        self.assertEqual(metadata["linux_direct_elf_fontconfig_version_basis"],
                         requirements["symbol_version_basis"])
        self.assertIn("Fontconfig >= 2.17.0", metadata["native_gui"])
        self.assertIn("Source-level fallbacks", metadata["linux_fontconfig_scope"])

    def test_old_api_does_not_invent_new_minimum(self):
        requirements = self.inspect(
            "49: 0000000000000000 0 FUNC GLOBAL DEFAULT UND FcDefaultSubstitute\n"
        )
        self.assertIsNone(requirements["minimum_version"])
        self.assertEqual(requirements["symbol_version_basis"], {})
        with patch.object(packager.platform, "system", return_value="Linux"):
            metadata = packager.runtime_dependencies(
                ["libX11.so.6", "libfontconfig.so.1"], None, requirements
            )
        self.assertIsNone(metadata["linux_direct_elf_minimum_fontconfig"])
        self.assertEqual(metadata["linux_direct_elf_fontconfig_version_basis"], {})
        self.assertIn("Fontconfig", metadata["native_gui"])
        self.assertNotIn("Fontconfig >=", metadata["native_gui"])

    def test_defined_weak_and_similarly_named_symbols_do_not_set_minimum(self):
        for row in (
            "49: 0000000000001000 25 FUNC GLOBAL DEFAULT 12 FcConfigSetDefaultSubstitute",
            "49: 0000000000000000 0 FUNC WEAK DEFAULT UND FcConfigSetDefaultSubstitute",
            "49: 0000000000000000 0 FUNC GLOBAL DEFAULT UND FcConfigSetDefaultSubstituteOther",
        ):
            with self.subTest(row=row):
                self.assertIsNone(self.inspect(row)["minimum_version"])

    def test_version_suffix_preserves_required_symbol_identity(self):
        requirements = self.inspect(
            "49: 0000000000000000 0 FUNC GLOBAL DEFAULT UND "
            "FcConfigSetDefaultSubstitute@FONTCONFIG_2.17.0 (2)\n"
        )
        self.assertEqual(requirements["minimum_version"], "2.17.0")

    def test_failed_inspection_is_not_reported_as_no_requirement(self):
        result = packager.subprocess.CompletedProcess([], 1, stdout="", stderr="invalid ELF")
        with patch.object(packager.platform, "system", return_value="Linux"), \
                patch.object(packager.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "ELF dynamic symbols"):
                packager.fontconfig_requirements("zmcl")

    def test_other_platforms_do_not_run_elf_inspection(self):
        for system in ("Windows", "Darwin"):
            with self.subTest(system=system), \
                    patch.object(packager.platform, "system", return_value=system), \
                    patch.object(packager.subprocess, "run") as run:
                self.assertIsNone(packager.fontconfig_requirements("zmcl"))
                run.assert_not_called()


@unittest.skipUnless(sys.platform.startswith("linux"), "Private-prefix install acceptance is Linux-only")
class PortableInstallAcceptanceTests(unittest.TestCase):
    def check_roundtrip(self, with_marker):
        version = "0.1.2"
        files = {
            "zmcl": {"data": b"#!/bin/sh\nprintf 'zmcl 0.1.2\\n'\n", "mode": 0o755},
            "assets/zmcl.svg": {"data": b"<svg xmlns='http://www.w3.org/2000/svg'/>", "mode": 0o644},
            "scripts/install.py": {"data": Path(__file__).with_name("install.py").read_bytes(), "mode": 0o755},
        }
        if with_marker:
            files["share/zmcl/distribution.json"] = {"data": json.dumps({
                "schema": 1, "application": "zmcl", "version": version,
                "format": "portable", "packager": "zdistro", "packager_version": "0.1.0",
                "packager_commit": "6a54a137fc697d372b54ef85105315f2ce522b3d",
                "release_url": "https://github.com/LIghtJUNction/zmcl/releases/tag/v" + version,
            }).encode(), "mode": 0o644}
        entries = [{"path": name, "bytes": len(value["data"]),
                    "sha256": hashlib.sha256(value["data"]).hexdigest()}
                   for name, value in sorted(files.items())]
        release = {
            "schema": 1, "application": "zmcl", "version": version,
            "binary": next(entry for entry in entries if entry["path"] == "zmcl"),
            "files": entries,
        }
        files["release.json"] = {"data": json.dumps(release).encode(), "mode": 0o644}
        contents = {name: value["data"] for name, value in files.items()}
        with tempfile.TemporaryDirectory(prefix="zmcl-package-install-test-") as directory:
            with patch.object(packager, "ROOT", Path(directory)):
                result = packager.verify_install(contents, files, version)
        self.assertTrue(result["clean_install_uninstall"]["passed"])
        self.assertTrue(result["modified_file_protection"]["passed"])
        installed = result["clean_install_uninstall"]["installed_files"]
        paths = {row["path"] for row in installed}
        self.assertIn("share/zmcl/installation.json", paths)
        self.assertEqual("share/zmcl/distribution.json" in paths, with_marker)
        self.assertEqual(len(paths), 6 if with_marker else 5)
        self.assertFalse(result["gui_started"])
        self.assertFalse(result["game_started"])

    def test_legacy_payload_and_new_receipt_roundtrip(self):
        self.check_roundtrip(with_marker=False)

    def test_zdistro_payload_and_both_sidecars_roundtrip(self):
        self.check_roundtrip(with_marker=True)


if __name__ == "__main__":
    unittest.main()
