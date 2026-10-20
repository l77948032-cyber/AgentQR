"""Bounded HTTPS GETs to explicitly approved public hosts, without mail credentials."""

import http.client
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from invoice_common import InvoiceError, MAX_BYTES, tls_context


def url_host(url):
    try:
        return (urlsplit(url).hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return ""


def validate_url(url, allowed):
    try:
        parts = urlsplit(url)
        host = url_host(url)
        if (parts.scheme != "https" or not host or parts.username or parts.password
                or parts.port not in (443, None) or any(ord(c) < 33 for c in url)
                or "\\" in url):
            raise InvoiceError("UNSAFE_URL")
        if host not in allowed:
            raise InvoiceError("DOMAIN_APPROVAL_REQUIRED")
        return host, urlunsplit(("", "", parts.path or "/", parts.query, ""))
    except ValueError:
        raise InvoiceError("UNSAFE_URL") from None


def public_addresses(host):
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise InvoiceError("NON_PUBLIC_ADDRESS")
        return addresses
    except socket.gaierror:
        raise InvoiceError("DNS_FAILED") from None


def get(url, allowed):
    current = url
    started = time.monotonic()
    for _ in range(6):
        if time.monotonic() - started > 60:
            raise InvoiceError("DOWNLOAD_TIMEOUT")
        host, path = validate_url(current, allowed)
        addresses = public_addresses(host)
        connection = http.client.HTTPSConnection(host, timeout=20, context=tls_context())
        # Pin the checked IP while keeping the original host for TLS verification/SNI.
        connection._create_connection = lambda *args, **kwargs: socket.create_connection((addresses[0], 443), timeout=20)
        try:
            connection.request("GET", path, headers={"User-Agent": "AgentQR-Invoices/0.1",
                                                    "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise InvoiceError("INVALID_REDIRECT")
                current = urljoin(current, location)
                # Validate each hop before sending any token or making a connection.
                validate_url(current, allowed)
                continue
            if response.status in (401, 403):
                raise InvoiceError("BROWSER_AUTH_REQUIRED")
            if response.status != 200:
                raise InvoiceError("HTTP_DOWNLOAD_FAILED")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise InvoiceError("UNSUPPORTED_CONTENT_ENCODING")
            length = response.getheader("Content-Length")
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise InvoiceError("DOWNLOAD_TOO_LARGE")
            chunks, size = [], 0
            while True:
                block = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not block:
                    break
                size += len(block)
                if size > MAX_BYTES:
                    raise InvoiceError("DOWNLOAD_TOO_LARGE")
                if time.monotonic() - started > 60:
                    raise InvoiceError("DOWNLOAD_TIMEOUT")
                chunks.append(block)
            return b"".join(chunks), response.getheader("Content-Type", ""), current
        except (OSError, http.client.HTTPException):
            raise InvoiceError("HTTPS_DOWNLOAD_FAILED") from None
        finally:
            connection.close()
    raise InvoiceError("TOO_MANY_REDIRECTS")
