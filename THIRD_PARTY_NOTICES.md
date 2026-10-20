# Third-party notices

AgentQR's own source is MIT licensed. Dependencies keep their own licenses.

## macOS

The decoder uses Apple's Vision, AppKit and Foundation system frameworks.
These are provided by macOS, not redistributed in AgentQR's archive. No
third-party QR decoding library is bundled with the macOS runtime.

## Windows

The self-contained Windows CLI redistributes:

| Component | Version | License / upstream |
| --- | --- | --- |
| ZXing.Net | 0.16.11 | Apache-2.0, https://github.com/micjahn/ZXing.Net |
| ZXing.Net.Bindings.Windows.Compatibility | 0.16.16 | Apache-2.0, same upstream |
| System.Drawing.Common | 10.0.11 | MIT, https://github.com/dotnet/runtime |
| Microsoft.Win32.SystemEvents | 10.0.11 | MIT, same upstream |
| .NET runtime | Resolved by the .NET 10 SDK during release build | MIT plus bundled third-party notices |

ZXing.Net and its Windows binding are by Michael Jahn and contributors, based
on ZXing by the ZXing authors. AgentQR links to the unmodified NuGet packages.
Their Apache-2.0 license is included in `licenses/Apache-2.0.txt`.

Microsoft's package license is included in `licenses/dotnet-library-MIT.txt`.
Release packaging also copies the LICENSE and ThirdPartyNotices files from
the exact .NET runtime pack used by the build into the archive's `licenses/`
directory. The runtime version is recorded in the bundled `agentqr.deps.json`.
Package versions and integrity hashes for application dependencies are recorded
in `windows/AgentQR/packages.lock.json`.

Test-only NuGet packages and build SDKs are not included in the runtime ZIPs.
The Python skill launcher uses the host's Python standard library; it does not
redistribute a Python interpreter or require pip packages.
# Invoice PDF parsing

The optional invoice workflow installs `pypdf` 6.21.1 into a local virtual
environment. pypdf is distributed under the BSD-3-Clause license. Its original
license is included in the installed Python distribution.
Source: https://github.com/py-pdf/pypdf

