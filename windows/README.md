# AgentQR for Windows

A local, noninteractive QR decoder for agent integrations. This is the Windows
backend, not a complete MCP server or agent-plugin package. It uses C#/.NET 10
and ZXing.Net, independently of the macOS Swift/Vision backend.

## Run

Build on Windows with the .NET 10 SDK:

```powershell
dotnet run --project windows/AgentQR -- scan "C:\Invoices\qr.png"
dotnet run --project windows/AgentQR -- scan "C:\Invoices\qr.png" --reveal
dotnet test windows/AgentQR.Tests -c Release
```

Publish a self-contained x64 Windows application (the destination computer does
not need to install .NET). Keep the entire output directory together:

```powershell
dotnet publish windows/AgentQR -c Release -r win-x64 --self-contained true -o artifacts/windows-x64
.\artifacts\windows-x64\agentqr.exe scan "C:\Invoices\qr.png"
```

The Windows CI workflow tests generated QR images and the published executable,
then uploads the self-contained application as an artifact. It uses synthetic
fixtures only, never real invoices or mail credentials.

## Agent contract

Invoke the executable with a process argument array, not a shell-built command.
Pass one local image path. The command does not read stdin or ask for confirmation.
Success emits the same JSON result shape as the macOS `scan` command:

```json
[
  {
    "index": 1,
    "kind": "web",
    "destination": "https://example.com/[redacted]"
  }
]
```

- `kind`: `web`, `text`, or `unsupported-scheme`.
- `index`: 1-based, top-to-bottom then left-to-right within this backend. Duplicate
  payloads are removed. Do not rely on identical ordering across decoder engines.
- `destination`: redacted origin for HTTP(S) URLs, otherwise `[redacted]`.
  Windows normalizes hostname casing, IDNs and default ports using .NET URI rules.
- `--reveal`: adds the exact decoded `payload`. Only use when the agent needs it;
  do not persist it in public logs, telemetry, source control, or search queries.

Errors emit a JSON object on stdout, for example `{"error":"NO_QR_CODE"}`.
The caller must check the exit code before reading success results.

| Exit | Error | Meaning |
| --- | --- | --- |
| 0 | None | One or more QR codes decoded, or help displayed |
| 1 | SCAN_OR_OPEN_FAILED | Missing, unreadable, invalid, oversized or rejected image |
| 2 | INVALID_ARGUMENTS | Unsupported command or arguments |
| 3 | NO_QR_CODE | Image decoded but no QR payload found |
| 4 | UNSUPPORTED_PLATFORM | This backend requires Windows |

Exit codes 1-3 and the successful result fields follow the macOS prototype.
Unlike that prototype, Windows always emits scan errors as JSON on stdout.
The historical `SCAN_OR_OPEN_FAILED` name is retained for compatibility; this
Windows backend never opens URLs.

## Scope and security

- Reads local PNG, JPEG, BMP and other images supported by Windows' image decoder.
  Only the selected/default frame is scanned, not every frame of animated images.
- Accepts files up to 25,000,000 bytes and images up to 40,000,000 pixels. These
  are input guards, not an OS-level memory sandbox. Agents should also apply a
  process timeout and resource limits to untrusted files.
- Rejects UNC/device paths, network drives and alternate data streams. Use files
  in a trusted local staging directory; filesystem links are not a security sandbox.
- QR detection is local and offline. No HTTP requests, browser launches,
  screenshots, clipboard reads, email access, login state, or PDF downloads.
- URL classification is not a phishing verdict. The agent must separately review
  the destination and obtain any required authorization before taking action.
- The existing macOS-only interactive `open` command is intentionally not ported.

Dependencies: [ZXing.Net](https://github.com/micjahn/ZXing.Net) and its
[Windows binding](https://www.nuget.org/packages/ZXing.Net.Bindings.Windows.Compatibility),
licensed under Apache-2.0. This does not select a license for AgentQR itself.
