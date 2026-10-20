"""A resumable local ledger for mail -> attachment/QR/link -> checked PDF."""

from collections import Counter
import base64
import binascii
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urljoin, urlsplit

from invoice_common import InvoiceError, bounded_read, digest, private_dir, private_write
import invoice_files
import invoice_http
from invoice_mail import parse_message
import runtime

HINT = re.compile(r"发票|开票|领票|二维码|invoice|fapiao|kaipiao|\.pdf(?:\?|$)", re.I)
ACTION = re.compile(r"发票|领票|下载|二维码|invoice|download|fapiao|kaipiao|\.pdf(?:\?|$)", re.I)
DONE = {"saved", "duplicate", "decoded", "unpacked", "no_qr", "ignored", "resolved",
        "data_only", "alternative_format"}


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.images, self.words = [], [], []
        self.anchor = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "a":
            self.anchor = [attrs.get("href") or "", ""]
        if tag == "img":
            if any(str(attrs.get(size, "")).rstrip("px") in ("0", "1", "2") for size in ("width", "height")):
                return
            self.images.append((attrs.get("src") or "", attrs.get("alt") or ""))

    def handle_data(self, data):
        if not self.hidden:
            self.words.append(data)
            if self.anchor is not None:
                self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.links.append(tuple(self.anchor))
            self.anchor = None
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)


def scan_image(path):
    program = runtime.installed()
    if not program:
        runtime.install()
        program = runtime.installed()
    result = runtime.invoke(program, ["scan", str(path), "--reveal"])
    if result.returncode == 3:
        return []
    if result.returncode:
        raise InvoiceError("QR_SCAN_FAILED")
    return json.loads(result.stdout)


