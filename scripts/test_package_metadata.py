"""Regression checks for release metadata across native rendering backends."""
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


if __name__ == "__main__":
    unittest.main()
