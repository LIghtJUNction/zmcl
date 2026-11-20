#!/usr/bin/env python3
"""Manual native Windows distribution acceptance, without publication or signing.

The workflow supplies an exact source commit and installs the Rust toolchain
declared by that source. All runtime checks use private synthetic data. A missing
interactive desktop or failed screenshot is a failed GUI gate, never a pass.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-msvc"
PAYLOAD = {"zmcl.exe", "README.md", "LICENSE", "docs/FEATURES.md", "assets/zmcl.svg",
           "scripts/install.py", "release.json", "share/zmcl/distribution.json"}
MANAGED = {"bin/zmcl.exe", "share/applications/zmcl.desktop",
           "share/icons/hicolor/scalable/apps/zmcl.svg", "share/zmcl/distribution.json",
           "share/zmcl/installation.json"}
REPOSITORY = "https://github.com/LIghtJUNction/zmcl"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def valid_commit(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value),
            "source_commit must be exactly 40 lowercase hexadecimal characters")
    return value


def toolchain(root=ROOT):
    data = tomllib.loads((root / "rust-toolchain.toml").read_text(encoding="utf-8"))["toolchain"]
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", data["channel"]),
            "Windows distribution requires the repository's fixed Rust release")
    require(data.get("profile", "minimal") == "minimal", "Unexpected Rust toolchain profile")
    components = data.get("components", [])
    require(isinstance(components, list) and all(
        isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]*", name) for name in components),
        "Invalid Rust component list")
    return {"channel": data["channel"], "components": ",".join(components), "target": TARGET}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "Required source helper is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pe_dependencies(text):
    names = sorted(set(re.findall(r"\b[A-Za-z0-9_.-]+\.dll\b", text, re.IGNORECASE)), key=str.casefold)
    require(names, "dumpbin did not report the native executable's direct DLL imports")
    require(not any(re.match(r"(?:vcruntime|msvcp)[0-9]", name, re.IGNORECASE) for name in names),
            "Portable executable still requires an external MSVC runtime DLL")
    return {"direct_dll_imports": names,
            "scope": "Direct PE imports only; external Python/curl/Java helpers and graphics drivers remain separate."}


def resolve_dumpbin(env):
    # Windows CreateProcess does not use a supplied child env PATH to find argv[0].
    # Resolve from the prepared MSVC environment; never fall back to the parent PATH/CWD.
    paths = [value for key, value in env.items() if key.casefold() == "path"]
    require(len(paths) == 1 and isinstance(paths[0], str) and paths[0],
            "Prepared MSVC environment must contain one nonempty PATH")
    for entry in paths[0].split(os.pathsep):
        directory = Path(entry.strip().strip('"'))
        if not directory.is_absolute():
            continue
        executable = directory / "dumpbin.exe"
        if executable.is_file():
            resolved = executable.resolve(strict=True)
            require(resolved.is_absolute(), "Resolved dumpbin.exe path must be absolute")
            return resolved
    raise ValueError("Prepared MSVC PATH does not contain an existing absolute dumpbin.exe")


class Evidence:
    def __init__(self, output, source_commit):
        output.mkdir(parents=True, exist_ok=False)
        self.output, self.step_number = output, 0
        self.report = {
            "schema": 1, "source_commit": source_commit, "platform": platform.platform(),
            "machine": platform.machine(), "target": TARGET, "passed": False,
            "checks": {}, "published": False, "signed": False,
            "scope": "Native Windows MSVC build, real ZIP and portable-install acceptance, "
                     "bounded native GUI with client-area screenshot, and synthetic data preservation. "
                     "No Minecraft, account sign-in, real game data, release publication or signing.",
        }
        self.save()

    def save(self):
        write_json(self.output / "windows-acceptance.json", self.report)

    def check(self, name, operation):
        self.report["checks"][name] = {"status": "running"}
        self.save()
        try:
            details = operation()
        except Exception as error:
            self.report["checks"][name] = {"status": "failed", "error": str(error)}
            self.report["failed_stage"] = name
            self.save()
            raise
        self.report["checks"][name] = {"status": "passed", "details": details}
        self.save()
        return details

    def command(self, label, argv, *, env=None, cwd=ROOT, timeout=120):
        self.step_number += 1
        prefix = self.output / (f"{self.step_number:02d}-" + label)
        try:
            result = subprocess.run([str(item) for item in argv], cwd=cwd, env=env,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            for channel, body in (("stdout", error.stdout), ("stderr", error.stderr)):
                if isinstance(body, bytes):
                    body = body.decode("utf-8", "replace")
                prefix.with_suffix("." + channel + ".txt").write_text(body or "", encoding="utf-8")
            raise RuntimeError(f"{label} timed out after {timeout} seconds") from error
        prefix.with_suffix(".stdout.txt").write_text(result.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(result.stderr, encoding="utf-8")
        require(result.returncode == 0, f"{label} failed with exit {result.returncode}: "
                + result.stderr[-4000:])
        return result.stdout


def extract_zip(archive, destination, root_name):
    """Check exact Windows portable layout before writing any extracted file."""
    rows, seen = [], set()
    with zipfile.ZipFile(archive) as stream:
        for entry in stream.infolist():
            require(entry.filename.startswith(root_name + "/"), "Unexpected ZIP root")
            relative = entry.filename[len(root_name) + 1:]
            require(relative in PAYLOAD and relative not in seen and not entry.is_dir()
                    and stat.S_ISREG(entry.external_attr >> 16) and not entry.flag_bits & 1,
                    "Unexpected, duplicate or non-regular ZIP entry")
            require(0 <= entry.file_size <= 256 * 1024 * 1024, "ZIP member exceeds the size limit")
            seen.add(relative)
            rows.append((entry, relative))
        require(seen == PAYLOAD and sum(entry.file_size for entry, _ in rows) <= 512 * 1024 * 1024,
                "ZIP payload is missing files or exceeds the size limit")
        destination.mkdir(parents=True, exist_ok=False)
        for entry, relative in rows:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with stream.open(entry) as source:
                body = source.read(entry.file_size + 1)
            require(len(body) == entry.file_size, "ZIP member size mismatch")
            path.write_bytes(body)
    return destination


def verify_extracted(root, version, binary_sha256, source_commit):
    release = json.loads((root / "release.json").read_text(encoding="utf-8"))
    marker = json.loads((root / "share/zmcl/distribution.json").read_text(encoding="utf-8"))
    require(release["version"] == version and release["app_source_commit"] == source_commit
            and release["binary"]["path"] == "zmcl.exe"
            and release["binary"]["sha256"] == binary_sha256
            and release["binary"]["bytes"] == (root / "zmcl.exe").stat().st_size
            and sha256(root / "zmcl.exe") == binary_sha256, "ZIP release/binary identity mismatch")
    expected_files = {entry["path"]: entry for entry in release["files"]}
    require(len(expected_files) == len(release["files"])
            and set(expected_files) == PAYLOAD - {"release.json"}, "release.json file set mismatch")
    for name, row in expected_files.items():
        file = root / name
        require(file.stat().st_size == row["bytes"] and sha256(file) == row["sha256"],
                "ZIP payload differs from release.json: " + name)
    require(marker == release["distribution"] and type(marker.get("schema")) is int
            and marker["schema"] == 1 and marker["application"] == "zmcl"
            and marker["version"] == version and marker["format"] == "portable"
            and marker["packager"] == "zdistro"
            and marker["release_url"] == REPOSITORY + "/releases/tag/v" + version,
            "ZIP distribution marker identity mismatch")
    return marker


def verified_managed_paths(prefix):
    managed = json.loads((prefix / "share/zmcl/install.json").read_text(encoding="utf-8"))
    require(isinstance(managed, list) and managed, "Missing managed installation manifest")
    names = set()
    for item in managed:
        require(isinstance(item, dict) and set(item) == {"path", "sha256"},
                "Unexpected managed installation entry fields")
        name = item["path"]
        require(isinstance(name, str), "Managed installation path is not a string")
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and ".." not in relative.parts
                and str(relative) == name and "\\" not in name and ":" not in name
                and name not in names, "Unsafe or duplicate managed installation path")
        names.add(name)
        require(sha256(prefix / name) == item["sha256"], "Managed installed file digest mismatch")
    require(names == MANAGED, "Windows installation must manage exactly the five expected files")
    return sorted(names)


def png_rgb(width, height, rgb):
    require(width > 0 and height > 0 and len(rgb) == width * height * 3, "Invalid screenshot pixels")
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    scanlines = b"".join(b"\0" + rgb[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))


class NativeWindow:
    """Operate only the window owned by the just-launched smoke process."""
    def __init__(self):
        require(os.name == "nt", "Win32 screenshot acceptance requires native Windows")
        from ctypes import wintypes as w
        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        specifications = [
            (self.user, "EnumWindows", [self.callback_type, w.LPARAM], w.BOOL),
            (self.user, "GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (self.user, "IsWindowVisible", [w.HWND], w.BOOL),
            (self.user, "GetWindowTextW", [w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            (self.user, "GetClientRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (self.user, "ClientToScreen", [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            (self.user, "SetWindowPos", [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, w.UINT], w.BOOL),
            (self.user, "SetForegroundWindow", [w.HWND], w.BOOL),
            (self.user, "GetForegroundWindow", [], w.HWND),
            (self.user, "GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
            (self.user, "GetDC", [w.HWND], w.HDC),
            (self.user, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
            (self.gdi, "CreateCompatibleDC", [w.HDC], w.HDC),
            (self.gdi, "CreateCompatibleBitmap", [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP),
            (self.gdi, "SelectObject", [w.HDC, w.HGDIOBJ], w.HGDIOBJ),
            (self.gdi, "BitBlt", [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD], w.BOOL),
            (self.gdi, "GetDIBits", [w.HDC, w.HBITMAP, w.UINT, w.UINT,
                                   ctypes.c_void_p, ctypes.c_void_p, w.UINT], ctypes.c_int),
            (self.gdi, "DeleteObject", [w.HGDIOBJ], w.BOOL),
            (self.gdi, "DeleteDC", [w.HDC], w.BOOL),
        ]
        for library, name, arguments, result in specifications:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        self.user.SetProcessDPIAware()

    def find(self, pid):
        found = []
        @self.callback_type
        def visit(hwnd, _data):
            owner = self.w.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and self.user.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(1024)
                self.user.GetWindowTextW(hwnd, title, len(title))
                if title.value.lower().startswith("zmcl"):
                    found.append((hwnd, title.value))
            return True
        require(self.user.EnumWindows(visit, 0), "EnumWindows failed")
        return found[0] if found else None

    def capture(self, hwnd, path):
        screen_width, screen_height = self.user.GetSystemMetrics(0), self.user.GetSystemMetrics(1)
        width, height = min(1120, screen_width - 40), min(760, screen_height - 80)
        require(width >= 640 and height >= 480, "Interactive desktop is too small for GUI acceptance")
        require(self.user.SetWindowPos(hwnd, None, 10, 10, width, height, 0x0040),
                "Cannot place the owned GUI window on the desktop")
        self.user.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        require(self.user.GetForegroundWindow() == hwnd,
                "Owned GUI window is not foreground; screenshot acceptance is unavailable")
        rect, point = self.w.RECT(), self.w.POINT(0, 0)
        require(self.user.GetClientRect(hwnd, ctypes.byref(rect))
                and self.user.ClientToScreen(hwnd, ctypes.byref(point)), "Cannot read owned GUI geometry")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        require(width >= 600 and height >= 400 and point.x >= 0 and point.y >= 0
                and point.x + width <= screen_width and point.y + height <= screen_height,
                "Owned GUI client area is outside the visible desktop")
        screen, memory, bitmap, previous = None, None, None, None
        try:
            screen = self.user.GetDC(None)
            require(screen, "No interactive screen DC is available")
            memory = self.gdi.CreateCompatibleDC(screen)
            bitmap = self.gdi.CreateCompatibleBitmap(screen, width, height)
            require(memory and bitmap, "Cannot allocate GUI capture resources")
            previous = self.gdi.SelectObject(memory, bitmap)
            require(previous and previous != ctypes.c_void_p(-1).value, "Cannot select screenshot bitmap")
            require(self.gdi.BitBlt(memory, 0, 0, width, height, screen, point.x, point.y,
                                    0x00CC0020 | 0x40000000), "GUI screen capture failed")
            self.gdi.SelectObject(memory, previous)
            previous = None
            # GetDIBits requires the bitmap to be deselected from any DC.
            # https://learn.microsoft.com/windows/win32/api/wingdi/nf-wingdi-getdibits
            header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0,
                                 width * height * 4, 0, 0, 0, 0)
            info = ctypes.create_string_buffer(header + b"\0" * 4)
            buffer = ctypes.create_string_buffer(width * height * 4)
            require(self.gdi.GetDIBits(memory, bitmap, 0, height, buffer, info, 0) == height,
                    "Cannot read complete GUI screenshot pixels")
            bgra = buffer.raw
            rgb = bytearray(width * height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
            colors = {bytes(rgb[index:index + 3]) for index in
                      range(0, len(rgb), 3 * max(1, width * height // 65536))}
            require(len(colors) >= 8, "GUI client screenshot is blank or lacks meaningful color variation")
            path.write_bytes(png_rgb(width, height, rgb))
            return {"width": width, "height": height, "sampled_colors": len(colors),
                    "screenshot": path.name, "sha256": sha256(path),
                    "scope": "Owned foreground Win32 client-area pixels; not exhaustive visual correctness."}
        finally:
            if previous and memory:
                self.gdi.SelectObject(memory, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            if screen:
                self.user.ReleaseDC(None, screen)


def gui_smoke(binary, data_root, evidence, env):
    windows = NativeWindow()
    with (evidence.output / "gui.stdout.txt").open("w", encoding="utf-8") as stdout, \
            (evidence.output / "gui.stderr.txt").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([str(binary), "gui", "--root", str(data_root), "--smoke-seconds", "15"],
                                   env=env, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 10
            found = None
            while time.monotonic() < deadline and process.poll() is None:
                found = windows.find(process.pid)
                if found:
                    break
                time.sleep(0.1)
            require(found is not None, "No visible owned zmcl window appeared before the deadline")
            hwnd, title = found
            image = windows.capture(hwnd, evidence.output / "windows-gui.png")
            result = process.wait(timeout=25)
            require(result == 0, f"Native GUI smoke exited with {result}")
            return {"title": title, "exit_code": result, "auto_closed": True, **image}
        finally:
            if process.poll() is None:
                # Scope cleanup to the process tree that this function launched.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, check=False)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def portable_roundtrip(package_output, package_report, evidence, env):
    version = package_report["version"]
    require(package_report["platform"] == "windows-amd64"
            and package_report["requested_targets"] == ["zip"]
            and package_report["skipped_targets"] == [], "Unexpected Windows package targets or platform")
    require(len(package_report["artifacts"]) == 1, "Expected exactly one Windows ZIP")
    asset = package_report["artifacts"][0]
    root_name = "zmcl-" + version + "-windows-amd64"
    require(asset["target"] == "zip" and asset["file"] == root_name + ".zip",
            "Windows asset filename must use windows-amd64")
    archive = package_output / "public" / asset["file"]
    require(sha256(archive) == asset["sha256"], "Published ZIP copy SHA-256 mismatch")
    with tempfile.TemporaryDirectory(prefix="portable acceptance with spaces-") as directory:
        temporary = Path(directory)
        extracted = evidence.check("zip_layout", lambda: str(extract_zip(
            archive, temporary / "unpacked" / root_name, root_name)))
        source = Path(extracted)
        marker = evidence.check("zip_identity", lambda: verify_extracted(
            source, version, package_report["binary"]["sha256"], evidence.report["source_commit"]))
        prefix = temporary / "private install prefix"
        installer = source / "scripts/install.py"
        evidence.check("portable_install", lambda: evidence.command(
            "install", [sys.executable, "-I", "-B", installer, "install", "--prefix", prefix],
            env=env))
        installed_binary = prefix / "bin/zmcl.exe"

        def installation_identity():
            require(installed_binary.is_file() and sha256(installed_binary) == package_report["binary"]["sha256"],
                    "Installed Windows executable identity mismatch")
            require(evidence.command("installed-version", [installed_binary, "--version"], env=env).strip()
                    == "zmcl " + version, "Installed Windows executable version mismatch")
            installed_marker = json.loads((prefix / "share/zmcl/distribution.json").read_text(encoding="utf-8"))
            receipt = json.loads((prefix / "share/zmcl/installation.json").read_text(encoding="utf-8"))
            require(installed_marker == marker, "Installer changed the distribution marker")
            require(type(receipt.get("schema")) is int and receipt["schema"] == 1
                    and receipt.get("application") == "zmcl" and receipt.get("channel") == "portable"
                    and receipt.get("version") == version and receipt.get("release_url") == marker["release_url"]
                    and Path(receipt["prefix"]).resolve() == prefix.resolve()
                    and Path(receipt["executable"]).resolve() == installed_binary.resolve(),
                    "Portable installation receipt identity mismatch")
            return {"version": version, "binary_sha256": sha256(installed_binary),
                    "managed_paths": verified_managed_paths(prefix)}

        installed = evidence.check("installation_identity", installation_identity)
        sentinel = b"SYNTHETIC GUI DISTRIBUTION ACCEPTANCE DATA; NOT A REAL SAVE\n"
        data_paths = [prefix / "share/zmcl/instances/fixture/game/saves/world/level.dat",
                      temporary / "synthetic external game data/instances/fixture/game/saves/world/level.dat"]
        for path in data_paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(sentinel)
        gui_error = None
        try:
            evidence.check("native_gui", lambda: gui_smoke(
                installed_binary, temporary / "isolated gui data", evidence, env))
        except Exception as error:
            gui_error = error
        evidence.check("portable_uninstall", lambda: evidence.command(
            "uninstall", [sys.executable, "-I", "-B", installer, "uninstall", "--prefix", prefix],
            env=env))

        def preserved():
            require(all(not (prefix / name).exists() for name in installed["managed_paths"])
                    and not (prefix / "share/zmcl/install.json").exists(),
                    "Uninstall left an unchanged managed file")
            require(all(path.read_bytes() == sentinel for path in data_paths),
                    "Uninstall changed synthetic game data")
            return {"managed_files_removed": True, "synthetic_game_data_preserved": True,
                    "inside_prefix": True, "outside_prefix": True}
        evidence.check("uninstall_preservation", preserved)
        if gui_error:
            raise gui_error


def native_run(args):
    valid_commit(args.source_commit)
    require(os.name == "nt" and platform.machine().lower() == "amd64",
            "This distribution workflow requires a native Windows AMD64 host")
    require(not args.output.exists(), "Acceptance output must be a new directory")
    evidence = Evidence(args.output.resolve(), args.source_commit)
    try:
        def source_check():
            head = evidence.command("source-head", ["git", "rev-parse", "HEAD"]).strip()
            require(head == args.source_commit, "Checkout HEAD differs from requested source_commit")
            status = evidence.command("submodules", ["git", "submodule", "status", "--recursive"])
            require(status.strip() and all(line.startswith(" ") for line in status.splitlines()),
                    "A pinned submodule is uninitialized or differs from its gitlink")
            return {"head": head, "submodule_status": status.splitlines()}
        evidence.check("exact_source", source_check)
        selected = toolchain()
        evidence.report["toolchain"] = selected
        evidence.save()
        # Reuse the proven MSVC environment setup from the exact vendored zgpui.
        # No test/build entry point from that module is invoked.
        native = load_module("zgpui_windows_environment", ROOT / "vendor/zgpui/scripts/check_native.py")
        env = native.environment()
        env["CARGO_BUILD_JOBS"] = "1"
        env["CARGO_TARGET_DIR"] = str(ROOT / "target/windows-distribution")
        env["RUSTFLAGS"] = "-C target-feature=+crt-static"
        evidence.report["rustflags"] = env["RUSTFLAGS"]
        dumpbin = evidence.check("msvc_dumpbin", lambda: str(resolve_dumpbin(env)))
        evidence.check("rust_version", lambda: evidence.command(
            "rust-version", ["rustc", "+" + selected["channel"], "--version", "--verbose"], env=env))
        evidence.check("native_build", lambda: evidence.command(
            "native-build", ["cargo", "+" + selected["channel"], "build", "--locked", "--release",
                             "--target", TARGET, "--features", "gui", "--package", "zmcl"],
            env=env, timeout=1200))
        binary = Path(env["CARGO_TARGET_DIR"]) / TARGET / "release/zmcl.exe"
        version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
        expected_sha = sha256(binary)
        evidence.check("pe_runtime_imports", lambda: pe_dependencies(evidence.command(
            "pe-imports", [dumpbin, "/dependents", binary], env=env)))
        evidence.check("native_version", lambda: require(
            evidence.command("native-version", [binary, "--version"], env=env).strip() == "zmcl " + version,
            "Native built executable version mismatch"))
        evidence.report["binary"] = {"path": binary.relative_to(ROOT).as_posix(),
                                     "sha256": expected_sha, "bytes": binary.stat().st_size,
                                     "version": version}
        evidence.save()
        packager = load_module("zmcl_pinned_zdistro", ROOT / "scripts/zdistro_package.py")
        tools_dir = evidence.output / "tools"
        tools_dir.mkdir()
        tool = tools_dir / "zdistro-0.1.0.pyz"

        def download():
            with urllib.request.urlopen(packager.ZDISTRO_URL, timeout=30) as response:
                body = response.read(1024 * 1024 + 1)
            require(len(body) <= 1024 * 1024
                    and hashlib.sha256(body).hexdigest() == packager.ZDISTRO_SHA256,
                    "Downloaded zdistro size or pinned SHA-256 mismatch")
            tool.write_bytes(body)
            packager.validate_tool(tool)
            return {"url": packager.ZDISTRO_URL, "sha256": sha256(tool), "bytes": len(body),
                    "version": packager.ZDISTRO_VERSION, "commit": packager.ZDISTRO_COMMIT}
        evidence.check("pinned_packager", download)
        package_output = evidence.output / "packaging"
        evidence.check("zdistro_zip", lambda: evidence.command(
            "zdistro-package", [sys.executable, "-I", "-B", ROOT / "scripts/zdistro_package.py",
                                "--source-root", ROOT, "--binary", binary,
                                "--source-commit", args.source_commit, "--expected-sha256", expected_sha,
                                "--zdistro", tool, "--target", "zip", "--output", package_output],
            env=env, timeout=600))
        package_report = json.loads((package_output / "release-result.json").read_text(encoding="utf-8"))
        originals = {path: sha256(path) for path in (package_output / "evidence").rglob("receipt.json")}
        require(originals, "No original zdistro receipt was preserved")
        portable_roundtrip(package_output, package_report, evidence, env)
        require(all(sha256(path) == value for path, value in originals.items()),
                "Portable acceptance modified an original zdistro receipt")
        evidence.report["original_zdistro_receipts_unchanged"] = True
        evidence.report["passed"] = True
        evidence.save()
        print(json.dumps(evidence.report))
        return 0
    except Exception as error:
        evidence.report["passed"] = False
        evidence.report["error"] = str(error)
        evidence.report["boundary"] = "A failed or unavailable Windows/desktop gate remains failed; "
        evidence.report["boundary"] += "partial packaging artifacts do not establish complete Windows acceptance."
        evidence.save()
        print("Windows distribution acceptance failed: " + str(error), file=sys.stderr)
        return 1


def main(argv=None):
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("toolchain")
    validate = sub.add_parser("validate")
    validate.add_argument("--source-commit", required=True)
    validate.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "toolchain":
            selected = toolchain()
            output = os.environ.get("GITHUB_OUTPUT")
            require(output, "GITHUB_OUTPUT is required for the workflow toolchain step")
            with Path(output).open("a", encoding="utf-8") as stream:
                stream.write("channel=" + selected["channel"] + "\n")
                stream.write("components=" + selected["components"] + "\n")
            print(json.dumps(selected))
            return 0
        return native_run(args)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Windows distribution: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