class Batch:
    def __init__(self, output, allowed=(), scanner=scan_image, fetcher=invoice_http.get, inspect=invoice_files.pdf_info):
        self.root = private_dir(output)
        self.private = private_dir(self.root / ".agentqr")
        self.blobs = private_dir(self.private / "inputs")
        self.pdfs = private_dir(self.root / "pdf")
        self.review = private_dir(self.root / "review")
        self.allowed = {x.lower() for x in allowed}
        self.scan, self.fetch, self.inspect = scanner, fetcher, inspect
        database = self.private / "state.sqlite3"
        if database.is_symlink():
            raise InvoiceError("UNSAFE_LOCAL_PATH")
        self.db = sqlite3.connect(database, timeout=2)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, message TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (hash TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if os.name != "nt":
            database.chmod(0o600)
        # One active writer per batch; a crash rolls back ledger changes, not originals.
        self.db.execute("BEGIN IMMEDIATE")

    def close(self):
        self.db.commit()
        self.db.close()

    def put_task(self, task):
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                        (task["id"], task["message"], json.dumps(task, ensure_ascii=False)))

    def task(self, ident):
        row = self.db.execute("SELECT value FROM tasks WHERE id = ?", (ident,)).fetchone()
        if row is None:
            raise InvoiceError("TASK_NOT_FOUND")
        return json.loads(row[0])

    def tasks(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT value FROM tasks ORDER BY rowid")]

    def add(self, message, type_, value, *, name="", parent=None, depth=0, status="pending"):
        key = digest(f"{message}|{type_}|{value}".encode())[:24]
        row = self.db.execute("SELECT value FROM tasks WHERE id = ?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        task = {"id": key, "message": message, "type": type_, "value": value, "name": name,
                "parent": parent, "depth": depth, "status": status}
        self.put_task(task)
        return task

    def add_file(self, message, data, name="", parent=None, depth=0):
        hash_ = digest(data)
        path = self.blobs / hash_
        private_write(path, data)
        return self.add(message, "file", hash_, name=name, parent=parent, depth=depth)

    def add_url(self, message, url, **kwargs):
        try:
            scheme = urlsplit(url).scheme.lower()
            if scheme in ("http", "https"):
                return self.add(message, "url", url, **kwargs)
            if not scheme:
                return self.add(message, "payload", url, **{**kwargs, "status": "data_only"})
        except ValueError:
            pass
        return self.add(message, "payload", url, **{**kwargs, "status": "unsupported_payload"})

    def ingest(self, reference, raw):
        ident = digest(reference.encode())[:24]
        parsed = parse_message(raw)
        html = Links()
        html.feed(parsed["html"])
        text = parsed["subject"] + "\n" + parsed["text"] + "\n" + " ".join(html.words)
        candidates = bool(HINT.search(text))
        previous = self.db.execute("SELECT metadata FROM messages WHERE id = ?", (ident,)).fetchone()
        metadata = json.loads(previous[0]) if previous else {}
        metadata.update(id=ident, reference=reference, subject=parsed["subject"], body=text, invoice_hint=candidates)
        self.db.execute("INSERT OR REPLACE INTO messages VALUES (?, ?)",
                        (ident, json.dumps(metadata)))
        for part in parsed["files"]:
            type_ = invoice_files.kind(part["data"])
            if type_ in ("pdf", "zip", "image"):
                # Decode every locally embedded image, even if its subject is not invoice-related.
                self.add_file(ident, part["data"], part["name"])
            elif candidates:
                self.add_file(ident, part["data"], part["name"])
        for url, label in html.links:
            if url.startswith(("https://", "http://")) and (HINT.search(url + label) or candidates):
                self.add_url(ident, url, name=label, status="link_review")
        for url in re.findall(r"https?://[^\s<>\"']+", parsed["text"]):
            url = url.rstrip(".,;，。；)")
            if candidates or HINT.search(url):
                self.add_url(ident, url, status="link_review")
        # CID images are already decoded above. External resources require selection.
        for url, label in html.images:
            if candidates and url.startswith(("https://", "http://")):
                self.add_url(ident, url, name=label, status="image_review")
            elif url.startswith("data:image/") and ";base64," in url:
                try:
                    data = base64.b64decode(url.split(",", 1)[1], validate=True)
                    if invoice_files.kind(data) == "image":
                        self.add_file(ident, data, label)
                except (binascii.Error, ValueError):
                    self.add(ident, "invalid_inline_image", digest(url.encode()), status="failed")
        return ident

    def save_pdf(self, task, data):
        hash_ = digest(data)
        path = self.blobs / hash_
        private_write(path, data)
        existing = self.db.execute("SELECT value FROM documents WHERE hash = ?", (hash_,)).fetchone()
        if existing:
            doc = json.loads(existing[0])
            # Repair a removed/corrupt output from checked source bytes on a repeat run.
            target = (self.pdfs if doc.get("invoice_like") else self.review) / f"{hash_}.pdf"
            if not target.exists() or digest(bounded_read(target)) != hash_:
                private_write(target, data)
            task.update(status="duplicate", document=hash_)
            return
        info = self.inspect(path)
        target = (self.pdfs if info.get("invoice_like") else self.review) / f"{hash_}.pdf"
        private_write(target, data)
        doc = {"hash": hash_, "file": str(target), **info}
        # Same invoice number with different bytes is a conflict to review, not silent deletion.
        if info.get("invoice_number"):
            same = [json.loads(row[0]) for row in self.db.execute("SELECT value FROM documents")]
            matches = [d["hash"] for d in same if d.get("invoice_number") == info["invoice_number"]
                       and d.get("invoice_code") == info.get("invoice_code")]
            if matches:
                doc["possible_duplicate_of"] = matches
        self.db.execute("INSERT INTO documents VALUES (?, ?)", (hash_, json.dumps(doc)))
        task.update(status="saved", document=hash_)

    def process_bytes(self, task, data, mime="", url=""):
        type_ = invoice_files.kind(data)
        if type_ == "pdf":
            self.save_pdf(task, data)
        elif Path(task["name"]).suffix.lower() in (".ofd", ".xml"):
            # OFD is itself a ZIP container. Keep the original alternative format
            # instead of treating its fonts/XML/identity QR as claim attachments.
            task["status"] = "alternative_format"
        elif type_ == "image":
            path = self.blobs / digest(data)
            private_write(path, data)
            found = self.scan(path)
            for item in found:
                self.add_url(task["message"], item["payload"], parent=task["id"],
                             depth=task["depth"] + 1, status="qr_link_review")
            task["status"] = "decoded" if found else "no_qr"
        elif type_ == "zip":
            if task["depth"] >= 2:
                raise InvoiceError("ARCHIVE_DEPTH_LIMIT")
            supported, skipped = 0, 0
            for name, content in invoice_files.zip_members(data):
                if invoice_files.kind(content) in ("pdf", "image", "zip"):
                    self.add_file(task["message"], content, name, task["id"], task["depth"] + 1)
                    supported += 1
                else:
                    skipped += 1
            task["skipped_archive_members"] = skipped
            task["status"] = "unpacked" if supported else "unsupported_archive"
        elif url and ("html" in mime.lower() or b"<html" in data[:1024].lower() or b"<!doctype" in data[:1024].lower()):
            task["status"] = "browser_required"
            if task["depth"] < 2:
                html = Links()
                html.feed(data.decode("utf-8", errors="replace"))
                for link, label in html.links[:100]:
                    if ACTION.search(label + link):
                        self.add_url(task["message"], urljoin(url, link), name=label, parent=task["id"],
                                     depth=task["depth"] + 1, status="link_review")
                for link, label in html.images[:100]:
                    if ACTION.search(label + link):
                        self.add_url(task["message"], urljoin(url, link), name=label, parent=task["id"],
                                     depth=task["depth"] + 1, status="image_review")
        else:
            raise InvoiceError("UNSUPPORTED_OR_NON_PDF_RESPONSE")

    def process(self, task, explicit=False):
        if task["status"] in DONE:
            return
        try:
            task.pop("error", None)
            if task["type"] == "file":
                self.process_bytes(task, bounded_read(self.blobs / task["value"]))
            elif task["type"] == "url":
                # Domain approval alone does not select mail links/tracking images.
                if not explicit:
                    return
                data, mime, final = self.fetch(task["value"], self.allowed)
                self.process_bytes(task, data, mime, final)
            else:
                return
        except (InvoiceError, runtime.Failure) as error:
            task.update(status="failed", error=error.code)
            if error.code == "DOMAIN_APPROVAL_REQUIRED":
                task["status"] = "domain_approval_required"
            elif error.code == "BROWSER_AUTH_REQUIRED":
                task["status"] = "browser_required"
        self.put_task(task)
        if task.get("document"):
            self.resolve_parents(task)

    def resolve_parents(self, task):
        seen = set()
        while task.get("parent") and task["parent"] not in seen:
            seen.add(task["parent"])
            parent = self.task(task["parent"])
            if parent["type"] == "url":
                parent.update(status="resolved", document=task["document"])
                self.put_task(parent)
            task = {**parent, "document": task["document"]}

    def process_local(self):
        seen = set()
        while True:
            pending = [t for t in self.tasks() if t["type"] == "file" and t["id"] not in seen
                       and t["status"] not in DONE]
            if not pending:
                break
            for task in pending:
                seen.add(task["id"])
                self.process(task)

    def resolve(self, ident, path):
        task = self.task(ident)
        data = bounded_read(path)
        if invoice_files.kind(data) != "pdf":
            raise InvoiceError("RESOLUTION_REQUIRES_PDF")
        self.save_pdf(task, data)
        task["status"] = "resolved"
        self.put_task(task)
        self.resolve_parents(task)

    def import_download(self, ident, path):
        task = self.task(ident)
        if task["type"] != "url":
            raise InvoiceError("TASK_IS_NOT_URL")
        self.process_bytes(task, bounded_read(path), url=task["value"])
        task.pop("error", None)
        self.put_task(task)
        if task.get("document"):
            self.resolve_parents(task)
        self.process_local()

    def dismiss_message(self, ident, reason):
        row = self.db.execute("SELECT metadata FROM messages WHERE id = ?", (ident,)).fetchone()
        if not row:
            raise InvoiceError("MESSAGE_NOT_FOUND")
        tasks = [t for t in self.tasks() if t["message"] == ident]
        if any(t.get("document") for t in tasks):
            raise InvoiceError("MESSAGE_HAS_SAVED_PDF")
        message = json.loads(row[0])
        message.update(review="not_invoice", review_reason=reason[:1000])
        self.db.execute("UPDATE messages SET metadata = ? WHERE id = ?", (json.dumps(message), ident))
        for task in tasks:
            task.update(status="ignored", reason=reason[:1000])
            self.put_task(task)

    def report(self, reveal=False, message_id=None):
        tasks = self.tasks()
        docs = [json.loads(row[0]) for row in self.db.execute("SELECT value FROM documents")]
        messages = [json.loads(row[0]) for row in self.db.execute("SELECT metadata FROM messages")]
        if message_id:
            messages = [m for m in messages if m["id"] == message_id]
            if not messages:
                raise InvoiceError("MESSAGE_NOT_FOUND")
            tasks = [t for t in tasks if t["message"] == message_id]
            docs = [d for d in docs if d["hash"] in {t.get("document") for t in tasks}]
        pending = [t for t in tasks if t["status"] not in DONE]
        for message in messages:
            related = [t for t in tasks if t["message"] == message["id"]]
            message["tasks"] = dict(Counter(t["status"] for t in related))
            message["status"] = "pending" if any(t["status"] not in DONE for t in related) else "processed"
            if (message["invoice_hint"] and message.get("review") != "not_invoice"
                    and not any(t.get("document") for t in related) and message["status"] != "pending"):
                message["status"] = "needs_manual_review"
        metadata = {key: json.loads(value) for key, value in self.db.execute("SELECT key, value FROM meta")}
        read_errors = metadata.get("collection", {}).get("read_errors", [])
        result = {"output": str(self.root), "messages_checked": len(messages), "pdf_count": len(docs),
                  "invoice_candidates": sum(bool(d.get("invoice_like")) for d in docs),
                  "duplicate_files": sum(t["status"] == "duplicate" for t in tasks),
                  "pending_count": len(pending), "messages": messages, "tasks": tasks,
                  "documents": docs, **metadata}
        incomplete = metadata.get("collection", {}).get("state") in ("reading", "partial", "failed")
        result["status"] = "needs_attention" if (pending or read_errors or incomplete or any(
            m["status"] == "needs_manual_review" for m in messages) or any(
            not d.get("invoice_like") or d.get("possible_duplicate_of") for d in docs)) else "complete"
        if not reveal:
            for message in messages:
                message.pop("subject", None)
                message.pop("reference", None)
                message.pop("body", None)
            for task in tasks:
                value = task.pop("value")
                task.pop("name", None)
                if task["type"] == "url":
                    task["host"] = invoice_http.url_host(value)
            for doc in docs:
                for key in ("invoice_number", "invoice_code", "text_preview"):
                    doc.pop(key, None)
        return result

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
