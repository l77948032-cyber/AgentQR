"""Install and invoke the versioned native AgentQR decoder. Standard library only."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import ssl
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VERSION = "0.2.0"
REPOSITORY = "l77948032-cyber/AgentQR"
PAYLOAD = "https://example.com/agentqr-self-test"
SKILL = Path(__file__).resolve().parent.parent
MAX_DOWNLOAD = 150_000_000
MAX_EXTRACTED = 600_000_000


class Failure(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def target(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system not in ("Darwin", "Windows") or arch is None:
        raise Failure("UNSUPPORTED_PLATFORM", "AgentQR supports macOS and 64-bit Windows on x64/ARM64.")
    return ("macos" if system == "Darwin" else "windows") + "-" + arch


def cache_root():
    override = os.environ.get("AGENTQR_HOME")
    if override:
        root = Path(override).expanduser().absolute()
    elif platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "AgentQR"
    else:
        root = Path.home() / "Library/Application Support/AgentQR"
    return root


def executable(directory):
    return Path(directory) / ("agentqr.exe" if platform.system() == "Windows" else "agentqr")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ready_directory(root=None):
    return (root or cache_root()) / VERSION / target()


def installed(root=None):
    directory = ready_directory(root)
    try:
        state = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        program = executable(directory)
        if (state["version"] == VERSION and state["target"] == target()
                and program.is_file() and not program.is_symlink()
                and sha256(program) == state["executable_sha256"]):
            return program
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def https_download(url, destination, limit, accept="application/octet-stream"):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AgentQR/" + VERSION, "Accept": accept})
        with urllib.request.urlopen(request, timeout=20) as response, Path(destination).open("wb") as output:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > limit:
                    raise Failure("DOWNLOAD_TOO_LARGE", "Release file exceeds the size limit.")
                output.write(block)
    except (urllib.error.URLError, OSError) as error:
        # Some Python distributions omit the system trust store. Use the OS curl
        # trust store without weakening TLS verification or modifying certificates.
        if not isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError) or not shutil.which("curl"):
            raise Failure("DOWNLOAD_FAILED", "Cannot download this release. Check network access and repository visibility.") from None
        result = subprocess.run([
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--http1.1", "--connect-timeout", "10", "--header", "Accept: " + accept,
            "--proto", "=https", "--proto-redir", "=https", "--max-time", "180",
            "--max-filesize", str(limit), "--output", str(destination), url,
        ], capture_output=True, timeout=190)
        if result.returncode != 0:
            raise Failure("DOWNLOAD_FAILED", "System HTTPS download failed; certificate checks were not disabled.")
    if Path(destination).stat().st_size > limit:
        raise Failure("DOWNLOAD_TOO_LARGE", "Release file exceeds the size limit.")


def download(url, destination, limit=MAX_DOWNLOAD):
    # Both transports are anonymous HTTPS to this repository's public release.
    base = f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/"
    if not url.startswith(base) or "/" in url[len(base):]:
        raise Failure("INVALID_DOWNLOAD_URL", "Unexpected release download location.")
    try:
        https_download(url, destination, limit)
        return
    except Failure as error:
        if error.code != "DOWNLOAD_FAILED":
            raise
    api = f"https://api.github.com/repos/{REPOSITORY}/releases/"
    with tempfile.TemporaryDirectory(prefix="agentqr-release-") as temporary:
        metadata = Path(temporary) / "release.json"
        https_download(api + "tags/v" + VERSION, metadata, 1_000_000, "application/vnd.github+json")
        try:
            release = json.loads(metadata.read_text(encoding="utf-8"))
            if release["tag_name"] != "v" + VERSION or release["draft"]:
                raise ValueError()
            asset = next(item for item in release["assets"] if item["name"] == url[len(base):])
            asset_url = asset["url"]
            prefix = api + "assets/"
            if (not asset_url.startswith(prefix) or not asset_url[len(prefix):].isdigit()
                    or asset["browser_download_url"] != url or asset["size"] > limit):
                raise ValueError()
        except (ValueError, TypeError, KeyError, StopIteration):
            raise Failure("DOWNLOAD_FAILED", "The public release API did not return the expected asset.") from None
        https_download(asset_url, destination, limit)


def release_entry(manifest, platform_target):
    try:
        entry = manifest["assets"][platform_target]
        expected = f"agentqr-{VERSION}-{platform_target}.zip"
        checksum = entry["sha256"]
        if (manifest["version"] != VERSION or entry["file"] != expected
                or not isinstance(checksum, str) or len(checksum) != 64
                or any(c not in "0123456789abcdef" for c in checksum)):
            raise ValueError()
        return entry
    except (KeyError, TypeError, ValueError):
        raise Failure("INVALID_MANIFEST", "Release metadata does not match the requested version and platform.") from None


def unpack(archive, destination):
    seen = set()
    total = 0
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 10000:
            raise Failure("INVALID_ARCHIVE", "Too many archive entries.")
        for entry in entries:
            # ZipInfo normalizes separators on Windows; inspect the original
            # archive name so platform-specific cleanup cannot hide unsafe input.
            name = entry.orig_filename
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            # Also reject Windows device/ADS paths and case-insensitive collisions.
            key = name.rstrip("/").casefold()
            if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name
                    or ":" in name or key in seen or stat.S_ISLNK(mode)
                    or any(part.split(".")[0].casefold() in devices for part in path.parts)
                    or any(character in name for character in '<>"|?*')
                    or any(ord(character) < 32 for character in name)
                    or any(part in ("", ".") for part in name.rstrip("/").split("/"))
                    or any(part.endswith((".", " ")) for part in path.parts)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))):
                raise Failure("INVALID_ARCHIVE", "Unsafe archive entry.")
            seen.add(key)
            total += entry.file_size
            if total > MAX_EXTRACTED:
                raise Failure("INVALID_ARCHIVE", "Archive exceeds the extracted size limit.")
        try:
            package.extractall(destination)
        except (zipfile.BadZipFile, RuntimeError):
            raise Failure("INVALID_ARCHIVE", "Release archive could not be extracted.") from None


def invoke(program, args, timeout=45):
    try:
        result = subprocess.run([str(program), *args], capture_output=True, timeout=timeout,
                                encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise Failure("DECODER_TIMEOUT", "QR decoding exceeded the time limit.") from None
    except OSError:
        raise Failure("DECODER_START_FAILED", "The native decoder could not start on this system.") from None
    if len(result.stdout) > 4_000_000:
        raise Failure("INVALID_DECODER_OUTPUT", "Decoder output exceeds the limit.")
    return result


def self_test(program):
    with tempfile.TemporaryDirectory(prefix="agentqr-check-") as temporary:
        fixture = Path(temporary) / "synthetic qr.png"
        fixture.write_bytes(base64.b64decode((SKILL / "assets/self-test.png.base64").read_text(), validate=False))
        hidden = invoke(program, ["scan", str(fixture)])
        revealed = invoke(program, ["scan", str(fixture), "--reveal"])
        try:
            safe = json.loads(hidden.stdout)
            full = json.loads(revealed.stdout)
            valid = (hidden.returncode == revealed.returncode == 0
                     and len(safe) == len(full) == 1
                     and safe[0]["kind"] == "web" and "payload" not in safe[0]
                     and safe[0]["destination"] == "https://example.com/[redacted]"
                     and full[0]["payload"] == PAYLOAD and PAYLOAD not in hidden.stdout)
        except (ValueError, KeyError, TypeError, IndexError):
            valid = False
        if not valid:
            raise Failure("SELF_TEST_FAILED", "Synthetic QR decoding or output redaction failed.")
    return {"status": "ready", "version": VERSION, "target": target(), "self_test": "passed"}


def install(bundle_dir=None):
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    program = installed(root)
    if program:
        return self_test(program)
    lock = root / ".install-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise Failure("INSTALL_BUSY", "Another installation may be running. Inspect the install lock before retrying.") from None
    try:
        (lock / "owner.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        final = ready_directory(root)
        if final.exists():
            raise Failure("INSTALL_DAMAGED", "An existing installation failed integrity checks. Move it aside before reinstalling.")
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temporary:
            staging = Path(temporary)
            manifest_path = staging / "manifest.json"
            base = f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/"
            if bundle_dir:
                shutil.copyfile(Path(bundle_dir) / "manifest.json", manifest_path)
            else:
                download(base + "manifest.json", manifest_path, limit=100_000)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = release_entry(manifest, target())
            archive = staging / entry["file"]
            if bundle_dir:
                source = Path(bundle_dir) / entry["file"]
                if source.stat().st_size > MAX_DOWNLOAD:
                    raise Failure("DOWNLOAD_TOO_LARGE", "Local bundle exceeds the size limit.")
                shutil.copyfile(source, archive)
            else:
                download(base + entry["file"], archive)
            if sha256(archive) != entry["sha256"]:
                raise Failure("CHECKSUM_MISMATCH", "Release checksum mismatch; nothing was installed.")
            payload = staging / "runtime"
            payload.mkdir(mode=0o700)
            try:
                unpack(archive, payload)
            except zipfile.BadZipFile:
                raise Failure("INVALID_ARCHIVE", "Release archive is not a readable ZIP.") from None
            program = executable(payload)
            if not program.is_file() or program.is_symlink():
                raise Failure("INVALID_ARCHIVE", "Native decoder is missing.")
            if platform.system() != "Windows":
                program.chmod(0o700)
            report = self_test(program)
            (payload / "ready.json").write_text(json.dumps({
                "version": VERSION, "target": target(), "archive_sha256": entry["sha256"],
                "executable_sha256": sha256(program),
            }), encoding="utf-8")
            final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.rename(payload, final)
            return report
    finally:
        shutil.rmtree(lock)


def doctor():
    program = installed()
    return {"status": "ready" if program else "not_installed", "version": VERSION,
            "target": target(), "decoder": str(program) if program else None,
            "login": "host_agent_tools", "mail": "separate_authorized_connector"}
