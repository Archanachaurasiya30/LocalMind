"""Exercise the real NSIS preinstall hook without opening a UI or installing the app."""

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def find_makensis():
    configured = os.environ.get("LOCALMIND_MAKENSIS") or shutil.which("makensis")
    if configured:
        return Path(configured)
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        candidate = Path(os.environ.get(variable, "")) / "NSIS" / "makensis.exe"
        if candidate.is_file():
            return candidate
    # The Tauri build downloads its portable toolset to the user cache.
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for directory in (local / "tauri", local / "cache" / "tauri"):
        if directory.is_dir():
            matches = sorted(directory.rglob("makensis.exe"), key=lambda path: (len(path.parts), str(path)))
            if matches:
                return matches[0]
    return None


MAKENSIS = find_makensis() if sys.platform == "win32" else None
if os.environ.get("LOCALMIND_TEST_NSIS") == "1" and not MAKENSIS:
    raise RuntimeError("NSIS upgrade tests are required but makensis was not found")


@unittest.skipUnless(sys.platform == "win32" and MAKENSIS, "requires Windows and makensis")
class WindowsUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="localmind-upgrade-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "profile O'Brien ü"
        self.install.mkdir()
        self.executable = self.root / "preflight.exe"
        script = self.root / "preflight.nsi"
        script.write_text(f'''Unicode true
RequestExecutionLevel user
SilentInstall silent
OutFile "{self.executable}"
!include "{ROOT / 'apps/desktop/src-tauri/installer-hooks.nsh'}"
Section
  StrCpy $INSTDIR "{self.install}"
  !insertmacro NSIS_HOOK_PREINSTALL
  FileOpen $2 "$INSTDIR\\passed" w
  FileWrite $2 "ready"
  FileClose $2
SectionEnd
''', encoding="utf-8-sig")
        subprocess.run([str(MAKENSIS), "/V2", str(script)], check=True, capture_output=True, timeout=30)

    def run_guard(self):
        return subprocess.run([str(self.executable), "/S"], capture_output=True, timeout=15).returncode

    def test_fresh_install_proceeds(self):
        self.assertEqual(self.run_guard(), 0)
        self.assertTrue((self.install / "passed").is_file())

    def test_unlocked_files_are_not_modified(self):
        sidecar = self.install / "localmind-sidecar.exe"
        sidecar.write_bytes(b"old service")
        self.assertEqual(self.run_guard(), 0)
        self.assertEqual(sidecar.read_bytes(), b"old service")

    def test_each_locked_runtime_stops_before_payload_then_retry_succeeds(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        for relative in ("localmind-app.exe", "localmind-sidecar.exe", "ollama-runtime/ollama.exe"):
            with self.subTest(file=relative):
                marker = self.install / "passed"
                marker.unlink(missing_ok=True)
                path = self.install / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"old runtime")
                handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
                self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
                try:
                    self.assertEqual(self.run_guard(), 2)
                    self.assertFalse(marker.exists())
                finally:
                    kernel.CloseHandle(handle)
                self.assertEqual(path.read_bytes(), b"old runtime")
                self.assertEqual(self.run_guard(), 0)
                self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
