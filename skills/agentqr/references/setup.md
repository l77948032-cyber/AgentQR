# Installation and diagnosis

Install this complete `skills/agentqr` folder using the host's supported skill
installer. Then perform runtime setup below as part of the same installation
task. Copying `SKILL.md` alone is not a completed installation. A host without
skill discovery can invoke the script, but is not a configured skill host.

## Runtime setup

Requires macOS 13+ or supported 64-bit Windows, and Python 3.10+. Release
artifacts cover x64 and ARM64. Python uses only its standard library; users do
not need Swift, Xcode, the .NET SDK, pip packages, or a separately installed
.NET runtime. If Python is missing, use the host's supported Python environment
or ask before installing it from its official distribution. Never execute a
command that opens the Windows Store and report Python as installed.

Resolve `<skill>` to the absolute installed folder. Run with the available
Python interpreter (`python3` on macOS, often `python` or `py -3` on Windows):

```sh
python3 <skill>/scripts/agentqr.py install
python3 <skill>/scripts/agentqr.py doctor
python3 <skill>/scripts/agentqr.py self-test
```

On Windows, pass the interpreter and script path as separate process arguments;
do not copy the literal `python3` command if that interpreter is unavailable.
Installation detects the OS and process architecture, downloads the fixed
version's ZIP and manifest from this project's GitHub Release over HTTPS,
checks SHA-256, rejects unsafe ZIP paths, and decodes a bundled synthetic QR
before activating the decoder. It never reads accounts or real invoices.
The checksum detects mismatched downloads; it is not a code-signing certificate.

The runtime is kept in the current user's application-data directory:

- macOS: `~/Library/Application Support/AgentQR/<version>/<target>/`
- Windows: `%LOCALAPPDATA%/AgentQR/<version>/<target>/`
- `AGENTQR_HOME` overrides the cache root for isolated tests or managed installs.

No administrator access, shell profile edit, PATH edit, login, or mailbox
connection is required. Do not disable Gatekeeper, SmartScreen, or TLS checks
if local policy blocks the runtime; report the actual installation failure.
These CLI artifacts are not notarized desktop application installers.

## Stable invocation

```sh
python3 <skill>/scripts/agentqr.py scan <local-image>
python3 <skill>/scripts/agentqr.py scan <local-image> --reveal
```

Normal decoding is offline. Missing runtimes trigger installation on first
scan. Allow a five-minute timeout for setup; ordinary decoding has a 45-second
timeout. Default output omits the full payload. Only use `--reveal` when the
authorized next step needs it. Success is a JSON array. Failures are JSON
objects with `error`; check the exit status before interpreting results.

`doctor` reports decoder readiness without touching browser/app/mail accounts.
`self-test` verifies actual QR recognition and default output redaction. Report
skill discovery and runtime readiness separately, and follow the host's own
reload procedure if it cannot discover a new skill immediately.

## Failure and recovery

- `DOWNLOAD_FAILED`: check GitHub connectivity and release availability. Do not
  ask for application passwords or upload the user's QR to another service.
- `CHECKSUM_MISMATCH` / `INVALID_ARCHIVE`: stop; no decoder is activated. Do not
  bypass integrity checking or run partially downloaded code.
- `INSTALL_BUSY`: another installer may hold the cache's `.install-lock`.
  Inspect its recorded PID before removing a stale lock; do not interrupt a
  live installation or automatically delete user directories.
- `INSTALL_DAMAGED`: move only the named version/target cache aside after
  confirming no task uses it, then reinstall. Keep user source files untouched.
- `SELF_TEST_FAILED` / `DECODER_START_FAILED`: report OS/architecture and error
  code, without attaching private images. Source builds are a developer fallback,
  not a successful release installation.

For a trusted offline bundle, use `install --bundle-dir <directory>` containing
the release `manifest.json` and matching ZIP. The same checks and self-test run.

Installing the decoder does not connect QQ Mail or sign into WeChat/WeCom.
Use the workflows in `SKILL.md` when a real task needs those existing host tools.
