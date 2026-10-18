"""Package native outputs and assemble a checksum manifest; no account access."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/agentqr/scripts"))
from runtime import VERSION

TARGETS = {"macos-arm64", "macos-x64", "windows-arm64", "windows-x64"}


def package(source, target, output):
    output.mkdir(parents=True, exist_ok=True)
    files = {"LICENSE": ROOT / "LICENSE", "THIRD_PARTY_NOTICES.md": ROOT / "THIRD_PARTY_NOTICES.md"}
    if target.startswith("macos"):
        files["agentqr"] = source / "agentqr"
    else:
        files.update({str(path.relative_to(source)).replace("\\", "/"): path
                      for path in source.rglob("*") if path.is_file() and path.suffix != ".pdb"})
        if "agentqr.exe" not in files:
            raise ValueError("Missing self-contained Windows executable")
        files.update({"licenses/" + path.name: path for path in (ROOT / "licenses").iterdir() if path.is_file()})
        # Preserve the exact runtime pack's redistributable notices.
        deps = json.loads((source / "agentqr.deps.json").read_text())
        runtime_key = next(key for key in deps["libraries"] if "microsoft.netcore.app.runtime." in key.lower())
        name, version = runtime_key.removeprefix("runtimepack.").split("/")
        pack = Path.home() / ".nuget/packages" / name.lower() / version
        notices = [path for path in pack.iterdir() if path.is_file() and
                   ("license" in path.name.lower() or "thirdpartynotices" in path.name.lower().replace("-", ""))]
        if len(notices) < 2:
            raise ValueError("Missing .NET runtime license or third-party notices")
        files.update({"licenses/dotnet-" + path.name: path for path in notices})
    archive = output / f"agentqr-{VERSION}-{target}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, path in sorted(files.items()):
            if path.is_symlink():
                raise ValueError("Symlinks are not supported in release bundles")
            bundle.write(path, name)
    return archive


def manifest(output, require_all=False):
    assets = {}
    for target in sorted(TARGETS):
        archive = output / f"agentqr-{VERSION}-{target}.zip"
        if archive.is_file():
            assets[target] = {"file": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    if not assets or (require_all and assets.keys() != TARGETS):
        raise ValueError("Release is missing platform bundles")
    (output / "manifest.json").write_text(json.dumps({"version": VERSION, "assets": assets}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", choices=sorted(TARGETS))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    if args.source and args.target:
        package(args.source, args.target, args.output)
    manifest(args.output, args.require_all)
