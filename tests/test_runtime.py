import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/agentqr/scripts"))
import agentqr
import runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        env = patch.dict(os.environ, {"AGENTQR_HOME": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        target = patch.object(runtime, "target", return_value="macos-arm64")
        self.real_target = runtime.target
        target.start()
        self.addCleanup(target.stop)

    def bundle(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        archive = bundle / f"agentqr-{runtime.VERSION}-macos-arm64.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr(runtime.executable(Path(".")).name, b"test binary")
        manifest = {"version": runtime.VERSION, "assets": {"macos-arm64": {
            "file": archive.name, "sha256": runtime.sha256(archive)}}}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        return bundle, archive, manifest

    def test_platforms(self):
        for system, arch, expected in [
            ("Darwin", "arm64", "macos-arm64"), ("Darwin", "x86_64", "macos-x64"),
            ("Windows", "AMD64", "windows-x64"), ("Windows", "ARM64", "windows-arm64"),
        ]:
            self.assertEqual(expected, self.real_target(system, arch))
        with self.assertRaises(runtime.Failure):
            self.real_target("Linux", "x86_64")

    def test_invalid_manifest(self):
        for manifest in [{}, [], None, {"version": runtime.VERSION, "assets": {"macos-arm64": {
            "file": "../bad.zip", "sha256": "a" * 64}}}]:
            with self.subTest(manifest=manifest), self.assertRaises(runtime.Failure):
                runtime.release_entry(manifest, "macos-arm64")

    def test_unsafe_archives(self):
        names = ["../outside", "/absolute", "C:/drive", "folder\\file", "file:stream",
                 "CON.txt", "file.", "file ", "./agentqr", "a//b", "a?b", "."]
        for name in names:
            archive = self.root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(name, "untrusted")
            with self.subTest(name=name), self.assertRaises(runtime.Failure):
                runtime.unpack(archive, self.root / "unpacked")
        self.assertFalse((self.root / "unpacked").exists())

    def test_duplicate_and_symlink(self):
        for link in (True, False):
            archive = self.root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                if link:
                    entry = zipfile.ZipInfo("link")
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    package.writestr(entry, "../outside")
                else:
                    package.writestr("agentqr", "one")
                    package.writestr("AgentQR", "two")
            with self.assertRaises(runtime.Failure):
                runtime.unpack(archive, self.root / "unpacked")

    def test_extracted_size_limit(self):
        _, archive, _ = self.bundle()
        with patch.object(runtime, "MAX_EXTRACTED", 1), self.assertRaises(runtime.Failure):
            runtime.unpack(archive, self.root / "unpacked")

    def test_checked_install_and_idempotency(self):
        bundle, _, _ = self.bundle()
        with patch.object(runtime, "self_test", return_value={"status": "ready"}) as check:
            runtime.install(bundle)
            self.assertIsNotNone(runtime.installed())
            runtime.install(bundle)
            self.assertEqual(2, check.call_count)
        self.assertFalse((runtime.cache_root() / ".install-lock").exists())
        runtime.installed().write_bytes(b"changed")
        self.assertIsNone(runtime.installed())
        with self.assertRaisesRegex(runtime.Failure, "integrity"):
            runtime.install(bundle)

    def test_checksum_failure_never_runs(self):
        bundle, archive, _ = self.bundle()
        archive.write_bytes(b"corrupted")
        with patch.object(runtime, "self_test") as check, self.assertRaisesRegex(runtime.Failure, "checksum"):
            runtime.install(bundle)
        check.assert_not_called()
        self.assertFalse(runtime.ready_directory().exists())
        self.assertFalse((runtime.cache_root() / ".install-lock").exists())

    def test_self_test_failure_does_not_activate(self):
        bundle, _, _ = self.bundle()
        with patch.object(runtime, "self_test", side_effect=runtime.Failure("SELF_TEST_FAILED", "failed")):
            with self.assertRaises(runtime.Failure):
                runtime.install(bundle)
        self.assertFalse(runtime.ready_directory().exists())
        self.assertEqual([], list(runtime.cache_root().glob(".staging-*")))

    def test_lock_preserved(self):
        lock = runtime.cache_root() / ".install-lock"
        lock.mkdir(parents=True)
        with self.assertRaisesRegex(runtime.Failure, "running"):
            runtime.install()
        self.assertTrue(lock.exists())

    def test_no_payload_leak_on_error(self):
        response = subprocess.CompletedProcess([], 1, "private token and path", "private error")
        output = io.StringIO()
        with patch.object(runtime, "installed", return_value=Path("agentqr")), \
                patch.object(runtime, "invoke", return_value=response), contextlib.redirect_stdout(output):
            self.assertEqual(1, agentqr.main(["scan", "private filename.png"]))
        self.assertEqual({"error": "SCAN_OR_OPEN_FAILED"}, json.loads(output.getvalue()))

    def test_scan_installs_on_first_use(self):
        response = subprocess.CompletedProcess([], 0, '[{"index":0,"kind":"text"}]', "")
        with patch.object(runtime, "installed", side_effect=[None, Path("agentqr")]), \
                patch.object(runtime, "install") as install, \
                patch.object(runtime, "invoke", return_value=response) as invoke, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, agentqr.main(["scan", "space in name.png"]))
        install.assert_called_once_with()
        invoke.assert_called_once_with(Path("agentqr"), ["scan", "space in name.png"])

    def test_synthetic_test_rejects_leaked_payload(self):
        response = subprocess.CompletedProcess([], 0, json.dumps([{
            "kind": "web", "destination": "https://example.com/[redacted]", "payload": runtime.PAYLOAD}]), "")
        with patch.object(runtime, "invoke", return_value=response), self.assertRaises(runtime.Failure):
            runtime.self_test(Path("agentqr"))


if __name__ == "__main__":
    unittest.main()
