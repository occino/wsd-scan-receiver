"""HTTP receiver for WSD/DPWS SOAP requests and pushed scan payloads."""

from __future__ import annotations

import logging
import re
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BufferedIOBase
from pathlib import Path
from socketserver import TCPServer
from typing import Any
from xml.etree.ElementTree import ParseError

from .config import Config
from .soap import device_metadata_xml, parse_soap_envelope, route_soap_request, soap_envelope

LOGGER = logging.getLogger(__name__)


class ThreadingHTTPServerNoFqdn(ThreadingHTTPServer):
    """HTTP server variant that avoids reverse-DNS during bind."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class ThreadingHTTPServerV6(ThreadingHTTPServerNoFqdn):
    """HTTP server variant that binds an IPv6 socket only."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def timestamped_name(prefix: str, suffix: str) -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{prefix}-{now}-{uuid.uuid4().hex[:8]}{suffix}"


def guess_extension(content_type: str, payload: bytes) -> str:
    """Guess a useful file extension for a received scan payload."""
    lower = content_type.lower()
    if "pdf" in lower or payload.startswith(b"%PDF"):
        return ".pdf"
    if "jpeg" in lower or payload.startswith(b"\xff\xd8"):
        return ".jpg"
    if "png" in lower or payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if "tiff" in lower or payload[:4] in {b"II*\x00", b"MM\x00*"}:
        return ".tif"
    if "xml" in lower or b"<s:Envelope" in payload[:512] or b":Envelope" in payload[:512]:
        return ".xml"
    return ".bin"


def is_probably_soap(content_type: str, payload: bytes) -> bool:
    lower = content_type.lower()
    return (
        "soap" in lower
        or "xml" in lower
        or re.search(br"<[^>]*Envelope\b", payload[:1024]) is not None
    )


def write_payload(directory: Path, prefix: str, suffix: str, payload: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / timestamped_name(prefix, suffix)
    path.write_bytes(payload)
    return path


def read_chunked_body(stream: BufferedIOBase) -> bytes:
    """Read an HTTP/1.1 chunked request body from a handler rfile stream."""
    chunks: list[bytes] = []
    while True:
        size_line = stream.readline()
        if not size_line:
            raise ValueError("unexpected EOF while reading chunk size")
        size_text = size_line.split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as exc:
            raise ValueError(f"invalid chunk size {size_text!r}") from exc
        if size == 0:
            while True:
                trailer = stream.readline()
                if trailer in {b"", b"\r\n", b"\n"}:
                    break
            return b"".join(chunks)
        chunks.append(stream.read(size))
        line_end = stream.read(2)
        if line_end != b"\r\n":
            raise ValueError("invalid chunk terminator")


class WsdRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler factory-bound to a Config instance by make_handler."""

    server_version = "WsdScanReceiver/0.1"
    config: Config
    event_observer: Callable[[bytes], None] | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("http request", extra={"peer": self.client_address[0], "line": fmt % args})

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/metadata", "/device", "/scanner"}:
            body = soap_envelope(
                "http://schemas.xmlsoap.org/ws/2004/09/transfer/GetResponse",
                device_metadata_xml(self.config),
            )
            self._send(HTTPStatus.OK, body, "application/soap+xml; charset=utf-8")
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        content_type = self.headers.get("Content-Type", "application/octet-stream")
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        try:
            if "chunked" in transfer_encoding.lower():
                payload = read_chunked_body(self.rfile)
            else:
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(content_length)
        except (OSError, ValueError) as exc:
            LOGGER.warning("failed to read HTTP POST body", extra={"error": str(exc)})
            self._send(HTTPStatus.BAD_REQUEST, b"invalid HTTP request body\n", "text/plain")
            return

        if self.config.debug:
            dump_path = write_payload(
                self.config.raw_dump_dir,
                "http-post",
                guess_extension(content_type, payload),
                payload,
            )
            LOGGER.debug(
                "stored raw incoming POST",
                extra={
                    "path": str(dump_path),
                    "content_type": content_type,
                    "transfer_encoding": transfer_encoding,
                    "content_length": self.headers.get("Content-Length"),
                    "bytes": len(payload),
                },
            )
            LOGGER.debug(
                "incoming SOAP or payload body",
                extra={"payload": payload.decode("utf-8", "replace")},
            )

        if is_probably_soap(content_type, payload):
            self._handle_soap(payload)
            return

        suffix = guess_extension(content_type, payload)
        out_path = write_payload(self.config.output_dir, "scan", suffix, payload)
        LOGGER.info(
            "stored scan payload",
            extra={"path": str(out_path), "content_type": content_type, "bytes": len(payload)},
        )
        self._send(HTTPStatus.ACCEPTED, b"stored\n", "text/plain; charset=utf-8")

    def _handle_soap(self, payload: bytes) -> None:
        try:
            request = parse_soap_envelope(payload)
        except ParseError:
            LOGGER.warning("invalid SOAP/XML request")
            if self.config.debug:
                path = write_payload(self.config.raw_dump_dir, "invalid-soap", ".xml", payload)
                LOGGER.debug("stored invalid SOAP", extra={"path": str(path)})
            self._send(HTTPStatus.BAD_REQUEST, b"invalid SOAP/XML\n", "text/plain; charset=utf-8")
            return

        status, body, content_type = route_soap_request(request, self.config)
        if (
            request.action
            and "ScanAvailableEvent" in request.action
            and self.event_observer is not None
        ):
            try:
                self.event_observer(payload)
            except Exception:
                LOGGER.exception("ScanAvailableEvent observer failed")
        self._send(HTTPStatus(status), body, content_type)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_handler(
    config: Config,
    event_observer: Callable[[bytes], None] | None = None,
) -> type[WsdRequestHandler]:
    """Create a request handler class bound to a runtime config."""

    class ConfiguredWsdRequestHandler(WsdRequestHandler):
        pass

    ConfiguredWsdRequestHandler.config = config
    if event_observer is not None:
        ConfiguredWsdRequestHandler.event_observer = staticmethod(event_observer)
    return ConfiguredWsdRequestHandler


class ReceiverService:
    """Threaded HTTP server for WSD metadata, SOAP, and payload POSTs."""

    def __init__(
        self,
        config: Config,
        event_observer: Callable[[bytes], None] | None = None,
    ) -> None:
        self.config = config
        handler = make_handler(config, event_observer)
        self.servers: list[ThreadingHTTPServer] = [
            ThreadingHTTPServerNoFqdn(("", config.http_port), handler)
        ]
        try:
            self.servers.append(ThreadingHTTPServerV6(("::", config.http_port), handler))
        except OSError:
            LOGGER.warning("IPv6 HTTP receiver unavailable", exc_info=True)
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for index, server in enumerate(self.servers):
            family = "ipv6" if server.address_family == socket.AF_INET6 else "ipv4"
            thread = threading.Thread(
                target=server.serve_forever,
                name=f"wsd-http-{family}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)
            LOGGER.info(
                "HTTP receiver started",
                extra={"tcp_port": self.config.http_port, "family": family, "index": index},
            )

    def stop(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(timeout=2)
