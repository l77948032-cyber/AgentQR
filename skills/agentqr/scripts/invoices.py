"""Command line workflow for collecting invoices from QQ Mail."""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from invoice_common import InvoiceError, bounded_read, clean_env, private_dir
from invoice_flow import Batch
import invoice_mail
import runtime


def install():
    home = private_dir(invoice_mail.credential_path().parent)
    venv = home / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                       capture_output=True, timeout=120, env=clean_env())
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(Path(__file__).with_name("requirements-invoices.txt"))],
                   check=True, capture_output=True, timeout=180, env=clean_env())
    result = runtime.install()
    return {"status": "ready", "pdf_dependency": "pypdf", "decoder": result}


def parser():
    root = argparse.ArgumentParser(description="QQ Mail -> local QR decoding -> invoice PDF collection")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="Install PDF dependency in a private venv and verify QR runtime")
    commands.add_parser("configure", help="User-only hidden terminal entry of a QQ authorization code")
    commands.add_parser("status", help="Check QQ IMAP access; never return the authorization code")
    for name in ("collect", "report", "fetch", "resolve", "import-download", "dismiss"):
        command = commands.add_parser(name)
        command.add_argument("--output", required=True, help="Private batch directory, outside the repository")
        if name == "collect":
            command.add_argument("--since", required=True, help="Inclusive server receive date, YYYY-MM-DD")
            command.add_argument("--before", required=True, help="Exclusive server receive date, YYYY-MM-DD")
            command.add_argument("--folder", default="INBOX")
            command.add_argument("--eml-dir", help="Offline .eml fixtures; no mailbox connection")
        if name == "fetch":
            command.add_argument("--allow-host", action="append", default=[], required=True,
                                 help="Exact HTTPS issuer host reviewed by the user/agent for this task")
        if name in ("fetch", "resolve", "import-download"):
            command.add_argument("--task", required=True)
        if name in ("resolve", "import-download"):
            command.add_argument("--file", required=True, help="File downloaded using the authorized host tools")
        if name == "dismiss":
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument("--task")
            choice.add_argument("--message", help="Mark a reviewed non-invoice email as unrelated")
            command.add_argument("--reason", required=True, help="Why this candidate is not relevant to the invoice")
        if name == "report":
            command.add_argument("--message", help="Inspect only one message ID from the redacted report")
            command.add_argument("--reveal", action="store_true",
                                 help="Include private URLs and PDF text for the authorized agent task")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    batch = None
    try:
        if args.command == "configure":
            result = invoice_mail.configure()
        elif args.command == "install":
            result = install()
        elif args.command == "status":
            # Empty future window performs a real read-only authentication check.
            today = dt.date.today()
            invoice_mail.collect_imap(str(today + dt.timedelta(days=1)),
                                      str(today + dt.timedelta(days=2)), "INBOX", lambda *_: None)
            result = {"status": "connected", "host": invoice_mail.HOST, "access": "readonly"}
        else:
            if args.command == "collect":
                invoice_mail.date_range(args.since, args.before)
            batch = Batch(args.output, getattr(args, "allow_host", ()))
            if args.command == "collect":
                batch.set_meta("collection", {"state": "reading", "since": args.since,
                                              "before": args.before, "folder": args.folder})
                if args.eml_dir:
                    files = sorted(Path(args.eml_dir).glob("*.eml"))
                    errors = []
                    for path in files:
                        try:
                            batch.ingest("eml:" + str(path.resolve()), bounded_read(path))
                        except InvoiceError as error:
                            errors.append({"file": path.name, "error": error.code})
                    collection = {"source": "eml", "messages_in_range": len(files), "read_errors": errors,
                                  "since": args.since, "before": args.before,
                                  "note": "Offline mode processes every supplied EML; dates are batch labels."}
                else:
                    collection = invoice_mail.collect_imap(args.since, args.before, args.folder, batch.ingest)
                batch.set_meta("collection", {**collection, "state": "partial" if collection["read_errors"] else "complete"})
                batch.process_local()
            elif args.command == "fetch":
                task = batch.task(args.task)
                if task["type"] != "url":
                    raise InvoiceError("TASK_IS_NOT_URL")
                batch.process(task, explicit=True)
                batch.process_local()
            elif args.command == "resolve":
                batch.resolve(args.task, args.file)
            elif args.command == "import-download":
                batch.import_download(args.task, args.file)
            elif args.command == "dismiss":
                if args.message:
                    batch.dismiss_message(args.message, args.reason)
                else:
                    task = batch.task(args.task)
                    if task["status"] in ("saved", "duplicate", "resolved"):
                        raise InvoiceError("CANNOT_DISMISS_SAVED_PDF")
                    task.update(status="ignored", reason=args.reason[:1000])
                    batch.put_task(task)
            result = batch.report(getattr(args, "reveal", False), getattr(args, "message", None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (InvoiceError, runtime.Failure) as error:
        print(json.dumps({"error": error.code}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({"error": "INVOICE_OPERATION_FAILED",
                          "message": "No credential or private exception details were logged."}))
        return 1
    finally:
        if batch:
            batch.close()


if __name__ == "__main__":
    sys.exit(main())
