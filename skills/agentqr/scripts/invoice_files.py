"""Inspect PDFs in a bounded child process and unpack only ordinary ZIP members."""

import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import unicodedata
import zipfile

from invoice_common import InvoiceError, MAX_BYTES, clean_env


def kind(data):
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")):
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"%PDF-"):
        return "pdf"
    return "other"


def zip_members(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 100 or sum(x.file_size for x in members) > 50 * 1024 * 1024:
                raise InvoiceError("ARCHIVE_TOO_LARGE")
            for item in members:
                name = item.filename.replace("\\", "/")
                if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                        or ":" in name or stat.S_ISLNK(item.external_attr >> 16)
                        or item.file_size > MAX_BYTES or item.flag_bits & 1):
                    raise InvoiceError("UNSAFE_ARCHIVE")
            for item in members:
                if not item.is_dir():
                    with archive.open(item) as stream:
                        content = stream.read(MAX_BYTES + 1)
                    if len(content) > MAX_BYTES:
                        raise InvoiceError("ARCHIVE_TOO_LARGE")
                    yield item.filename, content
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise InvoiceError("INVALID_ARCHIVE") from None


def pdf_worker(path):
    logging.disable(logging.CRITICAL)
    try:
        if os.name != "nt":
            import resource
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
            resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        from pypdf import PdfReader
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            return {"error": "ENCRYPTED_PDF"}
        if not 1 <= len(reader.pages) <= 100:
            return {"error": "PDF_PAGE_LIMIT"}
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
            if len(text) > 500000:
                return {"error": "PDF_TEXT_LIMIT"}
        text = unicodedata.normalize("NFKC", text)
        compact = re.sub(r"\s+", "", text)
        number = re.search(r"(?:发票号码|InvoiceNo\.?)[：:]?(\d{8,20})(?!\d)", compact, re.I)
        code = re.search(r"发票代码[：:]?(\d{10,12})(?!\d)", compact)
        return {"pages": len(reader.pages), "invoice_number": number[1] if number else None,
                "invoice_code": code[1] if code else None,
                "invoice_like": bool(re.search(r"发票|invoice", text, re.I)),
                "text_preview": text[:12000]}
    except ImportError:
        return {"error": "INVOICE_DEPENDENCY_REQUIRED"}
    except Exception:
        return {"error": "INVALID_OR_UNREADABLE_PDF"}


def pdf_info(path):
    home = Path(os.environ.get("AGENTQR_INVOICE_HOME", Path.home() / ".agentqr-invoices"))
    python = home / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        result = subprocess.run([str(python) if python.exists() else sys.executable,
                                 str(Path(__file__).resolve()), str(path)],
                                capture_output=True, timeout=25, env=clean_env())
        if result.returncode or len(result.stdout) > 100000:
            raise InvoiceError("PDF_INSPECTION_FAILED")
        info = json.loads(result.stdout)
        if "error" in info:
            raise InvoiceError(info["error"])
        return info
    except subprocess.TimeoutExpired:
        raise InvoiceError("PDF_INSPECTION_TIMEOUT") from None


if __name__ == "__main__":
    print(json.dumps(pdf_worker(sys.argv[1]), ensure_ascii=True))
