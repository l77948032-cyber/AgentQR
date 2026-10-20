"""Private local storage and bounded input shared by the invoice workflow."""

import hashlib
import json
import os
from pathlib import Path
import stat
import ssl
import sys
import tempfile

MAX_BYTES = 25 * 1024 * 1024


class InvoiceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("QQMAIL_", "AGENTQR_QQ_"))}


def tls_context():
    context = ssl.create_default_context()
    # Some macOS Python distributions have no configured CA bundle. Add the
    # system's public roots; certificate/hostname verification remains enabled.
    if sys.platform == "darwin" and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations("/etc/ssl/cert.pem")
    return context


def private_dir(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise InvoiceError("UNSAFE_LOCAL_PATH")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def private_write(path, data):
    path = Path(path)
    if path.is_symlink():
        raise InvoiceError("UNSAFE_LOCAL_PATH")
    fd, temp = tempfile.mkstemp(prefix=".agentqr-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def bounded_read(path, limit=MAX_BYTES):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InvoiceError("FILE_TOO_LARGE")
    return data


def read_private_json(path):
    path = Path(path)
    if path.is_symlink():
        raise InvoiceError("UNSAFE_LOCAL_PATH")
    if os.name != "nt":
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise InvoiceError("CREDENTIAL_FILE_NOT_PRIVATE")
    return json.loads(bounded_read(path, 16384))
