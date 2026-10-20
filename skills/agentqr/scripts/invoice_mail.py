"""QQ IMAP read-only input. No SMTP, cookie extraction, or cloud credential store."""

import datetime as dt
from email import policy
from email.parser import BytesParser
import getpass
import imaplib
import json
import os
from pathlib import Path
import re
import sys

from invoice_common import InvoiceError, MAX_BYTES, private_dir, private_write, read_private_json, tls_context

HOST = "imap.qq.com"


def credential_path():
    return Path(os.environ.get("AGENTQR_INVOICE_HOME", Path.home() / ".agentqr-invoices")) / "qqmail.json"


def validate_credentials(account, code):
    if not isinstance(account, str) or not re.fullmatch(r"[^@\s]+@(qq|foxmail)\.com", account, re.I):
        raise InvoiceError("INVALID_QQ_ACCOUNT")
    if not isinstance(code, str) or not re.fullmatch(r"[a-zA-Z]{16}", code):
        raise InvoiceError("EXPECTED_QQ_AUTHORIZATION_CODE")
    return account, code


def credentials():
    account, code = os.environ.get("QQMAIL_ACCOUNT"), os.environ.get("QQMAIL_AUTH_CODE")
    if account is not None or code is not None:
        return validate_credentials(account, code)
    try:
        stored = read_private_json(credential_path())
        return validate_credentials(stored.get("account"), stored.get("authorization_code"))
    except FileNotFoundError:
        raise InvoiceError("QQMAIL_SETUP_REQUIRED") from None


def configure():
    if not sys.stdin.isatty():
        raise InvoiceError("USER_TERMINAL_REQUIRED")
    # Secret entry stays in the user's own terminal, never in a chat/tool argument.
    print("QQ 邮箱授权码设置（不是邮箱登录密码）")
    print("请在 QQ 邮箱官方设置中开启 IMAP，并生成授权码。")
    print("授权码仅保存在本机，用于向 imap.qq.com 认证；不会发送给模型、AgentQR 服务或发票网站。")
    print("本地配置不是加密保险箱；拥有当前系统账号或管理员权限的程序可能读取它。")
    print("请不要把授权码发到聊天、共享目录、代码仓库或云同步目录。")
    account = input("QQ/Foxmail 邮箱地址: ").strip()
    code = getpass.getpass("授权码（输入隐藏）: ").strip()
    validate_credentials(account, code)
    path = credential_path()
    private_dir(path.parent)
    if os.name == "nt":
        # chmod does not implement Windows ACLs. Restrict this directory explicitly.
        import subprocess
        who = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
        result = subprocess.run(["icacls", str(path.parent), "/inheritance:r", "/grant:r",
                                 f"{who}:(OI)(CI)F", "/T"], capture_output=True)
        if result.returncode:
            raise InvoiceError("WINDOWS_PRIVATE_ACL_FAILED")
    private_write(path, json.dumps({"account": account, "authorization_code": code}).encode())
    return {"status": "configured", "storage": "local_private_file", "configuration_file": str(path),
            "purpose": "QQ official IMAP authentication only"}


def date_range(since, before):
    try:
        start, end = dt.date.fromisoformat(since), dt.date.fromisoformat(before)
        if start >= end:
            raise ValueError()
        return start, end
    except (ValueError, TypeError):
        raise InvoiceError("INVALID_DATE_RANGE") from None


def imap_date(date):
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{date.day:02}-{months[date.month - 1]}-{date.year}"


def parse_message(raw):
    if len(raw) > MAX_BYTES:
        raise InvoiceError("MESSAGE_TOO_LARGE")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text, html, files = [], [], []
    for part in message.walk():
        if part.is_multipart():
            continue
        data = part.get_payload(decode=True) or b""
        if len(data) > MAX_BYTES:
            raise InvoiceError("ATTACHMENT_TOO_LARGE")
        mime = part.get_content_type()
        if mime in ("text/plain", "text/html") and part.get_content_disposition() != "attachment":
            try:
                value = data.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                value = data.decode("utf-8", errors="replace")
            (html if mime == "text/html" else text).append(value)
        elif data:
            files.append({"name": part.get_filename() or "", "mime": mime, "data": data,
                          "cid": str(part.get("Content-ID", "")).strip("<>")})
    return {"subject": str(message.get("Subject", "")), "text": "\n".join(text),
            "html": "\n".join(html), "files": files}


def collect_imap(since, before, folder, consume):
    start, end = date_range(since, before)
    if not re.fullmatch(r"[A-Za-z0-9 _./-]{1,100}", folder):
        raise InvoiceError("INVALID_FOLDER")
    account, code = credentials()
    client = None
    errors, total = [], 0
    try:
        client = imaplib.IMAP4_SSL(HOST, 993, ssl_context=tls_context(), timeout=30)
        client.login(account, code)
        code = None
        if client.select('"' + folder + '"', readonly=True)[0] != "OK":
            raise InvoiceError("MAILBOX_SELECT_FAILED")
        validity = client.response("UIDVALIDITY")[1]
        if not validity or not validity[0] or not validity[0].isdigit():
            raise InvoiceError("MISSING_UIDVALIDITY")
        status, found = client.uid("search", None, "SINCE", imap_date(start), "BEFORE", imap_date(end))
        if status != "OK":
            raise InvoiceError("MAIL_SEARCH_FAILED")
        for uid in (found[0] or b"").split():
            total += 1
            ref = f"{account.lower()}|{folder}|{validity[0].decode()}|{uid.decode()}"
            try:
                status, sizes = client.uid("fetch", uid, "(RFC822.SIZE)")
                size = re.search(rb"RFC822\.SIZE (\d+)", b" ".join(x for x in sizes if isinstance(x, bytes)))
                if status != "OK" or not size:
                    raise InvoiceError("MAIL_SIZE_FAILED")
                if int(size[1]) > MAX_BYTES:
                    raise InvoiceError("MESSAGE_TOO_LARGE")
                status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
                bodies = [p[1] for p in parts if isinstance(p, tuple) and isinstance(p[1], bytes)]
                if status != "OK" or len(bodies) != 1:
                    raise InvoiceError("MAIL_READ_FAILED")
                consume(ref, bodies[0])
            except InvoiceError as error:
                errors.append({"uid": uid.decode(), "error": error.code})
            except (ValueError, TypeError, UnicodeError):
                errors.append({"uid": uid.decode(), "error": "MESSAGE_PARSE_FAILED"})
        return {"messages_in_range": total, "read_errors": errors, "folder": folder,
                "since": since, "before": before}
    except (imaplib.IMAP4.error, OSError):
        raise InvoiceError("QQMAIL_CONNECTION_OR_AUTH_FAILED") from None
    finally:
        if client:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
