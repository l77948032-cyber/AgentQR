# Local decoder setup

Use this reference when installing AgentQR or diagnosing a missing decoder.
The skill and compiled decoder are separate components. Never report full
readiness solely because `SKILL.md` was copied successfully.

## Get the source

The source repository is https://github.com/l77948032-cyber/AgentQR.git.
Use the host's supported installer to install `skills/agentqr` as a skill. Keep
this reference with it. A host without skill support can read these instructions
but cannot be described as having installed a discoverable skill.

For the decoder, reuse an existing checkout only after checking its origin and
working tree. Otherwise clone the repository into a new, user-owned application
data directory outside the skill directory. Choose an unused path; never replace
an existing project. Record the source commit used for the build. Do not modify
global Git configuration, request administrator access, or upload local files.

```sh
git clone https://github.com/l77948032-cyber/AgentQR.git <new-source-directory>
```

Run each build command separately, checking its exit status. Stop and report a
failed command. The paths below are placeholders to resolve to actual local
paths, not literal arguments to execute. If a toolchain is missing, ask before
installing it and use the vendor's official distribution.

## macOS

Requires macOS 13 or later and a Swift 6-compatible toolchain. Check `sw_vers`
and `swift --version`; Xcode Command Line Tools must supply the required SDK.

Run from the source checkout:

```sh
swift test
swift build -c release
swift build -c release --show-bin-path
```

The test suite generates synthetic QR images locally and checks real decoding,
blank/invalid images, and redaction. Use the `agentqr` executable inside the
reported release directory; verify that its `--help` command exits successfully.
No real invoice or login QR is needed for installation testing.

## Windows

Requires Windows and the .NET 10 SDK to build; check `dotnet --list-sdks` and
select the installed architecture. Use `win-x64` for an x64 process environment,
or `win-arm64` for native ARM64. Do not select a Linux/macOS runtime for this
backend. Keep the complete self-contained publish directory, not just the EXE.

Run from the source checkout (example for x64):

```powershell
dotnet restore windows/AgentQR.Tests --locked-mode
dotnet test windows/AgentQR.Tests -c Release --no-restore
dotnet publish windows/AgentQR -c Release -r win-x64 --self-contained true -o artifacts/windows-x64
.\artifacts\windows-x64\agentqr.exe --help
```

For ARM64, replace the runtime and output directory consistently. The tests
generate synthetic QR images and exercise the decoder and command contract.
Run these tests on Windows: a cross-build elsewhere is not runtime validation.
Dependency restore may contact NuGet; ordinary QR decoding does not require a
network connection. Missing connectivity or failed restore is a setup failure,
not a QR recognition failure.

## Make the decoder available to the agent

Record the absolute executable path in the host's local tool configuration or
task context. Invoke that path with a process argument array. If a persistent
configuration mechanism is not available, report the path and that limitation;
do not claim later sessions can discover it automatically. Do not rewrite the
shared skill with a user's private path or silently edit shell startup files.

Verify `--help` and the platform's synthetic QR tests before reporting decoder
readiness. Distinguish skill installation, decoder build, and runtime testing.
Installing this skill does not connect QQ Mail, sign into WeChat/WeCom, or grant
browser/screen permissions. Request those separately only when a task needs them.
