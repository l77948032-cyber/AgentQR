#!/usr/bin/env python3
"""Stable entry point for a skill installed without Swift or .NET SDKs."""

import argparse
import json
import subprocess
import sys

import runtime


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "invoices":
        import invoices
        return invoices.main(argv[1:])
    parser = argparse.ArgumentParser(description="AgentQR: install, verify and decode QR images locally.")
    parser.add_argument("--version", action="version", version=runtime.VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    installer = commands.add_parser("install", help="Download a checked native runtime and run a real QR self-test")
    installer.add_argument("--bundle-dir", help="Use a local release bundle directory instead of the network")
    commands.add_parser("doctor", help="Check installation without reading accounts or connecting services")
    commands.add_parser("self-test", help="Verify real decoding and redaction using a synthetic QR")
    scan = commands.add_parser("scan", help="Decode a local image; installs the native runtime if missing")
    scan.add_argument("image")
    scan.add_argument("--reveal", action="store_true", help="Include sensitive original payloads in output")
    commands.add_parser("invoices", help="Collect QQ Mail invoices; run invoices --help for commands")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = runtime.install(args.bundle_dir)
        elif args.command == "doctor":
            result = runtime.doctor()
        else:
            program = runtime.installed()
            if not program:
                runtime.install()
                program = runtime.installed()
            if args.command == "self-test":
                result = runtime.self_test(program)
            else:
                arguments = ["scan", args.image] + (["--reveal"] if args.reveal else [])
                response = runtime.invoke(program, arguments)
                # The macOS prototype has text-only errors. Normalize without
                # forwarding exception text or private filenames into agent logs.
                if response.returncode != 0:
                    codes = {1: "SCAN_OR_OPEN_FAILED", 2: "INVALID_ARGUMENTS", 3: "NO_QR_CODE", 4: "UNSUPPORTED_PLATFORM"}
                    print(json.dumps({"error": codes.get(response.returncode, "DECODER_FAILED")}))
                    return response.returncode if response.returncode in codes else 1
                result = json.loads(response.stdout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command != "doctor" or result["status"] == "ready" else 5
    except runtime.Failure as error:
        print(json.dumps({"error": error.code, "message": error.message}))
        return 1
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print(json.dumps({"error": "SETUP_OR_RUNTIME_FAILED", "message": "Cannot complete setup or decoding; no private exception details were logged."}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
