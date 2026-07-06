"""Experimental Epson push-scan debug helpers.

Some Epson devices use vendor-specific push-scan discovery in addition to, or
instead of, WS-Discovery. These helpers make traffic on the documented Epson
push-scan ports visible and emulate the scanner status polling observed from
Epson's macOS software. The protocol is still incomplete and capture-driven.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from collections.abc import Callable

from .config import Config

LOGGER = logging.getLogger(__name__)
EPSON_PUSH_PORT = 2968
EPSON_SCAN_PORT = 1865
EPSON_DISCOVERY_PORT = 3289
EPSON_DISCOVERY_BROADCAST_QUERY = bytes.fromhex("4550534f4e5000ff000000000000")
EPSON_DISCOVERY_UNICAST_QUERY = bytes.fromhex("4550534f4e510400000000000000")
EPSON_DISCOVERY_ACK = EPSON_DISCOVERY_UNICAST_QUERY
EPSON_SESSION_START = bytes.fromhex("49532100000c00000007000001a0040000012c")
EPSON_SESSION_POLL = bytes.fromhex("49532000000c0000000a000000000002000000011c59")
EPSON_RESPONSE_RE = re.compile(rb"([A-Z ]{4})x([0-9A-Fa-f]{7})")
EPSON_TCP_COMMANDS = ("STAT", "INFO", "CAPA", "RESA", "FIN ")


def make_epson_command_frame(command: str) -> bytes:
    """Build an Epson IS command frame captured from Epson Scan 2/macOS.

    The frame format is vendor-specific. The 20-byte prefix and 12-byte ASCII
    command payload match the ET-2750 traffic captures used for this receiver.
    """
    if len(command) != 4:
        raise ValueError("Epson IS commands must be exactly four characters")
    payload = f"{command}x0000000".encode("ascii")
    return (
        b"IS\x20\x00\x00\x0c"
        + (20).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + (len(payload)).to_bytes(2, "big")
        + (64).to_bytes(4, "big")
        + payload
    )


def make_epson_read_frame(length: int) -> bytes:
    """Build an Epson IS continuation-read frame for a response body."""
    if length < 0 or length > 0xFFFF:
        raise ValueError("Epson IS read length must fit into 16 bits")
    return (
        b"IS\x20\x00\x00\x0c"
        + (8).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + length.to_bytes(4, "big")
    )


class EpsonDebugService:
    """Log UDP/TCP traffic on Epson push-scan related ports."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []

    def start(self) -> None:
        for name, target in (
            ("epson-udp-3289", self._serve_udp_3289),
            ("epson-udp-2968", self._serve_udp_2968),
            ("epson-tcp-2968", lambda: self._serve_tcp(EPSON_PUSH_PORT)),
            ("epson-tcp-1865", lambda: self._serve_tcp(EPSON_SCAN_PORT)),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        if self.config.epson_printer_ip:
            thread = threading.Thread(
                target=self._poll_printer_discovery,
                name="epson-udp-3289-poll",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for sock in self._sockets:
            sock.close()
        for thread in self._threads:
            thread.join(timeout=2)

    def _track_socket(self, sock: socket.socket) -> socket.socket:
        self._sockets.append(sock)
        return sock

    def _serve_udp_3289(self) -> None:
        self._serve_udp(
            EPSON_DISCOVERY_PORT,
            "Epson UDP scanner discovery debug listener started",
            self._handle_udp_3289,
        )

    def _serve_udp_2968(self) -> None:
        self._serve_udp(
            EPSON_PUSH_PORT,
            "Epson UDP push-scan debug listener started",
            self._handle_udp_2968,
        )

    def _serve_udp(
        self,
        port: int,
        start_message: str,
        handler: Callable[[socket.socket, bytes, tuple[str, int]], None],
    ) -> None:
        sock = self._track_socket(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(1.0)
        LOGGER.info(start_message, extra={"udp_port": port})

        while not self._stop.is_set():
            try:
                payload, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOGGER.exception("Epson UDP listener error", extra={"udp_port": port})
                return

            handler(sock, payload, addr)

    def _handle_udp_3289(
        self,
        sock: socket.socket,
        payload: bytes,
        addr: tuple[str, int],
    ) -> None:
        LOGGER.info(
            "received Epson UDP scanner discovery datagram",
            extra={
                "peer": f"{addr[0]}:{addr[1]}",
                "bytes": len(payload),
                "hex": payload.hex(),
                "text": payload.decode("utf-8", "replace"),
            },
        )
        if payload.startswith(b"EPSONp"):
            try:
                sock.sendto(EPSON_DISCOVERY_ACK, addr)
                LOGGER.info(
                    "sent Epson UDP scanner discovery ACK",
                    extra={"peer": f"{addr[0]}:{addr[1]}"},
                )
            except OSError:
                LOGGER.exception("failed sending Epson UDP scanner discovery ACK")

    def _handle_udp_2968(
        self,
        _sock: socket.socket,
        payload: bytes,
        addr: tuple[str, int],
    ) -> None:
        LOGGER.info(
            "received Epson UDP push-scan datagram",
            extra={
                "peer": f"{addr[0]}:{addr[1]}",
                "bytes": len(payload),
                "hex": payload.hex(),
                "text": payload.decode("utf-8", "replace"),
            },
        )

    def _serve_tcp(self, port: int) -> None:
        sock = self._track_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.listen(5)
        sock.settimeout(1.0)
        LOGGER.info("Epson TCP push-scan debug listener started", extra={"tcp_port": port})

        while not self._stop.is_set():
            try:
                conn, addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOGGER.exception("Epson TCP push-scan listener error")
                return

            thread = threading.Thread(
                target=self._handle_tcp_client,
                args=(conn, addr, port),
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _handle_tcp_client(
        self,
        conn: socket.socket,
        addr: tuple[str, int],
        port: int,
    ) -> None:
        with conn:
            conn.settimeout(3.0)
            try:
                payload = conn.recv(65535)
            except OSError:
                LOGGER.exception("failed reading Epson TCP push-scan connection")
                return

            LOGGER.info(
                "received Epson TCP push-scan connection",
                extra={
                    "peer": f"{addr[0]}:{addr[1]}",
                    "local_port": port,
                    "bytes": len(payload),
                    "hex": payload.hex(),
                    "text": payload.decode("utf-8", "replace"),
                },
            )

    def _poll_printer_discovery(self) -> None:
        LOGGER.info(
            "Epson UDP scanner discovery polling enabled",
            extra={
                "printer_ip": self.config.epson_printer_ip,
                "udp_port": EPSON_DISCOVERY_PORT,
            },
        )
        while not self._stop.is_set():
            self._send_discovery_poll(
                EPSON_DISCOVERY_BROADCAST_QUERY,
                ("255.255.255.255", EPSON_DISCOVERY_PORT),
                "broadcast",
            )
            self._send_discovery_poll(
                EPSON_DISCOVERY_UNICAST_QUERY,
                (self.config.epson_printer_ip or "", EPSON_DISCOVERY_PORT),
                "unicast",
            )
            self._probe_printer_scan_protocol()
            time.sleep(30)

    def _send_discovery_poll(
        self,
        query: bytes,
        destination: tuple[str, int],
        mode: str,
    ) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(2.0)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", 0))
            sock.sendto(query, destination)
            try:
                payload, addr = sock.recvfrom(65535)
            except TimeoutError:
                LOGGER.debug(
                    "Epson UDP scanner discovery poll timed out",
                    extra={"mode": mode},
                )
            else:
                LOGGER.info(
                    "received Epson UDP scanner discovery poll response",
                    extra={
                        "mode": mode,
                        "peer": f"{addr[0]}:{addr[1]}",
                        "bytes": len(payload),
                        "hex": payload.hex(),
                        "text": payload.decode("utf-8", "replace"),
                    },
                )
                if payload.startswith(b"EPSONp"):
                    sock.sendto(EPSON_DISCOVERY_ACK, addr)
                    LOGGER.info(
                        "sent Epson UDP scanner discovery poll ACK",
                        extra={"mode": mode, "peer": f"{addr[0]}:{addr[1]}"},
                    )
        except OSError:
            LOGGER.exception(
                "Epson UDP scanner discovery polling error",
                extra={"mode": mode, "destination": f"{destination[0]}:{destination[1]}"},
            )
        finally:
            sock.close()

    def _probe_printer_scan_protocol(self) -> None:
        """Poll the Epson scanner TCP protocol observed on port 1865.

        In the available macOS captures the computer actively connects to the
        printer on TCP 1865 and requests scanner status/capabilities. Replaying
        this sequence may be necessary before some devices expose a computer in
        their front-panel scan menu. It is intentionally logged as experimental.
        """
        printer_ip = self.config.epson_printer_ip
        if not printer_ip:
            return

        try:
            with socket.create_connection((printer_ip, EPSON_SCAN_PORT), timeout=5.0) as sock:
                sock.settimeout(2.0)
                LOGGER.info(
                    "connected to Epson scanner TCP protocol",
                    extra={"peer": f"{printer_ip}:{EPSON_SCAN_PORT}"},
                )
                self._recv_and_log_epson_tcp(sock, "greeting")
                sock.sendall(EPSON_SESSION_START)
                self._recv_and_log_epson_tcp(sock, "session-start")
                sock.sendall(EPSON_SESSION_POLL)
                self._recv_and_log_epson_tcp(sock, "session-poll")
                for command in EPSON_TCP_COMMANDS:
                    self._send_epson_tcp_command(sock, command)
        except OSError:
            LOGGER.exception(
                "Epson scanner TCP protocol probe failed",
                extra={"peer": f"{printer_ip}:{EPSON_SCAN_PORT}"},
            )

    def _send_epson_tcp_command(self, sock: socket.socket, command: str) -> None:
        sock.sendall(make_epson_command_frame(command))
        payload = self._recv_and_log_epson_tcp(sock, f"command-{command.strip() or command}")
        match = EPSON_RESPONSE_RE.search(payload)
        if not match:
            return

        remaining_length = int(match.group(2), 16)
        if remaining_length <= 0:
            return

        sock.sendall(make_epson_read_frame(remaining_length))
        self._recv_and_log_epson_tcp(
            sock,
            f"command-{command.strip() or command}-continuation",
        )

    def _recv_and_log_epson_tcp(self, sock: socket.socket, stage: str) -> bytes:
        chunks: list[bytes] = []
        deadline = time.monotonic() + 1.5

        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                break
            except OSError:
                LOGGER.exception(
                    "failed reading Epson scanner TCP protocol",
                    extra={"stage": stage},
                )
                break
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 4096:
                sock.settimeout(0.15)

        payload = b"".join(chunks)
        LOGGER.info(
            "received Epson scanner TCP protocol data",
            extra={
                "stage": stage,
                "bytes": len(payload),
                "hex": payload.hex(),
                "text": payload.decode("utf-8", "replace"),
            },
        )
        sock.settimeout(2.0)
        return payload


def make_optional_service(config: Config) -> Callable[[], EpsonDebugService | None]:
    """Return a factory so main can keep startup readable."""

    def create() -> EpsonDebugService | None:
        if not config.epson_debug_enabled:
            return None
        return EpsonDebugService(config)

    return create
