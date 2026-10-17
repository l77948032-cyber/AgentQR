import AppKit
import Foundation
import AgentQRCore

@MainActor
func run() async -> Int32 {
    let args = Array(CommandLine.arguments.dropFirst())
    let usage = """
    AgentQR: local QR decoding for macOS (technical prototype)

    agentqr scan IMAGE [--reveal]
    agentqr open IMAGE [--index N] [--app BUNDLE_ID]

    scan prints JSON with private paths and tokens redacted by default.
    --reveal includes full QR payloads in stdout; do not publish that output.
    open previews a selected HTTP(S) URL and asks for confirmation in a terminal.
    --index is required when an image contains multiple codes (1-based).
    --app selects an installed browser, for example com.google.Chrome.
    The browser chooses its profile. No cookies are read, copied, or exported.
    Login, CAPTCHA, app-only links and PDF download remain browser-side steps.
    """
    guard let command = args.first, command != "--help", command != "-h" else {
        print(usage)
        return 0
    }
    guard ["scan", "open"].contains(command), args.count >= 2, !args[1].hasPrefix("--") else {
        print(usage)
        return 2
    }
    var reveal = false
    var selected: Int?
    var appID: String?
    var cursor = 2
    while cursor < args.count {
        switch args[cursor] {
        case "--reveal" where command == "scan": reveal = true
        case "--index" where command == "open":
            cursor += 1
            guard cursor < args.count, let value = Int(args[cursor]), value > 0 else { return 2 }
            selected = value
        case "--app" where command == "open":
            cursor += 1
            guard cursor < args.count, !args[cursor].isEmpty else { return 2 }
            appID = args[cursor]
        default:
            fputs("Invalid option. See --help.\n", stderr)
            return 2
        }
        cursor += 1
    }
    do {
        let payloads = try Scanner.decode(URL(fileURLWithPath: args[1]))
        guard !payloads.isEmpty else {
            print("{\"error\":\"NO_QR_CODE\"}")
            return 3
        }
        if command == "scan" {
            let results = payloads.enumerated().map { Scanner.describe($0.element, index: $0.offset + 1, reveal: reveal) }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            print(String(decoding: try encoder.encode(results), as: UTF8.self))
            return 0
        }
        guard payloads.count == 1 || selected != nil else {
            fputs("Multiple QR codes. Run scan, then choose --index N.\n", stderr)
            return 2
        }
        let index = selected ?? 1
        guard index <= payloads.count, let url = Scanner.webURL(payloads[index - 1]) else {
            fputs("Selected code is not an allowed HTTP(S) URL.\n", stderr)
            return 4
        }
        guard isatty(STDIN_FILENO) == 1 else {
            fputs("Opening requires an interactive terminal.\n", stderr)
            return 4
        }
        print("Destination: \(url.host!)")
        print("URL: \(url.absoluteString)")
        if url.scheme?.lowercased() == "http" {
            print("Warning: this URL is not encrypted (HTTP).")
        }
        print("Open in \(appID ?? "default browser")? [y/N]", terminator: " ")
        guard readLine()?.lowercased() == "y" else { return 0 }
        if let appID {
            guard let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: appID) else {
                fputs("Requested browser is not installed.\n", stderr)
                return 4
            }
            _ = try await NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: .init())
        } else if !NSWorkspace.shared.open(url) {
            fputs("Browser could not open the URL.\n", stderr)
            return 4
        }
        return 0
    } catch {
        // Framework errors can contain private source paths or payloads.
        fputs("SCAN_OR_OPEN_FAILED\n", stderr)
        return 1
    }
}

exit(await run())
