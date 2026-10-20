import base64
import datetime as dt
from email.message import EmailMessage
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/agentqr/scripts"
sys.path.insert(0, str(SCRIPTS))
from invoice_common import InvoiceError, digest, read_private_json
from invoice_flow import Batch
import invoice_files
import invoice_http
import invoice_mail
import invoices


def pdf(text="INVOICE Invoice No: 12345678"):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def mail(attachments=(), text="Invoice attached", html=None):
    message = EmailMessage()
    message["Subject"] = "Monthly invoice"
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    for name, data, mime in attachments:
        major, minor = mime.split("/")
        message.add_attachment(data, maintype=major, subtype=minor, filename=name)
    return message.as_bytes()


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"AGENTQR_INVOICE_HOME": str(self.root / "config")})
        self.environment.start()
        self.scanner = MagicMock(return_value=[{"payload": "https://issuer.example/claim?token=PRIVATE"}])
        self.fetcher = MagicMock(return_value=(pdf(), "application/pdf", "https://issuer.example/file"))
        self.batch = Batch(self.root / "batch", allowed=["issuer.example"], scanner=self.scanner,
                           fetcher=self.fetcher)

    def tearDown(self):
        self.batch.close()
        self.environment.stop()
        self.temp.cleanup()

    def test_pdf_bytes_preserved_and_duplicate_mail_not_duplicate_file(self):
        content = pdf()
        for ref in ("account|INBOX|1|1", "account|INBOX|1|2"):
            self.batch.ingest(ref, mail([("../../evil.pdf", content, "application/pdf")]))
        self.batch.process_local()
        report = self.batch.report()
        self.assertEqual(report["pdf_count"], 1)
        self.assertEqual(report["duplicate_files"], 1)
        self.assertEqual(Path(report["documents"][0]["file"]).read_bytes(), content)
        self.assertFalse((self.root / "evil.pdf").exists())
        self.assertNotIn("12345678", json.dumps(report))
        self.assertEqual(self.batch.report(True)["documents"][0]["invoice_number"], "12345678")

    def test_qr_requires_selected_https_download_then_saves(self):
        image = b"\x89PNG\r\n\x1a\nfake"
        self.batch.ingest("m1", mail([("qr.png", image, "image/png")]))
        self.batch.process_local()
        self.fetcher.assert_not_called()
        task = next(t for t in self.batch.tasks() if t["type"] == "url")
        self.assertEqual(task["status"], "qr_link_review")
        self.assertNotIn("PRIVATE", json.dumps(self.batch.report()))
        self.batch.process(task, explicit=True)
        self.assertEqual(self.batch.report()["pdf_count"], 1)
        self.assertEqual(self.batch.report()["pending_count"], 0)

    def test_remote_tracking_images_never_automatically_loaded(self):
        self.batch.ingest("m", mail(html='<img src="https://x.example/pixel" width="1">'
                                        '<img src="https://issuer.example/qr" alt="invoice QR">'))
        self.batch.process_local()
        self.fetcher.assert_not_called()
        tasks = self.batch.tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "image_review")

    def test_browser_handoff_and_import_resume(self):
        self.fetcher.return_value = (b"<html>Login using WeChat</html>", "text/html", "https://issuer.example/login")
        task = self.batch.add_url("m", "https://issuer.example/claim")
        self.batch.process(task, explicit=True)
        self.assertEqual(self.batch.task(task["id"])["status"], "browser_required")
        path = self.root / "download.pdf"
        path.write_bytes(b"<html>still login</html>")
        with self.assertRaises(InvoiceError):
            self.batch.resolve(task["id"], path)
        path.write_bytes(pdf())
        self.batch.resolve(task["id"], path)
        self.assertEqual(self.batch.task(task["id"])["status"], "resolved")
        self.assertEqual(self.batch.report()["pdf_count"], 1)

    def test_host_downloaded_qr_resumes_original_task(self):
        task = self.batch.add_url("m", "https://issuer.example/qr", status="failed")
        file = self.root / "download"
        file.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        self.batch.import_download(task["id"], file)
        child = next(t for t in self.batch.tasks() if t["parent"] == task["id"])
        self.assertEqual(child["status"], "qr_link_review")
        self.fetcher.assert_not_called()
        self.batch.process(child, explicit=True)
        self.assertEqual(self.batch.task(task["id"])["status"], "resolved")

    def test_plaintext_links_and_inline_images_are_candidates(self):
        message = EmailMessage()
        message["Subject"] = "Notice"
        message.set_content("Invoice https://issuer.example/claim?id=1")
        message.add_alternative('<img src="cid:q">', subtype="html")
        message.get_payload()[1].add_related(b"\x89PNG\r\n\x1a\nfake", maintype="image", subtype="png", cid="<q>")
        self.batch.ingest("m", message.as_bytes())
        self.batch.process_local()
        self.assertEqual(self.scanner.call_count, 1)
        self.assertEqual(sum(t["type"] == "url" for t in self.batch.tasks()), 2)

    def test_missing_html_attributes_and_nonweb_qr_do_not_abort_batch(self):
        self.scanner.return_value = [{"payload": "weixin://unsupported"}, {"payload": "https://[bad"}]
        self.batch.ingest("m", mail([("qr.png", b"\x89PNG\r\n\x1a\nfake", "image/png")],
                                    html="<a href>Invoice</a><img src alt><a href='mailto:a@example.com'>Contact</a>"))
        self.batch.process_local()
        self.assertEqual(sum(t["status"] == "unsupported_payload" for t in self.batch.tasks()), 2)

    def test_data_uri_qr_is_decoded_without_network(self):
        data = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        self.batch.ingest("m", mail(html=f'<img src="data:image/png;base64,{data}">'))
        self.batch.process_local()
        self.scanner.assert_called_once()
        self.fetcher.assert_not_called()

    def test_message_report_reveals_only_selected_mail(self):
        first = self.batch.ingest("m1", mail(text="First private body"))
        self.batch.ingest("m2", mail(text="Second private body"))
        self.assertNotIn("private body", json.dumps(self.batch.report()))
        report = self.batch.report(True, first)
        self.assertIn("First private body", json.dumps(report))
        self.assertNotIn("Second private body", json.dumps(report))

    def test_reviewed_job_ad_can_be_dismissed_and_stays_reviewed(self):
        raw = mail(text="QR code to register for a career event",
                   html='<a href="https://example.com/register">QR code registration</a>')
        ident = self.batch.ingest("ad", raw)
        self.batch.dismiss_message(ident, "Career event, not an invoice")
        self.batch.ingest("ad", raw)
        self.assertEqual(self.batch.report()["status"], "complete")

    def test_ofd_kept_as_alternative_not_unpacked_as_fonts_and_claim_qr(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("font.ttf", b"fake-font")
            zipped.writestr("Doc.xml", b"<ofd/>")
        self.batch.ingest("ofd", mail([("invoice.ofd", archive.getvalue(), "application/ofd")]))
        self.batch.process_local()
        self.assertEqual(len(self.batch.tasks()), 1)
        self.assertEqual(self.batch.tasks()[0]["status"], "alternative_format")
        self.assertEqual(self.batch.report()["status"], "needs_attention")

    def test_retry_does_not_duplicate_tasks_and_failed_scan_remains_pending(self):
        self.scanner.side_effect = InvoiceError("QR_SCAN_FAILED")
        raw = mail([("qr", b"\x89PNG\r\n\x1a\nfake", "image/png")])
        for _ in range(2):
            self.batch.ingest("m", raw)
            self.batch.process_local()
        self.assertEqual(len(self.batch.tasks()), 1)
        self.assertEqual(self.batch.report()["status"], "needs_attention")

    def test_invoice_notice_without_usable_input_is_not_complete(self):
        self.batch.ingest("m", mail(text="Your invoice is ready, please contact us"))
        self.batch.process_local()
        self.assertEqual(self.batch.report()["messages"][0]["status"], "needs_manual_review")

    def test_invalid_pdf_does_not_enter_pdf_directory(self):
        self.batch.ingest("m", mail([("bad.pdf", b"%PDF-1.7\nbroken", "application/pdf")]))
        self.batch.process_local()
        self.assertEqual(self.batch.report()["pdf_count"], 0)
        self.assertEqual(self.batch.report()["pending_count"], 1)
        self.assertFalse(list(self.batch.pdfs.iterdir()))

    def test_noninvoice_pdf_is_separate_review_not_invoice(self):
        self.batch.ingest("m", mail([("trip.pdf", pdf("Travel itinerary"), "application/pdf")]))
        self.batch.process_local()
        report = self.batch.report()
        self.assertEqual(report["invoice_candidates"], 0)
        self.assertEqual(report["status"], "needs_attention")
        self.assertIn("/review/", report["documents"][0]["file"].replace("\\", "/"))

    def test_same_number_different_bytes_retained_for_review(self):
        self.batch.ingest("m", mail([("a.pdf", pdf(), "application/pdf"),
                                     ("b.pdf", pdf("INVOICE Invoice No: 12345678 revised"), "application/pdf")]))
        self.batch.process_local()
        self.assertEqual(self.batch.report()["pdf_count"], 2)
        self.assertEqual(self.batch.report()["status"], "needs_attention")
        self.assertTrue(self.batch.report()["documents"][1]["possible_duplicate_of"])

    def test_zip_support_and_path_traversal_rejected_before_any_processing(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("normal.pdf", pdf())
        self.batch.ingest("m", mail([("invoices.zip", stream.getvalue(), "application/zip")]))
        self.batch.process_local()
        self.assertEqual(self.batch.report()["pdf_count"], 1)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("ok.pdf", pdf())
            archive.writestr("../evil.pdf", pdf())
        with self.assertRaisesRegex(InvoiceError, "UNSAFE_ARCHIVE"):
            list(invoice_files.zip_members(stream.getvalue()))

    def test_child_download_resolves_browser_parent(self):
        parent = self.batch.add_url("m", "https://issuer.example/view", status="browser_required")
        child = self.batch.add_url("m", "https://issuer.example/download", parent=parent["id"])
        self.batch.process(child, explicit=True)
        self.assertEqual(self.batch.task(parent["id"])["status"], "resolved")

    def test_uidvalidity_in_identity(self):
        raw = mail([("a.pdf", pdf(), "application/pdf")])
        for ref in ("account|INBOX|1|9", "account|INBOX|2|9", "other|INBOX|1|9"):
            self.batch.ingest(ref, raw)
        self.batch.process_local()
        self.assertEqual(self.batch.report()["messages_checked"], 3)
        self.assertEqual(self.batch.report()["pdf_count"], 1)

    def test_restart_reuses_ledger(self):
        raw = mail([("a.pdf", pdf(), "application/pdf")])
        self.batch.ingest("m", raw)
        self.batch.process_local()
        self.batch.close()
        self.batch = Batch(self.root / "batch")
        self.batch.ingest("m", raw)
        self.batch.process_local()
        self.assertEqual(self.batch.report()["pdf_count"], 1)
        self.assertEqual(len(self.batch.tasks()), 1)

    def test_real_native_qr_to_pdf_when_runtime_is_installed(self):
        import runtime
        from invoice_flow import scan_image
        if not runtime.installed():
            self.skipTest("Native QR runtime not installed on this runner")
        self.batch.scan = scan_image
        image = base64.b64decode((SCRIPTS.parent / "assets/self-test.png.base64").read_bytes())
        self.batch.ingest("actual-native", mail([("qr.png", image, "image/png")]))
        self.batch.process_local()
        task = next(t for t in self.batch.tasks() if t["type"] == "url")
        self.assertEqual(task["value"], runtime.PAYLOAD)
        self.batch.process(task, explicit=True)
        self.assertEqual(self.batch.report()["invoice_candidates"], 1)


class SecurityTests(unittest.TestCase):
    def test_configure_saves_locally_without_echoing_code(self):
        import contextlib
        code = "abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {"AGENTQR_INVOICE_HOME": folder}), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", return_value="test@qq.com"), \
                patch.object(invoice_mail.getpass, "getpass", return_value=code), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = invoice_mail.configure()
            self.assertEqual(result["status"], "configured")
            self.assertNotIn(code, output.getvalue() + json.dumps(result))
            self.assertEqual(read_private_json(invoice_mail.credential_path())["authorization_code"], code)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(invoice_mail.credential_path().stat().st_mode), 0o600)

    def test_mail_code_not_inherited_by_native_decoder(self):
        import runtime
        with patch.dict(os.environ, {"QQMAIL_ACCOUNT": "test@qq.com", "QQMAIL_AUTH_CODE": "abcdefghijklmnop"}), \
                patch.object(runtime.subprocess, "run", return_value=MagicMock(stdout="[]")) as run:
            runtime.invoke(Path("decoder"), ["scan", "synthetic.png"])
            self.assertNotIn("QQMAIL_AUTH_CODE", run.call_args.kwargs["env"])
            self.assertNotIn("QQMAIL_ACCOUNT", run.call_args.kwargs["env"])

    def test_only_exact_https_public_hosts(self):
        for url in ("http://issuer.example/x", "https://user:pass@issuer.example/x",
                    "https://issuer.example:8443/x", "file:///etc/passwd",
                    "https://issuer.example.evil/x", "https://issuer.example/\r\nx"):
            with self.subTest(url=url), self.assertRaises(InvoiceError):
                invoice_http.validate_url(url, {"issuer.example"})
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"):
            with patch.object(socket, "getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443))]):
                with self.assertRaisesRegex(InvoiceError, "NON_PUBLIC_ADDRESS"):
                    invoice_http.public_addresses("issuer.example")

    def test_redirect_not_contacted_without_approval(self):
        response = MagicMock()
        response.status = 302
        response.getheader.return_value = "https://other.example/file?secret=redacted"
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(invoice_http, "public_addresses", return_value=["8.8.8.8"]), \
                patch.object(invoice_http.http.client, "HTTPSConnection", return_value=connection) as factory:
            with self.assertRaisesRegex(InvoiceError, "DOMAIN_APPROVAL_REQUIRED"):
                invoice_http.get("https://issuer.example/start", {"issuer.example"})
            self.assertEqual(factory.call_count, 1)
            headers = connection.request.call_args.kwargs["headers"]
            self.assertNotIn("Cookie", headers)
            self.assertNotIn("Authorization", headers)

    def test_credentials_only_validate_qq_and_never_accept_login_password(self):
        with self.assertRaises(InvoiceError):
            invoice_mail.validate_credentials("a@evil.test", "a" * 16)
        with self.assertRaises(InvoiceError):
            invoice_mail.validate_credentials("a@qq.com", "password123")
        with patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(InvoiceError, "USER_TERMINAL_REQUIRED"):
                invoice_mail.configure()

    @unittest.skipIf(os.name == "nt", "POSIX mode check")
    def test_private_credentials_file_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text("{}")
            path.chmod(0o644)
            with self.assertRaisesRegex(InvoiceError, "CREDENTIAL_FILE_NOT_PRIVATE"):
                read_private_json(path)

    def test_imap_readonly_peek_no_seen_or_write_and_errors_per_mail(self):
        client = MagicMock()
        client.login.return_value = ("OK", [])
        client.select.return_value = ("OK", [])
        client.response.return_value = ("UIDVALIDITY", [b"123"])
        raw = mail()
        client.uid.side_effect = [("OK", [b"1 2"]), ("OK", [b"1 (RFC822.SIZE 100)"]),
                                 ("OK", [(b"1 BODY[]", raw)]), ("OK", [b"2 (RFC822.SIZE 999999999)"])]
        consume = MagicMock()
        with patch.object(invoice_mail, "credentials", return_value=("test@qq.com", "a" * 16)), \
                patch.object(invoice_mail.imaplib, "IMAP4_SSL", return_value=client):
            result = invoice_mail.collect_imap("2026-10-13", "2026-10-21", "INBOX", consume)
        self.assertTrue(client.select.call_args.kwargs["readonly"])
        self.assertIn("(BODY.PEEK[])", [c.args[-1] for c in client.uid.call_args_list])
        self.assertEqual(consume.call_args.args[0], "test@qq.com|INBOX|123|1")
        self.assertEqual(result["read_errors"], [{"uid": "2", "error": "MESSAGE_TOO_LARGE"}])
        client.logout.assert_called_once()

    def test_date_range_before_is_exclusive_and_locale_independent(self):
        self.assertEqual(invoice_mail.imap_date(dt.date(2026, 10, 13)), "13-Oct-2026")
        with self.assertRaises(InvoiceError):
            invoice_mail.date_range("2026-10-21", "2026-10-13")


if __name__ == "__main__":
    unittest.main()
